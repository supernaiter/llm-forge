import json

import pytest

from forge.ledger import EventLedger, LedgerError
from forge.resources import (
    ResourceError,
    ResourceLedger,
    evaluator_usage,
    generation_usage,
    normalize_usage,
)
from forge.replay import replay_summary


def test_missing_telemetry_is_explicit_and_not_zero():
    usage = normalize_usage()
    assert usage["input_tokens"] is None
    assert "input_tokens" in usage["missing"]
    assert usage["telemetry_complete"] is False


def test_estimated_telemetry_is_rejected():
    with pytest.raises(ResourceError):
        normalize_usage({"input_tokens": 10, "estimated": True})


def test_floating_model_identity_is_rejected():
    with pytest.raises(ResourceError):
        normalize_usage({"model_identity": "latest"})


def test_generation_and_evaluator_budgets_are_separate_and_replayed(tmp_path):
    path = tmp_path / "events.jsonl"
    ledger = EventLedger(
        path,
        run_id="run",
        max_attempts=1,
        resource_budgets={
            "generation": {"records": 1, "input_tokens": 100},
            "evaluator": {"records": 1},
        },
    )
    attempt_id = ledger.start_attempt(generation=1, slot=0, model="SMALL")
    ledger.finish_attempt(
        attempt_id,
        status="valid_candidate",
        resource_usage=generation_usage(
            input_tokens=10,
            output_tokens=20,
            model_identity="small@sha256:abc",
            sampling_profile={"temperature": 0.2},
            wall_time_ms=4.0,
            gpu_allocation={"device_type": "A100", "count": 1, "seconds": 2.0},
        ),
    )
    ledger.record_evaluation(
        attempt_id,
        resource_usage=evaluator_usage(
            wall_time_ms=2.0,
            evaluator_cost=0.002,
            evaluator_id="evaluator@sha256:def",
        ),
    )
    ledger.record_event("incumbent_selected", {
        "attempt_id": attempt_id,
        "after_attempt": 1,
        "candidate_sha256": "a" * 64,
        "score": 1.0,
    })
    ledger.assert_invariants()
    summary = ledger.resource_summary()
    assert summary["phases"]["generation"]["totals"]["input_tokens"] == 10
    assert summary["phases"]["generation"]["totals"]["gpu_seconds"] == 2.0
    assert summary["phases"]["evaluator"]["totals"]["evaluator_cost"] == 0.002
    replay = replay_summary(path)
    assert replay["resource_ledger_valid"] is True
    assert replay["resource_summary"] == summary


def test_resource_budget_exceedance_fails_closed(tmp_path):
    path = tmp_path / "events.jsonl"
    ledger = EventLedger(
        path,
        run_id="run",
        max_attempts=1,
        resource_budgets={"generation": {"input_tokens": 1}},
    )
    attempt_id = ledger.start_attempt(generation=1, slot=0, model="SMALL")
    with pytest.raises(LedgerError):
        ledger.finish_attempt(
            attempt_id,
            status="valid_candidate",
            resource_usage=generation_usage(
                input_tokens=2,
                model_identity="small",
                sampling_profile={"temperature": 0.2},
                wall_time_ms=1.0,
            ),
        )


def test_resource_ledger_rejects_duplicate_phase_attempt():
    ledger = ResourceLedger()
    usage = evaluator_usage(wall_time_ms=1.0, evaluator_cost=0.001, evaluator_id="e")
    ledger.record(phase="evaluator", attempt_id="a", usage=usage)
    with pytest.raises(ResourceError):
        ledger.record(phase="evaluator", attempt_id="a", usage=usage)


def test_native_track_requires_gpu_and_forward_telemetry(tmp_path):
    ledger = EventLedger(tmp_path / "events.jsonl", run_id="native", max_attempts=1)
    attempt_id = ledger.start_attempt(
        generation=1, slot=0, model="STRONG", track="NATIVE_COMPUTE"
    )
    with pytest.raises(LedgerError):
        ledger.finish_attempt(
            attempt_id,
            status="valid_candidate",
            resource_usage=generation_usage(
                input_tokens=1,
                output_tokens=1,
                model_identity="strong",
                sampling_profile={"temperature": 0.0},
                wall_time_ms=1.0,
            ),
        )


def test_native_gpu_budget_exceedance_fails_closed(tmp_path):
    ledger = EventLedger(
        tmp_path / "events.jsonl",
        run_id="native-cap",
        max_attempts=1,
        resource_budgets={"generation": {"gpu_seconds": 1.0}},
    )
    attempt_id = ledger.start_attempt(
        generation=1, slot=0, model="STRONG", track="NATIVE_COMPUTE"
    )
    with pytest.raises(LedgerError, match="generation.gpu_seconds"):
        ledger.finish_attempt(
            attempt_id,
            status="valid_candidate",
            resource_usage=generation_usage(
                input_tokens=1,
                output_tokens=1,
                model_identity="strong",
                sampling_profile={"temperature": 0.0},
                wall_time_ms=1.0,
                gpu_allocation={"device_type": "A100", "count": 1, "seconds": 2.0},
                model_forward_time_ms=1.0,
            ),
        )
