import copy

import pytest

from forge.protocol import ProtocolError, load_protocol
from forge.result_schema import (
    GPU_FRACTIONS,
    NATIVE_GPU_SECONDS,
    result_identity_sha256,
    validate_gpu_auc_result,
    validate_result_identity,
)


def _identity():
    value = {
        "study_id": "study-v3",
        "study_version": "a" * 64,
        "run_id": "study-v3/forge/h00/SMALL/101",
        "method_id": "FORGE",
        "problem_id": "h00",
        "problem_family": "f0",
        "distribution": "iid_heldout",
        "model_tier": "SMALL",
        "seed": 101,
        "seed_role": "primary",
        "track": "SAME_MODEL",
    }
    value["run_identity_sha256"] = result_identity_sha256(value)
    return value


def test_result_identity_rejects_hash_or_seed_tampering():
    result = _identity()
    validate_result_identity(result)
    result["seed"] = 113
    with pytest.raises(ProtocolError):
        validate_result_identity(result)


def test_same_model_gpu_auc_requires_explicit_not_applicable():
    result = {**_identity(), "native_a100_gpu_seconds_cap": 3600,
              "gpu_auc_status": "not_applicable", "gpu_anytime_curve": [],
              "auc_gpu": None}
    validate_gpu_auc_result(result)
    result["gpu_auc_status"] = "pending_unblinding"
    with pytest.raises(ProtocolError):
        validate_gpu_auc_result(result)


def test_native_gpu_auc_curve_is_exactly_preregistered():
    result = {**_identity(), "track": "NATIVE_COMPUTE",
              "native_a100_gpu_seconds_cap": int(NATIVE_GPU_SECONDS),
              "gpu_auc_status": "complete",
              "native_gpu_seconds_observed": NATIVE_GPU_SECONDS,
              "native_model_forward_time_ms_observed": 100.0}
    result["gpu_anytime_curve"] = [
        {
            "fraction": fraction,
            "gpu_seconds": fraction * NATIVE_GPU_SECONDS,
            "observed_gpu_seconds": fraction * NATIVE_GPU_SECONDS,
            "candidate_sha256": "b" * 64,
            "hidden_test_normalized_quality": fraction,
        }
        for fraction in GPU_FRACTIONS
    ]
    result["auc_gpu"] = sum(GPU_FRACTIONS) / len(GPU_FRACTIONS)
    validate_gpu_auc_result(result)
    result["gpu_anytime_curve"][0]["gpu_seconds"] += 1
    with pytest.raises(ProtocolError):
        validate_gpu_auc_result(result)


def test_native_gpu_curve_requires_measured_endpoint_and_monotonicity():
    result = {**_identity(), "track": "NATIVE_COMPUTE",
              "native_a100_gpu_seconds_cap": int(NATIVE_GPU_SECONDS),
              "gpu_auc_status": "complete",
              "native_gpu_seconds_observed": NATIVE_GPU_SECONDS,
              "native_model_forward_time_ms_observed": 100.0,
              "gpu_anytime_curve": [
                  {
                      "fraction": fraction,
                      "gpu_seconds": fraction * NATIVE_GPU_SECONDS,
                      "observed_gpu_seconds": fraction * NATIVE_GPU_SECONDS,
                      "candidate_sha256": "b" * 64,
                      "hidden_test_normalized_quality": fraction,
                  }
                  for fraction in GPU_FRACTIONS
              ]}
    result["auc_gpu"] = sum(GPU_FRACTIONS) / len(GPU_FRACTIONS)
    validate_gpu_auc_result(result)
    result["gpu_anytime_curve"][-1]["observed_gpu_seconds"] -= 1
    with pytest.raises(ProtocolError):
        validate_gpu_auc_result(result)


def test_result_gpu_schema_is_bound_to_validated_protocol_contract():
    result = {**_identity(), "native_a100_gpu_seconds_cap": 3600,
              "gpu_auc_status": "not_applicable", "gpu_anytime_curve": [],
              "auc_gpu": None}
    protocol = load_protocol()
    validate_gpu_auc_result(result, protocol_spec=protocol)

    mutated = copy.deepcopy(protocol)
    mutated["metrics"]["native_gpu_fractions"] = [0.1, 1.0]
    with pytest.raises(ProtocolError, match="GPU metric contract"):
        validate_gpu_auc_result(result, protocol_spec=mutated)
