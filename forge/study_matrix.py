"""Registered-run matrix schema for the V3 study bundle.

The event ledger/result pair represents one auditable run.  A scientific
termination decision additionally needs an immutable matrix describing every
registered seed/run identity.  This module validates that matrix without
opening hidden data or executing a candidate.
"""
from __future__ import annotations

import re
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .protocol import ProtocolError, canonical_json, sha256_bytes, sha256_file


MATRIX_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")
_DISTRIBUTIONS = frozenset({"iid_heldout", "size_shift", "distribution_shift"})
_TIERS = frozenset({"SMALL", "MEDIUM", "STRONG"})
_TRACKS = frozenset({"SAME_MODEL", "NATIVE_COMPUTE"})
_PRIMARY_PHASE_STATUSES = frozenset({"positive", "negative", "extend"})


def matrix_sha256(matrix: Mapping[str, Any]) -> str:
    """Hash matrix bytes excluding its self-hash field."""
    payload = {key: value for key, value in matrix.items() if key != "matrix_sha256"}
    return sha256_bytes(canonical_json(payload))


def _id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or _ID_RE.fullmatch(value) is None:
        raise ProtocolError(f"run matrix {field} must be a stable identifier")
    if value.strip().lower() in {"latest", "default", "floating", "unresolved", "draft"}:
        raise ProtocolError(f"run matrix {field} is unresolved")
    return value


def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ProtocolError(f"run matrix {field} must be a lowercase sha256 digest")
    return value


def _artifact_relpath(value: Any, field: str) -> str:
    """Validate a bundle-relative artifact path before it is joined to disk.

    A frozen matrix must not be able to escape its bundle through an absolute
    path, ``..`` component, platform-specific separator, or symlink target.
    The filesystem containment check is performed by the caller once the
    bundle root is known; this helper enforces the portable lexical part.
    """
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(f"run matrix {field} must be a relative artifact path")
    raw = value.strip()
    path = Path(raw)
    if path.is_absolute() or "\\" in raw:
        raise ProtocolError(f"run matrix {field} must be bundle-relative")
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ProtocolError(f"run matrix {field} contains an unsafe path component")
    if not path.name or path.name in {".", ".."}:
        raise ProtocolError(f"run matrix {field} is not a file path")
    return raw


def validate_run_matrix(
    matrix: Mapping[str, Any],
    *,
    protocol_spec: Mapping[str, Any],
    task_manifest: Mapping[str, Any],
    current_result: Mapping[str, Any] | None = None,
    current_events_sha256: str | None = None,
    current_result_sha256: str | None = None,
    artifact_root: str | Path | None = None,
) -> dict[str, Any]:
    """Validate a frozen matrix and return derived coverage information."""
    if not isinstance(matrix, Mapping):
        raise ProtocolError("run_matrix.json must be an object")
    if matrix.get("schema_version") != MATRIX_SCHEMA_VERSION:
        raise ProtocolError("unsupported run matrix schema version")
    if matrix.get("status") != "frozen" or matrix.get("frozen") is not True:
        raise ProtocolError("run matrix is not frozen")
    _id(matrix.get("manifest_id"), "manifest_id")
    _id(matrix.get("study_id"), "study_id")
    study_version = _sha(matrix.get("study_version"), "study_version")
    _sha(matrix.get("authority_attestation_sha256"), "authority_attestation_sha256")
    primary_phase_status = matrix.get("primary_verifier_status")
    if primary_phase_status not in _PRIMARY_PHASE_STATUSES:
        raise ProtocolError("run matrix primary_verifier_status is invalid or missing")
    unsigned_hash = matrix.get("matrix_sha256")
    _sha(unsigned_hash, "matrix_sha256")
    if unsigned_hash != matrix_sha256(matrix):
        raise ProtocolError("run matrix self-hash mismatch")

    seeds = protocol_spec.get("seeds")
    if not isinstance(seeds, Mapping):
        raise ProtocolError("protocol seed sets are missing")
    primary = seeds.get("primary")
    extension = seeds.get("extension")
    if not isinstance(primary, list) or not isinstance(extension, list):
        raise ProtocolError("protocol seed sets are malformed")
    matrix_primary = matrix.get("primary_seed_ids")
    matrix_extension = matrix.get("extension_seed_ids")
    if matrix_primary != primary:
        raise ProtocolError("run matrix primary seed IDs differ from protocol")
    if matrix_extension not in ([], extension):
        raise ProtocolError("run matrix extension seed IDs differ from protocol")
    extension_authorized = matrix.get("extension_authorized")
    if not isinstance(extension_authorized, bool):
        raise ProtocolError("run matrix extension_authorized must be boolean")
    if bool(matrix_extension) != extension_authorized:
        raise ProtocolError("run matrix extension authorization does not match IDs")
    if extension_authorized != (primary_phase_status == "extend"):
        raise ProtocolError(
            "run matrix extension authorization does not match primary verifier status"
        )

    rows = matrix.get("runs")
    if not isinstance(rows, list) or not rows:
        raise ProtocolError("run matrix runs are missing")
    holdout = task_manifest.get("holdout_problems")
    if not isinstance(holdout, list):
        raise ProtocolError("task manifest holdout problems are missing")
    task_by_id = {row.get("problem_id"): row for row in holdout if isinstance(row, Mapping)}
    seen_run_ids: set[str] = set()
    seen_artifact_paths: set[str] = set()
    # Lexically distinct paths are not sufficient: symlinks and hardlinks can
    # otherwise make several registered rows reuse one materialized artifact.
    seen_artifact_targets: dict[tuple[int, int], str] = {}
    seen_resolved_paths: dict[Path, str] = {}
    seen_primary: set[int] = set()
    seen_extension: set[int] = set()
    study_ids: set[str] = set()
    versions: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ProtocolError(f"run matrix row is not an object: {index}")
        run_id = _id(row.get("run_id"), f"runs[{index}].run_id")
        if run_id in seen_run_ids:
            raise ProtocolError(f"run matrix duplicate run_id: {run_id}")
        seen_run_ids.add(run_id)
        study_ids.add(_id(row.get("study_id"), f"runs[{index}].study_id"))
        versions.add(_sha(row.get("study_version"), f"runs[{index}].study_version"))
        problem_id = _id(row.get("problem_id"), f"runs[{index}].problem_id")
        task = task_by_id.get(problem_id)
        if task is None:
            raise ProtocolError(f"run matrix problem is not in holdout manifest: {problem_id}")
        if row.get("problem_family") != task.get("problem_family"):
            raise ProtocolError(f"run matrix problem family mismatch: {problem_id}")
        if row.get("distribution") not in _DISTRIBUTIONS:
            raise ProtocolError(f"run matrix distribution is invalid: {index}")
        if row.get("distribution") not in task.get("distributions", []):
            raise ProtocolError(f"run matrix distribution is not registered: {index}")
        if row.get("model_tier") not in _TIERS:
            raise ProtocolError(f"run matrix model tier is invalid: {index}")
        if row.get("track") not in _TRACKS:
            raise ProtocolError(f"run matrix track is invalid: {index}")
        seed = row.get("seed")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ProtocolError(f"run matrix seed is invalid: {index}")
        role = row.get("seed_role")
        if role == "primary" and seed in primary:
            seen_primary.add(seed)
        elif role == "extension" and seed in extension:
            seen_extension.add(seed)
        else:
            raise ProtocolError(f"run matrix seed/role is not preregistered: {index}")
        _id(row.get("method_id"), f"runs[{index}].method_id")
        events_path = _artifact_relpath(row.get("events_path"), f"runs[{index}].events_path")
        result_path = _artifact_relpath(row.get("result_path"), f"runs[{index}].result_path")
        if events_path == result_path:
            raise ProtocolError(f"run matrix run artifacts must be distinct: {run_id}")
        for artifact_path in (events_path, result_path):
            if artifact_path in seen_artifact_paths:
                raise ProtocolError(f"run matrix artifact path is reused: {artifact_path}")
            seen_artifact_paths.add(artifact_path)
        _sha(row.get("events_sha256"), f"runs[{index}].events_sha256")
        _sha(row.get("result_sha256"), f"runs[{index}].result_sha256")

        if artifact_root is not None:
            root = Path(artifact_root).resolve()
            for path_field, hash_field in (
                ("events_path", "events_sha256"),
                ("result_path", "result_sha256"),
            ):
                lexical_candidate = root / row[path_field]
                if lexical_candidate.is_symlink():
                    raise ProtocolError(
                        f"run matrix {path_field} must not be a symlink: {row[path_field]}"
                    )
                candidate = lexical_candidate.resolve()
                try:
                    candidate.relative_to(root)
                except ValueError as exc:
                    raise ProtocolError(
                        f"run matrix {path_field} escapes bundle root: {row[path_field]}"
                    ) from exc
                if not candidate.is_file():
                    raise ProtocolError(
                        f"run matrix artifact is missing: {row[path_field]}"
                    )
                previous = seen_resolved_paths.get(candidate)
                if previous is not None:
                    raise ProtocolError(
                        "run matrix artifact target is aliased: "
                        f"{row[path_field]} reuses {previous}"
                    )
                seen_resolved_paths[candidate] = row[path_field]
                try:
                    stat = os.stat(candidate)
                except OSError as exc:
                    raise ProtocolError(
                        f"run matrix artifact cannot be stat'ed: {row[path_field]}"
                    ) from exc
                if stat.st_ino:
                    inode_key = (stat.st_dev, stat.st_ino)
                    previous = seen_artifact_targets.get(inode_key)
                    if previous is not None:
                        raise ProtocolError(
                            "run matrix artifact inode is aliased: "
                            f"{row[path_field]} reuses {previous}"
                        )
                    seen_artifact_targets[inode_key] = row[path_field]
                try:
                    actual_hash = sha256_file(candidate)
                except OSError as exc:
                    raise ProtocolError(
                        f"run matrix artifact cannot be hashed: {row[path_field]}"
                    ) from exc
                if actual_hash != row[hash_field]:
                    raise ProtocolError(
                        f"run matrix artifact hash mismatch: {row[path_field]}"
                    )

    if study_ids != {matrix["study_id"]}:
        raise ProtocolError("run matrix rows do not share study_id")
    if versions != {study_version}:
        raise ProtocolError("run matrix rows do not share study_version")
    if seen_primary != set(primary):
        raise ProtocolError("run matrix does not cover every primary seed")
    if matrix_extension:
        if seen_extension != set(extension):
            raise ProtocolError("run matrix does not cover every extension seed")
    elif seen_extension:
        # Extension rows are not allowed to smuggle an unregistered extension
        # into a primary/negative matrix.  The row-level seed check alone is
        # insufficient because extension IDs are intentionally absent when the
        # external primary status is positive or negative.
        raise ProtocolError("run matrix contains extension rows without authorization")
    if matrix.get("primary_seed_count") != len(seen_primary):
        raise ProtocolError("run matrix primary_seed_count mismatch")
    if matrix.get("extension_seed_count") != len(seen_extension):
        raise ProtocolError("run matrix extension_seed_count mismatch")

    if current_result is not None:
        current_run_id = current_result.get("run_id")
        matching = [row for row in rows if row.get("run_id") == current_run_id]
        if len(matching) != 1:
            raise ProtocolError("current result run_id is absent or duplicated in run matrix")
        row = matching[0]
        if current_events_sha256 is not None and row.get("events_sha256") != current_events_sha256:
            raise ProtocolError("run matrix current events hash mismatch")
        if current_result_sha256 is not None and row.get("result_sha256") != current_result_sha256:
            raise ProtocolError("run matrix current result hash mismatch")

    return {
        "run_count": len(rows),
        "primary_seed_count": len(seen_primary),
        "extension_seed_count": len(seen_extension),
        "extension_authorized": extension_authorized,
        "primary_verifier_status": primary_phase_status,
        "run_ids_unique": True,
        "artifact_paths_unique": True,
        "artifact_count": len(seen_artifact_paths),
    }
