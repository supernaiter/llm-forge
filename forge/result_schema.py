"""Fail-closed schema checks for registered V3 run results.

The search loop writes a pre-unblinding result containing run identity and
resource metadata.  The external verifier later adds hidden-test qualities and
GPU-budget curves. Native-compute bundles also carry measured GPU/model-forward
totals, which are bound to the append-only resource ledger by
``study_verifier``. These helpers keep both stages explicit: a missing field is
never interpreted as a zero or as an inapplicable metric by accident.
"""
from __future__ import annotations

import math
import re
import statistics
from collections.abc import Mapping, Sequence
from typing import Any

from .protocol import (
    ProtocolError,
    V3_NATIVE_A100_GPU_SECONDS,
    V3_NATIVE_GPU_FRACTIONS,
    canonical_json,
    sha256_bytes,
)


MODEL_TIERS = ("SMALL", "MEDIUM", "STRONG")
TRACKS = ("SAME_MODEL", "NATIVE_COMPUTE")
SEED_ROLES = ("primary", "extension")
GPU_FRACTIONS = V3_NATIVE_GPU_FRACTIONS
NATIVE_GPU_SECONDS = float(V3_NATIVE_A100_GPU_SECONDS)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")


def _require_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or not _SAFE_ID_RE.fullmatch(value):
        raise ProtocolError(f"result {field} must be a stable non-empty identifier")
    lowered = value.strip().lower()
    if lowered in {"latest", "default", "floating", "unresolved", "draft", "unpinned"}:
        raise ProtocolError(f"result {field} is unresolved")
    return value


def _require_sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ProtocolError(f"result {field} must be a lowercase sha256 digest")
    return value


def _require_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(f"result {field} must be an integer")
    return value


def _identity_payload(result: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "study_id", "study_version", "run_id", "method_id", "problem_id",
        "problem_family", "distribution", "model_tier", "seed", "seed_role",
        "track",
    )
    return {field: result.get(field) for field in fields}


def result_identity_sha256(result: Mapping[str, Any]) -> str:
    """Hash the registered identity fields, excluding its own digest."""
    return sha256_bytes(canonical_json(_identity_payload(result)))


def validate_result_identity(
    result: Mapping[str, Any],
    *,
    task_manifest: Mapping[str, Any] | None = None,
    protocol_spec: Mapping[str, Any] | None = None,
) -> None:
    """Validate stable study/run/task identity and exact seed role semantics."""
    if not isinstance(result, Mapping):
        raise ProtocolError("result.json must be an object")
    for field in (
        "study_id", "study_version", "run_id", "method_id", "problem_id",
        "problem_family", "distribution",
    ):
        _require_id(result.get(field), field)
    _require_sha(result.get("study_version"), "study_version")
    model_tier = result.get("model_tier")
    if model_tier not in MODEL_TIERS:
        raise ProtocolError("result model_tier is not a frozen model tier")
    track = result.get("track")
    if track not in TRACKS:
        raise ProtocolError("result track is not frozen")
    seed = _require_int(result.get("seed"), "seed")
    role = result.get("seed_role")
    if role not in SEED_ROLES:
        raise ProtocolError("result seed_role must be primary or extension")
    seeds = protocol_spec.get("seeds") if isinstance(protocol_spec, Mapping) else None
    primary_values = seeds.get("primary") if isinstance(seeds, Mapping) else list(range(101, 113))
    extension_values = seeds.get("extension") if isinstance(seeds, Mapping) else list(range(113, 125))
    if (
        not isinstance(primary_values, list)
        or not isinstance(extension_values, list)
        or any(isinstance(value, bool) or not isinstance(value, int) for value in (*primary_values, *extension_values))
    ):
        raise ProtocolError("protocol seed sets are malformed")
    primary = set(primary_values)
    extension = set(extension_values)
    allowed = primary if role == "primary" else extension
    if seed not in allowed:
        raise ProtocolError("result seed is not in its registered seed set")
    _require_sha(result.get("run_identity_sha256"), "run_identity_sha256")
    if result["run_identity_sha256"] != result_identity_sha256(result):
        raise ProtocolError("result run identity hash mismatch")
    if isinstance(task_manifest, Mapping):
        holdout = task_manifest.get("holdout_problems")
        matching = [row for row in holdout if isinstance(row, Mapping)
                    and row.get("problem_id") == result.get("problem_id")]
        if len(matching) != 1:
            raise ProtocolError("result problem_id is not uniquely present in holdout manifest")
        if matching[0].get("problem_family") != result.get("problem_family"):
            raise ProtocolError("result problem_family differs from task manifest")
        distributions = matching[0].get("distributions")
        if not isinstance(distributions, list) or result.get("distribution") not in distributions:
            raise ProtocolError("result distribution is not registered for problem")


def validate_gpu_auc_result(
    result: Mapping[str, Any], *, protocol_spec: Mapping[str, Any] | None = None
) -> None:
    """Validate the native-compute GPU-fraction curve or explicit N/A marker."""
    if not isinstance(result, Mapping):
        raise ProtocolError("result.json must be an object")
    track = result.get("track")
    if track not in TRACKS:
        raise ProtocolError("result track is not frozen")
    fractions = GPU_FRACTIONS
    gpu_cap = NATIVE_GPU_SECONDS
    if isinstance(protocol_spec, Mapping):
        budgets = protocol_spec.get("budgets")
        metrics = protocol_spec.get("metrics")
        if not isinstance(budgets, Mapping) or not isinstance(metrics, Mapping):
            raise ProtocolError("protocol metric/budget sections are malformed")
        registered_cap = budgets.get("native_a100_gpu_seconds")
        registered_fractions = metrics.get("native_gpu_fractions")
        if (
            isinstance(registered_cap, bool)
            or not isinstance(registered_cap, (int, float))
            or not math.isfinite(float(registered_cap))
            or float(registered_cap) != gpu_cap
            or not isinstance(registered_fractions, list)
            or tuple(registered_fractions) != fractions
        ):
            raise ProtocolError("protocol GPU metric contract is malformed")
        gpu_cap = float(registered_cap)
    if result.get("native_a100_gpu_seconds_cap") != int(gpu_cap):
        raise ProtocolError("result native GPU-second cap is not preregistered")
    if "gpu_auc_status" not in result:
        raise ProtocolError("result gpu_auc_status is missing")
    curve = result.get("gpu_anytime_curve")
    auc = result.get("auc_gpu")
    if track == "SAME_MODEL":
        if (
            result.get("gpu_auc_status") != "not_applicable"
            or curve != []
            or auc is not None
            or result.get("native_gpu_seconds_observed") is not None
            or result.get("native_model_forward_time_ms_observed") is not None
        ):
            raise ProtocolError("same-model result must mark GPU-AUC not_applicable")
        return
    if result.get("gpu_auc_status") != "complete":
        raise ProtocolError("native result GPU-AUC is not complete")
    observed_total = result.get("native_gpu_seconds_observed")
    if (
        isinstance(observed_total, bool)
        or not isinstance(observed_total, (int, float))
        or not math.isfinite(float(observed_total))
        or float(observed_total) < 0
        or float(observed_total) > NATIVE_GPU_SECONDS
    ):
        raise ProtocolError("native result observed GPU seconds are missing or invalid")
    observed_forward = result.get("native_model_forward_time_ms_observed")
    if (
        isinstance(observed_forward, bool)
        or not isinstance(observed_forward, (int, float))
        or not math.isfinite(float(observed_forward))
        or float(observed_forward) < 0
    ):
        raise ProtocolError("native result observed model-forward time is missing or invalid")
    if not isinstance(curve, list) or len(curve) != len(fractions):
        raise ProtocolError("native result GPU curve has wrong number of fractions")
    qualities: list[float] = []
    observed_values: list[float] = []
    for index, (row, fraction) in enumerate(zip(curve, fractions)):
        if not isinstance(row, Mapping):
            raise ProtocolError(f"native GPU curve row is not an object: {index}")
        observed_fraction = row.get("fraction")
        if (
            isinstance(observed_fraction, bool)
            or not isinstance(observed_fraction, (int, float))
            or not math.isclose(float(observed_fraction), fraction, rel_tol=0.0, abs_tol=1e-12)
        ):
            raise ProtocolError(f"native GPU curve fraction mismatch: {index}")
        gpu_seconds = row.get("gpu_seconds")
        if (
            isinstance(gpu_seconds, bool)
            or not isinstance(gpu_seconds, (int, float))
            or not math.isfinite(float(gpu_seconds))
                or not math.isclose(float(gpu_seconds), fraction * gpu_cap,
                                rel_tol=0.0, abs_tol=1e-9)
        ):
            raise ProtocolError(f"native GPU curve budget mismatch: {index}")
        observed_gpu_seconds = row.get("observed_gpu_seconds")
        if (
            isinstance(observed_gpu_seconds, bool)
            or not isinstance(observed_gpu_seconds, (int, float))
            or not math.isfinite(float(observed_gpu_seconds))
            or float(observed_gpu_seconds) < 0
            or float(observed_gpu_seconds) > float(gpu_seconds)
        ):
            raise ProtocolError(f"native GPU curve observed seconds invalid: {index}")
        if observed_values and float(observed_gpu_seconds) < observed_values[-1]:
            raise ProtocolError("native GPU curve observed seconds are not monotonic")
        observed_values.append(float(observed_gpu_seconds))
        quality = row.get("hidden_test_normalized_quality")
        if (
            isinstance(quality, bool)
            or not isinstance(quality, (int, float))
            or not math.isfinite(float(quality))
        ):
            raise ProtocolError(f"native GPU curve quality is not finite: {index}")
        _require_sha(row.get("candidate_sha256"), f"gpu_anytime_curve[{index}].candidate_sha256")
        qualities.append(float(quality))
    if not math.isclose(observed_values[-1], float(observed_total), rel_tol=0.0, abs_tol=1e-9):
        raise ProtocolError("native result observed GPU seconds differ from curve endpoint")
    if (
        isinstance(auc, bool)
        or not isinstance(auc, (int, float))
        or not math.isfinite(float(auc))
        or not math.isclose(float(auc), statistics.fmean(qualities), rel_tol=0.0, abs_tol=1e-12)
    ):
        raise ProtocolError("result auc_gpu differs from native GPU curve")


def validate_result_schema(
    result: Mapping[str, Any],
    *,
    task_manifest: Mapping[str, Any] | None = None,
    protocol_spec: Mapping[str, Any] | None = None,
) -> None:
    """Run all result identity and compute-metric checks."""
    validate_result_identity(
        result,
        task_manifest=task_manifest,
        protocol_spec=protocol_spec,
    )
    validate_gpu_auc_result(result, protocol_spec=protocol_spec)
