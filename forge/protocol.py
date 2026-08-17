"""Machine-readable Forge Research V3 protocol and fail-closed gates.

The JSON protocol is deliberately data-only.  This module validates its shape,
computes content hashes for manifests, and exposes the terminal-state contract.
It does not infer a positive result from incomplete evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = ROOT / "protocol" / "forge_research_v3.json"
TERMINAL_STATES = frozenset({
    "STRONG_POSITIVE",
    "CLEAN_FALSIFICATION",
    "INCONCLUSIVE",
    "BLOCKED_INTEGRITY_FAILURE",
})
RESEARCH_TERMINAL_STATES = frozenset({
    "STRONG_POSITIVE",
    "CLEAN_FALSIFICATION",
})
POSITIVE_METRIC_GATES = frozenset({
    "same_model_superiority_ready",
    "final_quality_ready",
    "ood_generalization_ready",
    "compute_efficiency_ready",
    "primary_mechanism_validated",
    "replication_ready",
})
POSITIVE_GATE_OPERATORS = frozenset({"ge", "gt", "le", "eq", "bool"})
V3_SAME_MODEL_ATTEMPT_CAP = 512
V3_MAX_INPUT_TOKENS = 4_194_304
V3_MAX_OUTPUT_TOKENS = 524_288
V3_MAX_SEARCH_EVALUATIONS = 512
V3_NATIVE_A100_GPU_SECONDS = 3600
V3_NATIVE_ATTEMPT_CAP = 2048
V3_DEVELOPMENT_PROBLEMS = (
    "obp_dev_v1", "tsp_dev_v1", "jssp_dev_v1", "capset_dev_v1",
)
V3_PRIMARY_SEEDS = tuple(range(101, 113))
V3_EXTENSION_SEEDS = tuple(range(113, 125))
V3_BOOTSTRAP_REPLICATES = 20_000
V3_BOOTSTRAP_SEED = 2_026_080_901
V3_BOOTSTRAP_HIERARCHY = (
    "problem_family", "problem", "seed", "hidden_test_instance_cluster",
)
V3_NATIVE_GPU_FRACTIONS = (
    0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.60, 0.70,
    0.80, 0.90, 1.00,
)
V3_ABLATIONS = (
    "FIXED_DEV_BEST", "NO_TRANSFER_PRIOR", "COST_UNAWARE_CONTROLLER",
)
V3_HOLDOUT_MINIMUMS = {
    "distinct_problem_families_min": 8,
    "families_absent_from_development_min": 5,
    "external_repository_packs_min": 5,
    "search_instance_clusters_min_per_problem": 50,
    "test_instance_clusters_min_per_problem": 100,
    "hidden_test_instances_min_per_problem": 500,
    "size_shift_problems_min": 6,
    "distribution_shift_problems_min": 6,
}


class ProtocolError(ValueError):
    """Raised when a protocol or evidence object is malformed."""


def _sequence(value: Any, field: str) -> tuple[Any, ...]:
    """Return a JSON array as a tuple, translating type errors to ProtocolError."""
    if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)):
        raise ProtocolError(f"protocol field must be an array: {field}")
    return tuple(value)


def _integer(value: Any, field: str) -> int:
    """Require a JSON integer, rejecting booleans and integer-looking floats."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(f"protocol field must be an integer: {field}")
    return value


def canonical_json(value: Any) -> bytes:
    """Serialize JSON in the one representation used for all content hashes."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def strict_json_loads(text: str) -> Any:
    """Decode strict JSON and reject Python-only non-finite constants."""
    def reject_nonfinite_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON constant is not permitted: {value}")

    return json.loads(text, parse_constant=reject_nonfinite_constant)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_protocol(path: str | Path = PROTOCOL_PATH) -> dict[str, Any]:
    """Load and validate the repository's protocol specification."""
    target = Path(path)
    try:
        value = strict_json_loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProtocolError(f"cannot load protocol: {target}: {exc}") from exc
    validate_protocol(value)
    return value


def validate_protocol(spec: Mapping[str, Any]) -> None:
    """Validate required V3 fields and fail closed on unknown terminal states."""
    if not isinstance(spec, Mapping):
        raise ProtocolError("protocol must be a JSON object")
    required = {
        "protocol_id", "protocol_version", "primary_thesis", "development_problems",
        "holdout", "models", "seeds", "budgets", "baselines", "tracks", "metrics",
        "ablations", "statistics", "terminal_states", "requirements",
    }
    missing = sorted(required - set(spec))
    if missing:
        raise ProtocolError(f"protocol missing fields: {', '.join(missing)}")
    if spec["protocol_id"] != "FORGE_RESEARCH_V3" or spec["protocol_version"] != 3:
        raise ProtocolError("unsupported protocol identity/version")
    if spec["primary_thesis"] != "TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V1":
        raise ProtocolError("primary thesis is not the preregistered V3 thesis")
    if _sequence(spec["development_problems"], "development_problems") != V3_DEVELOPMENT_PROBLEMS:
        raise ProtocolError("development problem set is not frozen")
    if set(_sequence(spec["terminal_states"], "terminal_states")) != TERMINAL_STATES:
        raise ProtocolError("terminal_states must exactly match V3 terminal contract")
    if _sequence(spec["models"], "models") != ("SMALL", "MEDIUM", "STRONG"):
        raise ProtocolError("model tiers are not frozen to SMALL/MEDIUM/STRONG")
    if _sequence(spec["tracks"], "tracks") != ("SAME_MODEL", "NATIVE_COMPUTE"):
        raise ProtocolError("comparison tracks are not frozen")
    holdout = spec["holdout"]
    if not isinstance(holdout, Mapping):
        raise ProtocolError("holdout specification must be an object")
    if _integer(holdout.get("problem_count"), "holdout.problem_count") != 10:
        raise ProtocolError("V3 requires exactly 10 holdout problems")
    for field, minimum in V3_HOLDOUT_MINIMUMS.items():
        value = holdout.get(field)
        if _integer(value, f"holdout.{field}") < minimum:
            raise ProtocolError(f"holdout field is below the V3 minimum: {field}")

    seeds = spec["seeds"]
    if not isinstance(seeds, Mapping):
        raise ProtocolError("seed specification must be an object")
    if _sequence(seeds.get("primary", ()), "seeds.primary") != V3_PRIMARY_SEEDS:
        raise ProtocolError("primary seed set is not frozen")
    if _sequence(seeds.get("extension", ()), "seeds.extension") != V3_EXTENSION_SEEDS:
        raise ProtocolError("extension seed set is not frozen")
    if seeds.get("extension_allowed_only_if_primary_inconclusive") is not True:
        raise ProtocolError("extension seeds must require a primary inconclusive status")
    if _integer(seeds.get("maximum_total"), "seeds.maximum_total") != len(V3_PRIMARY_SEEDS) + len(V3_EXTENSION_SEEDS):
        raise ProtocolError("maximum registered seed count is not frozen")

    budgets = spec["budgets"]
    if not isinstance(budgets, Mapping):
        raise ProtocolError("budget specification must be an object")
    expected_budgets = {
        "same_model_attempts": V3_SAME_MODEL_ATTEMPT_CAP,
        "max_input_tokens": V3_MAX_INPUT_TOKENS,
        "max_output_tokens": V3_MAX_OUTPUT_TOKENS,
        "max_search_evaluations": V3_MAX_SEARCH_EVALUATIONS,
        "native_a100_gpu_seconds": V3_NATIVE_A100_GPU_SECONDS,
        "native_max_attempts": V3_NATIVE_ATTEMPT_CAP,
    }
    if any(
        _integer(budgets.get(key), f"budgets.{key}") != expected
        for key, expected in expected_budgets.items()
    ):
        raise ProtocolError("V3 budget constants are not frozen")
    baselines = spec["baselines"]
    if not isinstance(baselines, Mapping):
        raise ProtocolError("baseline specification must be an object")
    expected_peer = (
        "FunSearch", "EoH", "ReEvo", "MCTS_AHD", "PartEvo", "ShinkaEvolve", "EoH_S",
    )
    expected_open = ("OpenEvolve", "CodeEvolve", "EvoX", "SMCEvolve")
    expected_predicate = (
        "public_before_baseline_cutoff", "source_commit_resolved",
        "license_allows_evaluation", "native_smoke_tests_pass",
        "forge_adapter_conformance_pass", "no_material_algorithm_change_required",
    )
    if _sequence(baselines.get("peer_reviewed_required", ()), "baselines.peer_reviewed_required") != expected_peer:
        raise ProtocolError("peer-reviewed baseline set is not frozen")
    if _sequence(baselines.get("open_frontier_candidates", ()), "baselines.open_frontier_candidates") != expected_open:
        raise ProtocolError("open-frontier baseline candidate set is not frozen")
    if baselines.get("baseline_cutoff_utc") != "2026-08-01T00:00:00Z":
        raise ProtocolError("baseline cutoff is not frozen")
    if _sequence(baselines.get("eligibility_predicate", ()), "baselines.eligibility_predicate") != expected_predicate:
        raise ProtocolError("baseline eligibility predicate is not frozen")

    if _sequence(spec["ablations"], "ablations") != V3_ABLATIONS:
        raise ProtocolError("ablation set is not frozen")

    stats = spec["statistics"]
    if not isinstance(stats, Mapping):
        raise ProtocolError("statistics specification must be an object")
    if (
        stats.get("bootstrap") != "paired_hierarchical"
        or _integer(stats.get("replicates"), "statistics.replicates") != V3_BOOTSTRAP_REPLICATES
        or _integer(stats.get("seed"), "statistics.seed") != V3_BOOTSTRAP_SEED
        or _sequence(stats.get("hierarchy", ()), "statistics.hierarchy") != V3_BOOTSTRAP_HIERARCHY
        or stats.get("oracle_reselected_inside_replicate") is not True
        or stats.get("confidence_interval") != "two_sided_percentile_95"
    ):
        raise ProtocolError("V3 bootstrap contract is not frozen")

    metrics = spec["metrics"]
    if not isinstance(metrics, Mapping):
        raise ProtocolError("metric specification must be an object")
    if (
        metrics.get("primary") != "hidden_test_normalized_anytime_auc_by_attempt"
        or metrics.get("compute") != "hidden_test_normalized_anytime_auc_by_a100_gpu_seconds"
        or metrics.get("final") != "normalized_hidden_test_final_incumbent"
        or metrics.get("normalization_clipping") is not False
        or metrics.get("reference_must_be_independent") is not True
        or _sequence(metrics.get("native_gpu_fractions", ()), "metrics.native_gpu_fractions") != V3_NATIVE_GPU_FRACTIONS
    ):
        raise ProtocolError("V3 metric contract is not frozen")
    _validate_positive_gate_contract(metrics)
    requirements = _sequence(spec["requirements"], "requirements")
    if any(not isinstance(item, Mapping) for item in requirements):
        raise ProtocolError("requirements must contain objects")
    ids = [item.get("id") for item in requirements]
    if len(ids) != len(set(ids)) or any(not item for item in ids):
        raise ProtocolError("requirement IDs must be present and unique")


def _validate_positive_gate_contract(metrics: Mapping[str, Any]) -> None:
    """Validate the metric gate schema, including field/operator binding.

    ``positive_gate_thresholds`` is retained as the compact preregistration
    table (and may use human-facing ``*_min``/``*_max`` names).  The companion
    ``positive_gate_contract`` binds every positive boolean to the exact
    evidence field and comparison operator consumed by the verdict engine.
    Keeping the two tables linked makes a changed threshold or relation fail at
    protocol load time instead of silently diverging from the registered rule.
    """
    positive_thresholds = metrics.get("positive_gate_thresholds")
    contract = metrics.get("positive_gate_contract")
    if (
        not isinstance(positive_thresholds, Mapping)
        or set(positive_thresholds) != POSITIVE_METRIC_GATES
        or any(not isinstance(value, Mapping) or not value for value in positive_thresholds.values())
        or not isinstance(contract, Mapping)
        or set(contract) != POSITIVE_METRIC_GATES
        or any(not isinstance(value, Mapping) or not value for value in contract.values())
    ):
        raise ProtocolError("positive metric gate contract is not machine-readable")

    for gate in POSITIVE_METRIC_GATES:
        threshold_table = positive_thresholds[gate]
        gate_contract = contract[gate]
        referenced_threshold_keys: set[str] = set()
        for field, rule in gate_contract.items():
            if not isinstance(field, str) or not isinstance(rule, Mapping):
                raise ProtocolError(f"invalid positive metric gate rule: {gate}.{field}")
            if set(rule) - {"operator", "threshold", "threshold_key"}:
                raise ProtocolError(f"unknown positive metric gate rule field: {gate}.{field}")
            operator = rule.get("operator")
            threshold = rule.get("threshold")
            if operator not in POSITIVE_GATE_OPERATORS:
                raise ProtocolError(f"invalid positive metric gate operator: {gate}.{field}")
            if operator == "bool":
                if threshold is not True:
                    raise ProtocolError(f"boolean metric gate threshold must be true: {gate}.{field}")
            elif (
                isinstance(threshold, bool)
                or not isinstance(threshold, (int, float))
                or not math.isfinite(float(threshold))
            ):
                raise ProtocolError(f"positive metric gate threshold is not finite: {gate}.{field}")

            threshold_key = rule.get("threshold_key", field)
            if not isinstance(threshold_key, str):
                raise ProtocolError(f"invalid positive metric gate threshold key: {gate}.{field}")
            if threshold_key not in threshold_table:
                # Boolean invariants (same model pool, same evaluator budget,
                # etc.) are represented directly in the contract and do not
                # need a duplicate numeric entry in the compact table.
                if operator != "bool" or "threshold_key" in rule:
                    raise ProtocolError(
                        f"positive metric gate threshold key is missing: {gate}.{threshold_key}"
                    )
                continue
            referenced_threshold_keys.add(threshold_key)
            registered = threshold_table[threshold_key]
            if isinstance(registered, bool) or not isinstance(registered, (int, float)):
                raise ProtocolError(f"positive metric gate threshold is malformed: {gate}.{threshold_key}")
            if float(registered) != float(threshold):
                raise ProtocolError(
                    f"positive metric gate threshold mismatch: {gate}.{field}"
                )
        if referenced_threshold_keys != set(threshold_table):
            raise ProtocolError(f"positive metric gate threshold coverage mismatch: {gate}")


def protocol_hash(path: str | Path = PROTOCOL_PATH) -> str:
    """Hash the exact protocol bytes, not a parsed/reformatted equivalent."""
    return sha256_file(path)


def research_verdict(
    *,
    integrity_ready: bool,
    strong_method_ready: bool = False,
    clean_falsification_ready: bool = False,
    integrity_failure: bool = False,
) -> str:
    """Return the V3 terminal state using a fail-closed precedence order."""
    if integrity_failure or not integrity_ready:
        return "BLOCKED_INTEGRITY_FAILURE"
    if strong_method_ready:
        return "STRONG_POSITIVE"
    if clean_falsification_ready:
        return "CLEAN_FALSIFICATION"
    return "INCONCLUSIVE"


def validate_evidence(evidence: Mapping[str, Any]) -> None:
    """Require explicit booleans before a verdict can be computed."""
    required = {"integrity_ready", "strong_method_ready", "clean_falsification_ready"}
    missing = sorted(required - set(evidence))
    if missing:
        raise ProtocolError(f"evidence missing fields: {', '.join(missing)}")
    if any(not isinstance(evidence[key], bool) for key in required):
        raise ProtocolError("verdict evidence fields must be booleans")
