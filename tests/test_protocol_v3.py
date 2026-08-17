import json

import pytest

from forge.protocol import (
    PROTOCOL_PATH,
    ProtocolError,
    V3_SAME_MODEL_ATTEMPT_CAP,
    load_protocol,
    research_verdict,
    strict_json_loads,
    validate_evidence,
    validate_protocol,
)


def test_v3_protocol_is_machine_readable_and_frozen_constants_are_present():
    spec = load_protocol()
    assert PROTOCOL_PATH.is_file()
    assert spec["primary_thesis"] == "TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V1"
    assert spec["budgets"]["same_model_attempts"] == 512
    assert spec["holdout"]["problem_count"] == 10
    assert len(spec["requirements"]) >= 5
    assert V3_SAME_MODEL_ATTEMPT_CAP == spec["budgets"]["same_model_attempts"]


def test_strict_json_loader_rejects_nonfinite_constants():
    for constant in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(ValueError, match="non-finite JSON constant"):
            strict_json_loads('{"value": ' + constant + '}')


def test_protocol_validation_fails_closed_on_terminal_state_change():
    spec = load_protocol()
    spec["terminal_states"] = ["STRONG_POSITIVE"]
    with pytest.raises(ProtocolError):
        validate_protocol(spec)


def test_protocol_validation_freezes_primary_thesis():
    spec = load_protocol()
    spec["primary_thesis"] = "OTHER_CONTROLLER"
    with pytest.raises(ProtocolError, match="primary thesis"):
        validate_protocol(spec)


def test_protocol_validation_freezes_every_budget_cap():
    spec = load_protocol()
    spec["budgets"]["native_max_attempts"] = 2047
    with pytest.raises(ProtocolError):
        validate_protocol(spec)


def test_protocol_validation_freezes_holdout_seed_and_bootstrap_contracts():
    spec = load_protocol()

    mutated = json.loads(json.dumps(spec))
    mutated["holdout"]["hidden_test_instances_min_per_problem"] = 499
    with pytest.raises(ProtocolError, match="holdout field"):
        validate_protocol(mutated)

    mutated = json.loads(json.dumps(spec))
    mutated["seeds"]["primary"][0] = 100
    with pytest.raises(ProtocolError, match="primary seed"):
        validate_protocol(mutated)

    mutated = json.loads(json.dumps(spec))
    mutated["statistics"]["seed"] += 1
    with pytest.raises(ProtocolError, match="bootstrap contract"):
        validate_protocol(mutated)

    mutated = json.loads(json.dumps(spec))
    mutated["budgets"]["same_model_attempts"] = 512.0
    with pytest.raises(ProtocolError, match="protocol field must be an integer"):
        validate_protocol(mutated)

    mutated = json.loads(json.dumps(spec))
    mutated["metrics"]["native_gpu_fractions"] = [0.1, 1.0]
    with pytest.raises(ProtocolError, match="metric contract"):
        validate_protocol(mutated)


def test_protocol_validation_reports_malformed_sections_as_protocol_errors():
    spec = load_protocol()
    for field in (
        "development_problems", "terminal_states", "models", "tracks", "holdout",
        "seeds", "budgets", "baselines", "ablations", "statistics", "metrics",
        "requirements",
    ):
        mutated = json.loads(json.dumps(spec))
        mutated[field] = None
        with pytest.raises(ProtocolError):
            validate_protocol(mutated)


def test_protocol_requires_machine_readable_positive_gate_thresholds():
    spec = load_protocol()
    spec["metrics"].pop("positive_gate_thresholds")
    with pytest.raises(ProtocolError):
        validate_protocol(spec)


def test_protocol_binds_positive_gate_fields_operators_and_thresholds():
    spec = load_protocol()
    contract = spec["metrics"]["positive_gate_contract"]
    assert set(contract) == {
        "same_model_superiority_ready",
        "final_quality_ready",
        "ood_generalization_ready",
        "compute_efficiency_ready",
        "primary_mechanism_validated",
        "replication_ready",
    }
    assert contract["same_model_superiority_ready"]["overall_delta_oracle_mean"] == {
        "operator": "ge",
        "threshold": 0.03,
    }

    mutated = json.loads(json.dumps(spec))
    mutated["metrics"]["positive_gate_contract"][
        "same_model_superiority_ready"
    ]["overall_delta_oracle_mean"]["operator"] = "between"
    with pytest.raises(ProtocolError, match="invalid positive metric gate operator"):
        validate_protocol(mutated)

    mutated = json.loads(json.dumps(spec))
    mutated["metrics"]["positive_gate_contract"][
        "same_model_superiority_ready"
    ]["overall_delta_oracle_mean"]["threshold"] = 0.031
    with pytest.raises(ProtocolError, match="threshold mismatch"):
        validate_protocol(mutated)

    mutated = json.loads(json.dumps(spec))
    mutated["metrics"]["positive_gate_contract"][
        "compute_efficiency_ready"
    ]["same_model_pool_for_all_methods"]["threshold_key"] = "missing"
    with pytest.raises(ProtocolError, match="threshold key is missing"):
        validate_protocol(mutated)


def test_research_verdict_has_integrity_failure_precedence():
    assert research_verdict(integrity_ready=False, strong_method_ready=True) == \
        "BLOCKED_INTEGRITY_FAILURE"
    assert research_verdict(integrity_ready=True, strong_method_ready=True) == \
        "STRONG_POSITIVE"
    assert research_verdict(integrity_ready=True, clean_falsification_ready=True) == \
        "CLEAN_FALSIFICATION"
    assert research_verdict(integrity_ready=True) == "INCONCLUSIVE"


def test_verdict_evidence_requires_explicit_booleans():
    validate_evidence({
        "integrity_ready": True,
        "strong_method_ready": False,
        "clean_falsification_ready": False,
    })
    with pytest.raises(ProtocolError):
        validate_evidence({"integrity_ready": True})
    with pytest.raises(ProtocolError):
        validate_evidence({
            "integrity_ready": 1,
            "strong_method_ready": False,
            "clean_falsification_ready": False,
        })
