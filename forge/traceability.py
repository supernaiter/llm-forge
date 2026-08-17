"""Traceability matrix validation for the V3 research contract."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .protocol import ProtocolError, sha256_file, strict_json_loads


ROOT = Path(__file__).resolve().parents[1]
TRACEABILITY_PATH = ROOT / "protocol" / "traceability_v3.json"
ALLOWED_STATUSES = frozenset({
    "implemented",
    "implemented_opt_in",
    "partial",
    "external_blocked",
})


class TraceabilityError(ProtocolError):
    """Raised when a traceability matrix cannot be audited."""


def load_traceability(path: str | Path = TRACEABILITY_PATH) -> dict[str, Any]:
    target = Path(path)
    try:
        value = strict_json_loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise TraceabilityError(f"cannot load traceability matrix: {target}") from exc
    validate_traceability(value)
    return value


def validate_traceability(
    matrix: Mapping[str, Any],
    *,
    protocol_path: str | Path | None = None,
    root: str | Path = ROOT,
) -> None:
    if matrix.get("matrix_id") != "FORGE_TRACEABILITY_V3":
        raise TraceabilityError("unsupported traceability matrix")
    source_document = matrix.get("source_document")
    if source_document != "RESEARCH_V3_TERMINATION_CRITERION.md":
        raise TraceabilityError("traceability source document is not canonical")
    entries = matrix.get("entries")
    if not isinstance(entries, list) or not entries:
        raise TraceabilityError("traceability matrix has no entries")
    ids = [entry.get("requirement_id") for entry in entries if isinstance(entry, Mapping)]
    if len(ids) != len(entries) or len(ids) != len(set(ids)) or any(not item for item in ids):
        raise TraceabilityError("traceability requirement IDs must be unique")

    project_root = Path(root)
    source_path = project_root / source_document
    if not source_path.is_file():
        raise TraceabilityError(f"traceability source document is missing: {source_document}")

    protocol_file = Path(protocol_path) if protocol_path else project_root / "protocol" / "forge_research_v3.json"
    try:
        protocol = strict_json_loads(protocol_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise TraceabilityError("cannot load protocol for traceability comparison") from exc
    required_ids = {item.get("id") for item in protocol.get("requirements", [])}
    if set(ids) != required_ids:
        missing = sorted(required_ids - set(ids))
        extra = sorted(set(ids) - required_ids)
        raise TraceabilityError(f"requirement ID coverage mismatch; missing={missing}, extra={extra}")

    for entry in entries:
        if not isinstance(entry, Mapping):
            raise TraceabilityError("traceability entry is not an object")
        if entry.get("status") not in ALLOWED_STATUSES:
            raise TraceabilityError(f"invalid traceability status: {entry.get('status')}")
        for key in ("source", "implementation_evidence", "verification_evidence", "blockers"):
            value = entry.get(key)
            if key == "source":
                if not isinstance(value, str) or not value:
                    raise TraceabilityError(f"entry {entry.get('requirement_id')} has no source")
            elif not isinstance(value, list):
                raise TraceabilityError(
                    f"entry {entry.get('requirement_id')} field {key} must be a list"
                )
        for key in ("implementation_evidence", "verification_evidence"):
            for path_value in entry[key]:
                # Evidence paths are repository-relative and must be resolvable;
                # external blockers belong in blockers, not fabricated paths.
                if not isinstance(path_value, str) or not path_value:
                    raise TraceabilityError(
                        f"entry {entry.get('requirement_id')} has invalid {key} path"
                    )
                if not (project_root / path_value).exists():
                    raise TraceabilityError(
                        f"entry {entry.get('requirement_id')} evidence is missing: {path_value}"
                    )


def traceability_sha256(path: str | Path = TRACEABILITY_PATH) -> str:
    return sha256_file(path)
