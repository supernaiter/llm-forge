import pytest

from forge.protocol import ProtocolError
from forge.verdict import (
    INTEGRITY_BOOLEAN_FLAGS,
    INTEGRITY_ZERO_FIELDS,
    STRONG_BOOLEAN_GATES,
    clean_falsification_ready,
    derive_q_statuses,
    final_verdict,
    integrity_ready,
    strong_method_ready,
    validate_metric_gate_claims,
)


def _integrity():
    return {
        **{field: True for field in INTEGRITY_BOOLEAN_FLAGS},
        **{field: 0 for field in INTEGRITY_ZERO_FIELDS},
    }


def test_strong_method_verdict_requires_every_gate():
    evidence = {
        **_integrity(),
        **{field: True for field in STRONG_BOOLEAN_GATES},
    }
    assert integrity_ready(evidence) is True
    assert strong_method_ready(evidence) is True
    assert final_verdict(evidence) == "STRONG_POSITIVE"

    evidence["budget_violation_count"] = 1
    assert final_verdict(evidence) == "BLOCKED_INTEGRITY_FAILURE"


def test_integrity_zero_fields_reject_boolean_false():
    evidence = _integrity()
    evidence["budget_violation_count"] = False
    assert integrity_ready(evidence) is False


def test_integrity_requires_holdout_family_structure_attestation():
    evidence = _integrity()
    evidence.pop("heldout_problem_family_requirements_pass")
    assert integrity_ready(evidence) is False


def test_clean_falsification_is_a_terminal_research_result():
    evidence = {
        **_integrity(),
        "primary_and_required_extension_complete": True,
        "q1_status": "clean_negative",
        "q2_status": "strong_positive",
        "q3_status": "strong_positive",
        "q4_status": "strong_positive",
        "post_unblinding_changes": 0,
    }
    assert clean_falsification_ready(evidence) is True
    assert final_verdict(evidence) == "CLEAN_FALSIFICATION"


def test_clean_falsification_rejects_unknown_q_status_and_boolean_zero():
    evidence = {
        **_integrity(),
        "primary_and_required_extension_complete": True,
        "q1_status": "clean_negative",
        "q2_status": "strong_positive",
        "q3_status": "strong_positive",
        "q4_status": "not-a-registered-status",
        "post_unblinding_changes": 0,
    }
    assert clean_falsification_ready(evidence) is False
    evidence["q4_status"] = "strong_positive"
    evidence["post_unblinding_changes"] = False
    assert clean_falsification_ready(evidence) is False


def test_missing_evidence_is_inconclusive_or_blocked_not_positive():
    assert final_verdict({}) == "BLOCKED_INTEGRITY_FAILURE"
    evidence = _integrity()
    evidence["q1_status"] = "inconclusive"
    evidence["q2_status"] = "inconclusive"
    evidence["q3_status"] = "inconclusive"
    evidence["q4_status"] = "inconclusive"
    evidence["primary_and_required_extension_complete"] = True
    evidence["post_unblinding_changes"] = 0
    assert final_verdict(evidence) == "INCONCLUSIVE"


def test_q_status_derivation_uses_strict_preregistered_thresholds():
    metrics = {
        "same_model_superiority_ready": False,
        "overall_delta_oracle_95ci_high": 0.030,
        "compute_efficiency_ready": False,
        "overall_delta_gpu_oracle_95ci_high": 0.029,
        "primary_mechanism_validated": False,
        "delta_fixed_95ci_high": 0.015,
        "delta_transfer_95ci_high": 0.009,
        "delta_cost_95ci_high": 0.020,
        "ood_generalization_ready": True,
        "overall_delta_ood_95ci_high": 0.000,
    }
    assert derive_q_statuses(metrics) == {
        "q1_status": "inconclusive",
        "q2_status": "clean_negative",
        "q3_status": "clean_negative",
        "q4_status": "strong_positive",
    }


def test_q_status_derivation_rejects_missing_or_nonfinite_ci():
    with pytest.raises(ProtocolError):
        derive_q_statuses({})
    metrics = {
        "same_model_superiority_ready": False,
        "overall_delta_oracle_95ci_high": float("nan"),
    }
    with pytest.raises(ProtocolError):
        derive_q_statuses(metrics)


def test_positive_metric_gate_requires_preregistered_numeric_attestations():
    metrics = {field: False for field in STRONG_BOOLEAN_GATES}
    metrics["same_model_superiority_ready"] = True
    with pytest.raises(ProtocolError, match="finite value"):
        validate_metric_gate_claims(metrics)

    metrics.update({
        "min_model_delta_oracle_mean": 0.025,
        "min_family_delta_oracle_mean": 0.020,
        "min_distribution_delta_oracle_mean": 0.020,
        "overall_delta_oracle_mean": 0.030,
        "overall_delta_oracle_95ci_low": 0.015,
        "overall_delta_champion_mean": 0.050,
        "overall_delta_champion_95ci_low": 0.030,
        "min_model_delta_oracle_95ci_low": 0.005,
        "min_family_delta_oracle_95ci_low": 0.000,
        "severe_regression_cell_rate": 0.050,
        "min_cell_delta_oracle": -0.100,
        "heldout_problem_win_rate_vs_oracle": 0.750,
        "cell_win_rate_vs_oracle": 0.650,
    })
    validate_metric_gate_claims(metrics)
    metrics["overall_delta_oracle_mean"] = 0.029999
    with pytest.raises(ProtocolError, match="threshold failed"):
        validate_metric_gate_claims(metrics)
    metrics.update({
        "compute_efficiency_ready": False,
        "overall_delta_gpu_oracle_95ci_high": 0.0,
        "primary_mechanism_validated": True,
        "ood_generalization_ready": False,
        "overall_delta_ood_95ci_high": 0.0,
    })
    with pytest.raises(ProtocolError):
        derive_q_statuses(metrics)


def test_replication_gate_requires_independent_replay_attestation():
    metrics = {field: False for field in STRONG_BOOLEAN_GATES}
    metrics["replication_ready"] = True
    with pytest.raises(ProtocolError, match="finite value"):
        validate_metric_gate_claims(metrics)

    metrics.update({
        "primary_seed_count": 12,
        "independent_model_profiles": 3,
        "strongest_model_effect_sign": 0.001,
        "medium_model_effect_sign": 0.001,
        "small_model_effect_sign": 0.001,
        "independent_replay_runs": 100,
        "replay_decision_hash_mismatches": 0,
    })
    validate_metric_gate_claims(metrics)
    metrics["independent_replay_runs"] = 99
    with pytest.raises(ProtocolError, match="threshold failed"):
        validate_metric_gate_claims(metrics)
    metrics["independent_replay_runs"] = 100
    metrics["replay_decision_hash_mismatches"] = -1
    with pytest.raises(ProtocolError, match="threshold failed"):
        validate_metric_gate_claims(metrics)
