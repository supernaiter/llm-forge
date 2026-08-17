"""Machine-auditable resource and budget accounting for Research V3.

Resource telemetry is deliberately conservative.  A caller may report an
observed value or leave it unavailable, but it may not silently replace an
unobserved value with an estimate.  The normalized record therefore always
contains every field and an explicit ``missing`` list.

The event ledger owns the tamper-evident persistence.  This module owns the
schema, validation, aggregation, and separated generation/evaluator budget
semantics used by the ledger, replay, and public verifier.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Mapping


RESOURCE_SCHEMA_VERSION = 1
RESOURCE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "model_identity",
    "sampling_profile",
    "wall_time_ms",
    "gpu_allocation",
    "model_forward_time_ms",
    "evaluator_cost",
    "evaluator_calls",
)
RESOURCE_PHASES = frozenset({"generation", "evaluator"})
_NUMERIC_FIELDS = frozenset({
    "input_tokens",
    "output_tokens",
    "wall_time_ms",
    "model_forward_time_ms",
    "evaluator_cost",
    "evaluator_calls",
})
_FORBIDDEN_ESTIMATE_KEYS = frozenset({
    "estimate",
    "estimated",
    "estimated_value",
    "imputed",
    "inferred",
})
_FORBIDDEN_MODEL_ALIASES = frozenset({"latest", "default", "main", "master", "floating", "unpinned"})
_REQUIRED_FIELDS_BY_PHASE = {
    "generation": frozenset({
        "input_tokens", "output_tokens", "model_identity", "sampling_profile", "wall_time_ms",
    }),
    "evaluator": frozenset({
        "wall_time_ms", "evaluator_cost", "evaluator_calls", "model_identity",
    }),
}


class ResourceError(ValueError):
    """Raised when resource telemetry is malformed or violates an invariant."""


def _finite_nonnegative(value: Any, field: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResourceError(f"{field} must be a non-negative number or null")
    if not math.isfinite(float(value)) or value < 0:
        raise ResourceError(f"{field} must be finite and non-negative")
    return value


def normalize_usage(usage: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return the canonical, explicit representation of one resource record.

    ``None`` means that telemetry was not obtained; it never means zero.
    Existing callers can omit the mapping entirely and receive a valid record
    whose fields are all explicitly marked missing.  Any estimate/imputation
    marker is rejected so downstream reports cannot mistake it for observed
    telemetry.
    """

    if usage is None:
        usage = {}
    if not isinstance(usage, Mapping):
        raise ResourceError("resource usage must be an object")
    unknown = set(usage) - set(RESOURCE_FIELDS) - {
        "schema_version", "missing", "telemetry_complete", "telemetry_notes",
        "evaluator_cost_unit",
    }
    if unknown:
        raise ResourceError("unknown resource fields: " + ", ".join(sorted(map(str, unknown))))
    for key in _FORBIDDEN_ESTIMATE_KEYS:
        if key in usage and usage[key]:
            raise ResourceError(f"estimated telemetry is forbidden: {key}")
    if "schema_version" in usage and usage["schema_version"] != RESOURCE_SCHEMA_VERSION:
        raise ResourceError("unsupported resource schema version")

    out: dict[str, Any] = {
        "schema_version": RESOURCE_SCHEMA_VERSION,
    }
    for field in RESOURCE_FIELDS:
        value = usage.get(field)
        if field in _NUMERIC_FIELDS and value is not None:
            value = _finite_nonnegative(value, field)
        elif field == "model_identity" and value is not None:
            if (
                not isinstance(value, str)
                or not value.strip()
                or value.strip().lower() in _FORBIDDEN_MODEL_ALIASES
            ):
                raise ResourceError("model_identity must be a non-empty string or null")
        elif field == "sampling_profile" and value is not None:
            if not isinstance(value, Mapping):
                raise ResourceError("sampling_profile must be an object or null")
            value = dict(value)
        elif field == "gpu_allocation" and value is not None:
            if not isinstance(value, Mapping):
                raise ResourceError("gpu_allocation must be an object or null")
            value = dict(value)
            if "seconds" in value and value["seconds"] is not None:
                value["seconds"] = _finite_nonnegative(value["seconds"], "gpu_allocation.seconds")
            if "count" in value and value["count"] is not None:
                value["count"] = _finite_nonnegative(value["count"], "gpu_allocation.count")
        out[field] = value

    missing = sorted(field for field in RESOURCE_FIELDS if out[field] is None)
    supplied_missing = usage.get("missing")
    if supplied_missing is not None:
        if not isinstance(supplied_missing, (list, tuple)) or any(
            not isinstance(item, str) for item in supplied_missing
        ):
            raise ResourceError("missing must be a list of resource field names")
        if sorted(set(supplied_missing)) != missing:
            raise ResourceError("missing does not match null resource fields")
    out["missing"] = missing
    out["telemetry_complete"] = not missing
    if "telemetry_notes" in usage:
        notes = usage["telemetry_notes"]
        if not isinstance(notes, list) or any(not isinstance(item, str) for item in notes):
            raise ResourceError("telemetry_notes must be a list of strings")
        out["telemetry_notes"] = list(notes)
    if "evaluator_cost_unit" in usage:
        unit = usage["evaluator_cost_unit"]
        if unit is not None and (not isinstance(unit, str) or not unit.strip()):
            raise ResourceError("evaluator_cost_unit must be a non-empty string or null")
        out["evaluator_cost_unit"] = unit
    return out


def empty_usage(*, notes: list[str] | None = None) -> dict[str, Any]:
    """Create an explicit all-missing record without inventing zeros."""

    value: dict[str, Any] = {}
    if notes:
        value["telemetry_notes"] = list(notes)
    return normalize_usage(value)


def evaluator_usage(*, wall_time_ms: float | int | None,
                    evaluator_cost: float | int | None = None,
                    evaluator_id: str | None = None,
                    calls: int | float | None = 1,
                    notes: list[str] | None = None) -> dict[str, Any]:
    """Build a resource record for one evaluator phase.

    ``evaluator_cost`` is an observed cost unit (normally wall seconds or a
    billable evaluator unit), never an imputed score.  The unit is recorded
    separately so a future study can use a different cost model.
    """

    payload: dict[str, Any] = {
        "wall_time_ms": wall_time_ms,
        "evaluator_cost": evaluator_cost,
        "model_identity": evaluator_id,
        "evaluator_cost_unit": "wall_seconds" if evaluator_cost is not None else None,
        "evaluator_calls": calls,
    }
    if notes:
        payload["telemetry_notes"] = list(notes)
    return normalize_usage(payload)


def generation_usage(*, wall_time_ms: float | int | None,
                     model_identity: str | None = None,
                     sampling_profile: Mapping[str, Any] | None = None,
                     input_tokens: int | None = None,
                     output_tokens: int | None = None,
                     gpu_allocation: Mapping[str, Any] | None = None,
                     model_forward_time_ms: float | int | None = None,
                     notes: list[str] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "wall_time_ms": wall_time_ms,
        "model_identity": model_identity,
        "sampling_profile": dict(sampling_profile) if sampling_profile is not None else None,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "gpu_allocation": dict(gpu_allocation) if gpu_allocation is not None else None,
        "model_forward_time_ms": model_forward_time_ms,
    }
    if notes:
        payload["telemetry_notes"] = list(notes)
    return normalize_usage(payload)


def _sum_known(records: list[Mapping[str, Any]], field: str) -> float | int | None:
    values = [record.get(field) for record in records]
    if any(value is None for value in values):
        return None
    return sum(values)  # type: ignore[arg-type]


def _sum_gpu_seconds(records: list[Mapping[str, Any]]) -> float | None:
    """Compute allocated GPU-seconds without treating missing allocation as 0."""
    if not records:
        return 0
    total = 0.0
    for record in records:
        allocation = record.get("gpu_allocation")
        if allocation is None:
            return None
        if not isinstance(allocation, Mapping):
            return None
        seconds = allocation.get("seconds")
        count = allocation.get("count", 1)
        if seconds is None or count is None:
            return None
        try:
            total += float(seconds) * float(count)
        except (TypeError, ValueError):
            return None
    return total


def summarize_resources(records: list[Mapping[str, Any]],
                        *, budgets: Mapping[str, Any] | None = None,
                        require_gpu: bool = False) -> dict[str, Any]:
    """Aggregate records without converting unknown values into zero.

    Budgets are keyed by phase (``generation`` or ``evaluator``).  A budget
    check is ``unknown`` when the corresponding telemetry is incomplete; this
    is intentionally not reported as a pass.
    """

    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    errors: list[str] = []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            errors.append(f"record {index} is not an object")
            continue
        phase = record.get("phase")
        attempt_id = record.get("attempt_id")
        if phase not in RESOURCE_PHASES:
            errors.append(f"record {index} has invalid phase")
            continue
        if not isinstance(attempt_id, str) or not attempt_id:
            errors.append(f"record {index} has invalid attempt_id")
            continue
        key = (str(phase), attempt_id)
        if key in seen:
            errors.append(f"duplicate resource record: {phase}:{attempt_id}")
            continue
        seen.add(key)
        try:
            usage = normalize_usage(record.get("usage"))
        except ResourceError as exc:
            errors.append(f"record {index}: {exc}")
            continue
        normalized.append({"phase": phase, "attempt_id": attempt_id, "usage": usage})

    by_phase: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in normalized:
        by_phase[str(record["phase"])].append(record)

    phase_summary: dict[str, Any] = {}
    all_missing: dict[str, set[str]] = {phase: set() for phase in RESOURCE_PHASES}
    for phase in sorted(RESOURCE_PHASES):
        phase_records = [record["usage"] for record in by_phase[phase]]
        for usage in phase_records:
            all_missing[phase].update(usage["missing"])
        totals = {
            field: _sum_known(phase_records, field) if phase_records else 0
            for field in _NUMERIC_FIELDS
        }
        totals["gpu_seconds"] = _sum_gpu_seconds(phase_records)
        required_missing: set[str] = set()
        for usage in phase_records:
            required = set(_REQUIRED_FIELDS_BY_PHASE[phase])
            if phase == "generation" and "non_llm_parametric_generation" in usage.get(
                "telemetry_notes", []
            ):
                required -= {"input_tokens", "output_tokens"}
            if phase == "generation" and require_gpu:
                required.update({"gpu_allocation", "model_forward_time_ms"})
            required_missing.update(field for field in required if usage.get(field) is None)
        phase_summary[phase] = {
            "record_count": len(phase_records),
            "totals": totals,
            "missing_fields": sorted(all_missing[phase]),
            "required_missing_fields": sorted(required_missing),
            "telemetry_complete": bool(phase_records) and not required_missing,
        }

    budget_checks: dict[str, Any] = {}
    limits = budgets if isinstance(budgets, Mapping) else {}
    for phase in sorted(RESOURCE_PHASES):
        phase_limits = limits.get(phase, {})
        if not isinstance(phase_limits, Mapping):
            errors.append(f"budget for {phase} is not an object")
            phase_limits = {}
        phase_result: dict[str, Any] = {}
        totals = phase_summary[phase]["totals"]
        for field, limit in phase_limits.items():
            if field == "records":
                observed = phase_summary[phase]["record_count"]
            elif phase == "evaluator" and field == "calls":
                observed = totals.get("evaluator_calls")
            else:
                observed = totals.get(field)
            if isinstance(limit, bool) or not isinstance(limit, (int, float)) or limit < 0:
                errors.append(f"invalid {phase} budget limit: {field}")
                continue
            if observed is None:
                phase_result[field] = {"status": "unknown", "observed": None, "limit": limit}
            else:
                phase_result[field] = {
                    "status": "pass" if observed <= limit else "exceeded",
                    "observed": observed,
                    "limit": limit,
                }
        budget_checks[phase] = phase_result

    budget_violations = [
        f"{phase}.{field}"
        for phase, result in budget_checks.items()
        for field, check in result.items()
        if check.get("status") == "exceeded"
    ]
    return {
        "schema_version": RESOURCE_SCHEMA_VERSION,
        "record_count": len(normalized),
        "records": normalized,
        "phases": phase_summary,
        "budgets": budget_checks,
        "budget_violations": budget_violations,
        "errors": sorted(set(errors)),
        "valid": not errors,
        "telemetry_complete": all(
            phase_summary[phase]["telemetry_complete"] for phase in RESOURCE_PHASES
        ) if normalized else False,
    }


def merge_usage(first: Mapping[str, Any] | None,
                second: Mapping[str, Any] | None) -> dict[str, Any]:
    """Merge two observations from the same phase without imputing values."""

    left = normalize_usage(first)
    right = normalize_usage(second)
    payload: dict[str, Any] = {}
    for field in RESOURCE_FIELDS:
        a, b = left[field], right[field]
        if field in _NUMERIC_FIELDS:
            payload[field] = (a + b) if a is not None and b is not None else None
        elif a is not None and b is not None and a == b:
            payload[field] = a
        elif a is not None and b is None:
            payload[field] = a
        elif b is not None and a is None:
            payload[field] = b
        else:
            if field == "model_identity":
                identities = sorted({str(a), str(b)})
                payload[field] = "MULTIPLE[" + ",".join(identities) + "]"
            elif field == "sampling_profile":
                payload[field] = {"components": [a, b]}
            else:
                payload[field] = None
    notes = list(dict.fromkeys(left.get("telemetry_notes", []) + right.get("telemetry_notes", [])))
    if notes:
        payload["telemetry_notes"] = notes
    units = {left.get("evaluator_cost_unit"), right.get("evaluator_cost_unit")} - {None}
    if len(units) == 1:
        payload["evaluator_cost_unit"] = units.pop()
    return normalize_usage(payload)


class ResourceLedger:
    """Small in-memory resource ledger used by adapters and independent checks.

    ``EventLedger`` is the authoritative persistent implementation.  This
    class is intentionally useful on its own for evaluator adapters and unit
    tests, while sharing exactly the same normalization and aggregation code.
    """

    def __init__(self, records: list[Mapping[str, Any]] | None = None,
                 *, budgets: Mapping[str, Any] | None = None,
                 require_gpu: bool = False):
        self.records: list[dict[str, Any]] = []
        self.budgets = dict(budgets or {})
        self.require_gpu = bool(require_gpu)
        for record in records or []:
            self.record(
                phase=record.get("phase"),
                attempt_id=record.get("attempt_id"),
                usage=record.get("usage"),
            )

    def record(self, *, phase: str, attempt_id: str,
               usage: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if phase not in RESOURCE_PHASES:
            raise ResourceError(f"invalid resource phase: {phase}")
        if not isinstance(attempt_id, str) or not attempt_id:
            raise ResourceError("attempt_id must be a non-empty string")
        if any(
            item["phase"] == phase and item["attempt_id"] == attempt_id
            for item in self.records
        ):
            raise ResourceError(f"duplicate resource record: {phase}:{attempt_id}")
        item = {"phase": phase, "attempt_id": attempt_id,
                "usage": normalize_usage(usage)}
        self.records.append(item)
        return item

    def summary(self) -> dict[str, Any]:
        return summarize_resources(
            self.records, budgets=self.budgets, require_gpu=self.require_gpu
        )

    def assert_invariants(self) -> None:
        summary = self.summary()
        if summary["errors"]:
            raise ResourceError("; ".join(summary["errors"]))
        if (
            self.require_gpu
            and summary["phases"]["generation"]["required_missing_fields"]
        ):
            raise ResourceError(
                "native generation telemetry missing: "
                + ", ".join(summary["phases"]["generation"]["required_missing_fields"])
            )
        if summary["budget_violations"]:
            raise ResourceError(
                "resource budget exceeded: " + ", ".join(summary["budget_violations"])
            )
