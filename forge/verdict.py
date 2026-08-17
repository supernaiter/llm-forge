"""Deterministic V3 research gates.

All predicates are explicit and fail closed.  Missing evidence is not treated
as a zero score, a passing default, or an absent baseline.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

from .protocol import ProtocolError, load_protocol, research_verdict


INTEGRITY_BOOLEAN_FLAGS = (
    "exact_source_commit_frozen",
    "protocol_manifest_sha256_frozen",
    "baseline_registry_sha256_frozen",
    "model_manifests_sha256_frozen",
    "task_manifests_sha256_frozen",
    "evaluator_manifests_sha256_frozen",
    "container_image_digests_frozen",
    "prompt_and_decoding_profiles_frozen",
    "p_dev_overlap_with_p_holdout_zero",
    "heldout_problem_family_requirements_pass",
    "search_test_instance_overlap_zero",
    "hidden_test_hash_access_by_search_zero",
    "hidden_test_score_feedback_events_zero",
    "candidate_generator_hidden_test_access_zero",
    "post_unblinding_core_changes_zero",
    "post_unblinding_prompt_changes_zero",
    "post_unblinding_model_changes_zero",
    "post_unblinding_baseline_changes_zero",
    "post_unblinding_metric_changes_zero",
    "post_unblinding_threshold_changes_zero",
    "post_unblinding_ablation_changes_zero",
    "post_unblinding_changes_zero",
    "evaluator_nondeterminism_events_zero",
    "evaluator_hack_false_accept_count_zero",
    "test_data_mutation_count_zero",
    "cross_run_state_leakage_count_zero",
    "resource_budget_telemetry_complete",
)

INTEGRITY_ZERO_FIELDS = (
    "invalid_or_missing_primary_runs",
    "budget_violation_count",
    "accepted_evaluation_hack_count",
    "hidden_test_side_channel_count",
    "baseline_adapter_conformance_failures",
    "benchmark_identifier_branches_in_forge",
    "holdout_specific_prompt_count",
    "hidden_answer_literal_count",
    "controller_parameter_updates_on_holdout",
    "controller_training_holdout_access_count",
)

STRONG_BOOLEAN_GATES = (
    "same_model_superiority_ready",
    "final_quality_ready",
    "ood_generalization_ready",
    "compute_efficiency_ready",
    "primary_mechanism_validated",
    "replication_ready",
)

Q1_MPE = 0.030
Q2_MPE = 0.030
Q3_FIXED_MPE = 0.015
Q3_TRANSFER_MPE = 0.010
Q3_COST_MPE = 0.010
Q4_MPE = 0.020
_Q_STATUSES = frozenset({"strong_positive", "clean_negative", "inconclusive"})

def _load_metric_gate_requirements() -> dict[str, tuple[tuple[str, str, Any], ...]]:
    """Load the exact field/operator/threshold contract from the protocol.

    The JSON protocol is the registered source of truth.  The protocol loader
    validates its compact threshold aliases and this expanded contract before
    the verdict engine consumes it, so changing either the numeric value or
    the comparison relation cannot silently alter the scientific gate.
    """
    contract = load_protocol()["metrics"]["positive_gate_contract"]
    return {
        gate: tuple(
            (field, rule["operator"], rule["threshold"])
            for field, rule in rules.items()
        )
        for gate, rules in contract.items()
    }


_METRIC_GATE_REQUIREMENTS = _load_metric_gate_requirements()


def _all_true(evidence: Mapping[str, Any], fields: tuple[str, ...]) -> bool:
    return all(evidence.get(field) is True for field in fields)


def _all_zero(evidence: Mapping[str, Any], fields: tuple[str, ...]) -> bool:
    return all(
        isinstance(evidence.get(field), int)
        and not isinstance(evidence.get(field), bool)
        and evidence.get(field) == 0
        for field in fields
    )


def _required_ci_high(metrics: Mapping[str, Any], field: str) -> float:
    value = metrics.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ProtocolError(f"metric evidence missing finite CI high: {field}")
    return float(value)


def _required_bool(metrics: Mapping[str, Any], field: str) -> bool:
    value = metrics.get(field)
    if not isinstance(value, bool):
        raise ProtocolError(f"metric evidence missing boolean gate: {field}")
    return value


def validate_metric_gate_claims(metrics: Mapping[str, Any]) -> None:
    """Validate numeric attestations behind every claimed positive gate.

    ``metrics_summary`` is a frozen input, not an authority by itself.  This
    check makes each positive boolean auditable: booleans must be exact, all
    required numeric fields must be finite, and every preregistered inequality
    must hold.  Negative/inconclusive gates still require their CI fields via
    :func:`derive_q_statuses`, but do not need irrelevant positive-only rows.
    """
    if not isinstance(metrics, Mapping):
        raise ProtocolError("metrics summary must be an object")
    for gate in STRONG_BOOLEAN_GATES:
        claimed = metrics.get(gate)
        if not isinstance(claimed, bool):
            raise ProtocolError(f"metric gate is missing boolean: {gate}")
        if not claimed:
            continue
        for field, relation, threshold in _METRIC_GATE_REQUIREMENTS[gate]:
            value = metrics.get(field)
            if relation == "bool":
                if value is not True:
                    raise ProtocolError(f"positive metric gate requires true: {field}")
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise ProtocolError(f"positive metric gate requires finite value: {field}")
            observed = float(value)
            passed = {
                "ge": observed >= threshold,
                "gt": observed > threshold,
                "le": observed <= threshold,
                "eq": observed == threshold,
            }.get(relation)
            if passed is not True:
                raise ProtocolError(
                    f"positive metric gate threshold failed: {field} {relation} {threshold}"
                )


def _q_status(positive_ready: bool, ci_high: float, threshold: float) -> str:
    if positive_ready:
        return "strong_positive"
    if ci_high < threshold:
        return "clean_negative"
    # At or above the registered boundary without satisfying the complete
    # positive gate is explicitly inconclusive; never call it negative.
    return "inconclusive"


def derive_q_statuses(metrics: Mapping[str, Any]) -> dict[str, str]:
    """Derive Q1--Q4 status from preregistered gates and CI highs.

    This function intentionally requires all inputs and uses strict
    inequalities from the criterion.  It does not infer missing effects,
    treat NaN as zero, or let a favorable partial gate produce a positive.
    """
    q3_positive = _required_bool(metrics, "primary_mechanism_validated")
    q3_fixed_high = _required_ci_high(metrics, "delta_fixed_95ci_high")
    q3_transfer_high = _required_ci_high(metrics, "delta_transfer_95ci_high")
    q3_cost_high = _required_ci_high(metrics, "delta_cost_95ci_high")
    q3_status = "strong_positive" if q3_positive else (
        "clean_negative" if (
            q3_fixed_high < Q3_FIXED_MPE
            or q3_transfer_high < Q3_TRANSFER_MPE
            or q3_cost_high < Q3_COST_MPE
        ) else "inconclusive"
    )
    return {
        "q1_status": _q_status(
            _required_bool(metrics, "same_model_superiority_ready"),
            _required_ci_high(metrics, "overall_delta_oracle_95ci_high"),
            Q1_MPE,
        ),
        "q2_status": _q_status(
            _required_bool(metrics, "compute_efficiency_ready"),
            _required_ci_high(metrics, "overall_delta_gpu_oracle_95ci_high"),
            Q2_MPE,
        ),
        "q3_status": q3_status,
        "q4_status": _q_status(
            _required_bool(metrics, "ood_generalization_ready"),
            _required_ci_high(metrics, "overall_delta_ood_95ci_high"),
            Q4_MPE,
        ),
    }


def integrity_ready(evidence: Mapping[str, Any]) -> bool:
    """Evaluate the protocol gate without coercing missing or string values."""
    return (
        _all_true(evidence, INTEGRITY_BOOLEAN_FLAGS)
        and _all_zero(evidence, INTEGRITY_ZERO_FIELDS)
    )


def strong_method_ready(evidence: Mapping[str, Any]) -> bool:
    return (
        integrity_ready(evidence)
        and _all_true(evidence, STRONG_BOOLEAN_GATES)
        and _all_zero(evidence, (
            "baseline_adapter_conformance_failures",
            "benchmark_identifier_branches_in_forge",
            "holdout_specific_prompt_count",
            "hidden_answer_literal_count",
            "invalid_or_missing_primary_runs",
        ))
    )


def clean_falsification_ready(evidence: Mapping[str, Any]) -> bool:
    q_statuses = [evidence.get(f"q{i}_status") for i in range(1, 5)]
    if any(status not in _Q_STATUSES for status in q_statuses):
        return False
    one_clean_negative = "clean_negative" in q_statuses
    no_inconclusive = all(status != "inconclusive" for status in q_statuses)
    post_unblinding_changes = evidence.get("post_unblinding_changes")
    invalid_primary_runs = evidence.get("invalid_or_missing_primary_runs")
    exact_zero = lambda value: (
        isinstance(value, int) and not isinstance(value, bool) and value == 0
    )
    return (
        integrity_ready(evidence)
        and evidence.get("primary_and_required_extension_complete") is True
        and one_clean_negative
        and no_inconclusive
        and exact_zero(post_unblinding_changes)
        and exact_zero(invalid_primary_runs)
    )


def final_verdict(evidence: Mapping[str, Any]) -> str:
    """Compute exactly one V3 terminal state from explicit evidence."""
    if not isinstance(evidence, Mapping):
        raise ProtocolError("verdict evidence must be a mapping")
    integrity = integrity_ready(evidence)
    strong = strong_method_ready(evidence)
    clean = clean_falsification_ready(evidence)
    return research_verdict(
        integrity_ready=integrity,
        strong_method_ready=strong,
        clean_falsification_ready=clean,
        integrity_failure=evidence.get("integrity_failure") is True,
    )
