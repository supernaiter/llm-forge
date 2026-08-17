"""Baseline registry validation for Forge Research V3."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .protocol import ProtocolError, canonical_json, sha256_bytes, strict_json_loads


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "protocol" / "baseline_registry_v3.json"
PEER_REQUIRED = (
    "FunSearch", "EoH", "ReEvo", "MCTS_AHD", "PartEvo", "ShinkaEvolve", "EoH_S"
)
OPEN_CANDIDATES = ("OpenEvolve", "CodeEvolve", "EvoX", "SMCEvolve")
BASELINE_CUTOFF_UTC = "2026-08-01T00:00:00Z"
BASELINE_TRACKS = frozenset({"SAME_MODEL", "NATIVE_COMPUTE"})
BASELINE_CATEGORIES = frozenset({"peer_reviewed", "open_frontier"})
ELIGIBILITY_FIELDS = (
    "public_before_baseline_cutoff",
    "source_commit",
    "license_allows_evaluation",
    "native_smoke_tests_pass",
    "forge_adapter_conformance_pass",
    "no_material_algorithm_change_required",
)
BOOLEAN_ELIGIBILITY_FIELDS = (
    "public_before_baseline_cutoff",
    "license_allows_evaluation",
    "native_smoke_tests_pass",
    "forge_adapter_conformance_pass",
    "no_material_algorithm_change_required",
)
_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
ELIGIBLE_EVIDENCE_FIELDS = (
    "source_url",
    "license_id",
    "adapter_id",
    "native_smoke_evidence_sha256",
    "adapter_conformance_evidence_sha256",
    "algorithm_change_audit_sha256",
)


def load_registry(path: str | Path = REGISTRY_PATH) -> dict[str, Any]:
    target = Path(path)
    try:
        registry = strict_json_loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProtocolError(f"cannot load baseline registry: {target}") from exc
    validate_registry(registry)
    return registry


def validate_registry(registry: Mapping[str, Any]) -> None:
    if registry.get("registry_id") != "FORGE_BASELINES_V3":
        raise ProtocolError("unsupported baseline registry")
    if registry.get("baseline_cutoff_utc") != BASELINE_CUTOFF_UTC:
        raise ProtocolError("baseline cutoff is not the preregistered UTC instant")
    for field in ("post_unblinding_baseline_additions", "post_unblinding_baseline_deletions"):
        value = registry.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value != 0:
            raise ProtocolError(f"baseline registry {field} must be integer zero")
    entries = registry.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ProtocolError("baseline registry has no entries")
    by_name = {entry.get("name"): entry for entry in entries}
    missing = [name for name in PEER_REQUIRED if name not in by_name]
    if missing:
        raise ProtocolError(f"mandatory peer baselines missing: {', '.join(missing)}")
    if len(by_name) != len(entries):
        raise ProtocolError("baseline names must be unique")
    for entry in entries:
        if not isinstance(entry.get("name"), str) or entry.get("status") not in {
            "unresolved", "eligible", "ineligible"
        }:
            raise ProtocolError("baseline entry has invalid name/status")
        if not isinstance(entry.get("required"), bool):
            raise ProtocolError("baseline entry required must be boolean")
        if entry.get("track") not in BASELINE_TRACKS:
            raise ProtocolError("baseline entry track is invalid")
        if entry.get("category") not in BASELINE_CATEGORIES:
            raise ProtocolError("baseline entry category is invalid")
        name = entry["name"]
        if name in PEER_REQUIRED and (
            entry["required"] is not True
            or entry["track"] != "SAME_MODEL"
            or entry["category"] != "peer_reviewed"
        ):
            raise ProtocolError(f"mandatory peer baseline metadata is invalid: {name}")
        if name in OPEN_CANDIDATES and (
            entry["track"] != "NATIVE_COMPUTE"
            or entry["category"] != "open_frontier"
        ):
            raise ProtocolError(f"open frontier baseline metadata is invalid: {name}")
        source_commit = entry.get("source_commit")
        if source_commit is not None and (
            not isinstance(source_commit, str) or not _COMMIT_RE.fullmatch(source_commit)
        ):
            raise ProtocolError("baseline source_commit must be a full hexadecimal commit")
        if "source_commit_observed_at" not in entry:
            raise ProtocolError("baseline entry missing source_commit_observed_at")
        observed_at = entry.get("source_commit_observed_at")
        if source_commit is None:
            if observed_at not in (None, ""):
                raise ProtocolError("source_commit_observed_at requires source_commit")
        else:
            if not isinstance(observed_at, str) or not observed_at:
                raise ProtocolError("resolved baseline requires source_commit_observed_at")
            try:
                observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ProtocolError("source_commit_observed_at is not ISO-8601") from exc
            if observed.tzinfo is None:
                raise ProtocolError("source_commit_observed_at must include UTC offset")
            cutoff = datetime.fromisoformat(BASELINE_CUTOFF_UTC.replace("Z", "+00:00"))
            if observed.astimezone(timezone.utc) > cutoff:
                raise ProtocolError("baseline source commit was observed after cutoff")
        for key in ELIGIBILITY_FIELDS:
            if key not in entry:
                raise ProtocolError(f"baseline entry missing eligibility field: {key}")
        for key in BOOLEAN_ELIGIBILITY_FIELDS:
            if not isinstance(entry[key], bool):
                raise ProtocolError(f"baseline eligibility field must be boolean: {key}")
        if entry["status"] == "eligible":
            for key in ELIGIBLE_EVIDENCE_FIELDS:
                value = entry.get(key)
                if not isinstance(value, str) or not value:
                    raise ProtocolError(f"eligible baseline missing evidence field: {key}")
                if key.endswith("_sha256") and _SHA256_RE.fullmatch(value) is None:
                    raise ProtocolError(f"eligible baseline evidence is not sha256: {key}")


def baseline_registry_sha256(path: str | Path = REGISTRY_PATH) -> str:
    target = Path(path)
    return sha256_bytes(target.read_bytes())


def baseline_eligible(entry: Mapping[str, Any]) -> bool:
    """Eligibility is an explicit conjunction; unresolved is never eligible."""
    return (
        entry.get("status") == "eligible"
        and isinstance(entry.get("source_commit"), str)
        and _COMMIT_RE.fullmatch(entry["source_commit"]) is not None
        and isinstance(entry.get("source_commit_observed_at"), str)
        and bool(entry.get("source_commit_observed_at"))
        and all(entry.get(key) is True for key in BOOLEAN_ELIGIBILITY_FIELDS)
        and all(
            isinstance(entry.get(key), str) and bool(entry.get(key))
            and (not key.endswith("_sha256") or _SHA256_RE.fullmatch(entry[key]) is not None)
            for key in ELIGIBLE_EVIDENCE_FIELDS
        )
    )


def baseline_readiness_report(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Explain why a registry entry is or is not eligible, without coercion."""
    missing_evidence = [
        key for key in ELIGIBLE_EVIDENCE_FIELDS
        if not isinstance(entry.get(key), str) or not entry.get(key)
    ]
    failed_predicates = []
    if entry.get("status") != "eligible":
        failed_predicates.append("status_is_eligible")
    if not isinstance(entry.get("source_commit"), str) or \
            _COMMIT_RE.fullmatch(entry.get("source_commit", "")) is None:
        failed_predicates.append("source_commit_pinned")
    failed_predicates.extend(
        key for key in BOOLEAN_ELIGIBILITY_FIELDS if entry.get(key) is not True
    )
    failed_predicates.extend(f"evidence:{key}" for key in missing_evidence)
    for key in ELIGIBLE_EVIDENCE_FIELDS:
        if key.endswith("_sha256") and isinstance(entry.get(key), str) and entry[key]:
            if _SHA256_RE.fullmatch(entry[key]) is None:
                failed_predicates.append(f"evidence:{key}_format")
    return {
        "name": entry.get("name"),
        "status": entry.get("status"),
        "eligible": baseline_eligible(entry),
        "failed_predicates": sorted(set(failed_predicates)),
    }


def baseline_registry_report(registry: Mapping[str, Any]) -> list[dict[str, Any]]:
    validate_registry(registry)
    return [baseline_readiness_report(entry) for entry in registry["entries"]]


def primary_baselines(registry: Mapping[str, Any]) -> list[dict[str, Any]]:
    validate_registry(registry)
    entries = registry["entries"]
    peers = [entry for entry in entries if entry["name"] in PEER_REQUIRED]
    if not all(baseline_eligible(entry) for entry in peers):
        raise ProtocolError("mandatory peer baseline conformance is incomplete")
    return [entry for entry in entries if baseline_eligible(entry)]


def adapter_conformance_report(entry: Mapping[str, Any], smoke_test: Callable[[], Any]) -> dict[str, Any]:
    """Run a baseline's native smoke adapter twice and compare outputs exactly."""
    report: dict[str, Any] = {
        "name": entry.get("name"),
        "baseline_eligible": baseline_eligible(entry),
        "native_smoke_tests_pass": False,
        "deterministic_replay": False,
        "adapter_conformance_pass": False,
    }
    if not report["baseline_eligible"]:
        report["reason"] = "eligibility predicate is incomplete"
        return report
    try:
        first = smoke_test()
        second = smoke_test()
    except Exception as exc:  # adapter failures are data, not Forge wins
        report["reason"] = f"native smoke failure: {type(exc).__name__}"
        return report
    report["native_smoke_tests_pass"] = True
    report["deterministic_replay"] = first == second
    report["adapter_conformance_pass"] = report["deterministic_replay"]
    if not report["deterministic_replay"]:
        report["reason"] = "native smoke output changed across identical runs"
    return report
