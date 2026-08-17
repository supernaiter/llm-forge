import json
import subprocess
import sys
from pathlib import Path

import pytest

from forge.ledger import EventLedger, candidate_sha256
from forge.lineage import lineage_metadata
from forge.resources import generation_usage
from forge.controller import SearchState, load_controller_manifest
from forge.protocol import ProtocolError
from tools.collect_controller_traces import collect_traces


def test_collect_controller_traces_recomputes_dev_gain_and_cost(tmp_path):
    events = tmp_path / "events.jsonl"
    ledger = EventLedger(events, run_id="dev-trace", max_attempts=2)
    actions = [
        {
            "generator_model": "SMALL@sha256:" + "a" * 64,
            "parent_selection_policy": "elite",
            "mutation_operator": "local",
            "number_of_offspring": 1,
            "reflection_depth": 0,
            "archive_sampling_policy": "uniform",
        },
        {
            "generator_model": "STRONG@sha256:" + "b" * 64,
            "parent_selection_policy": "diverse",
            "mutation_operator": "structural",
            "number_of_offspring": 1,
            "reflection_depth": 1,
            "archive_sampling_policy": "score_spread",
        },
    ]
    for generation, (action, score, wall_time_ms) in enumerate(
        zip(actions, (1.0, 1.5), (10.0, 20.0)), 1
    ):
        attempt = ledger.start_attempt(
            generation=generation,
            slot=0,
            model=action["generator_model"],
            track="SAME_MODEL",
            metadata={
                "controller_action": action,
                "controller_state": {"remaining_budget": 2},
                "generation_baseline_score": 0.9 if generation == 1 else 1.0,
                "generation_mode": "llm",
            },
        )
        candidate = f"candidate-{generation}"
        ledger.finish_attempt(
            attempt,
            status="valid_candidate",
            candidate_hash=candidate_sha256(candidate),
            score=score,
            resource_usage=generation_usage(
                wall_time_ms=wall_time_ms,
                model_identity=action["generator_model"],
            ),
            metadata={
                **lineage_metadata(candidate, []),
                "evaluator_hack_audit": {
                    "parseable": True,
                    "suspected_hack": False,
                    "findings": [],
                },
            },
        )
        ledger.record_event("incumbent_selected", {
            "attempt_id": attempt,
            "after_attempt": generation,
            "candidate_sha256": candidate_sha256(candidate),
            "score": score,
        })
    ledger.assert_invariants(require_checkpoints=True)

    traces = collect_traces(events, problem_id="obp_dev_v1")
    assert [trace["generation"] for trace in traces] == [1, 2]
    assert [trace["quality_gain"] for trace in traces] == pytest.approx([0.1, 0.5])
    assert [trace["cost"] for trace in traces] == [0.01, 0.02]
    assert all(trace["split"] == "dev" for trace in traces)
    assert traces[1]["action"] == actions[1]
    assert traces[0]["state"] == {"remaining_budget": 2}

    trace_path = tmp_path / "development_traces.jsonl"
    collect_proc = subprocess.run(
        [
            sys.executable,
            "tools/collect_controller_traces.py",
            "--events", str(events),
            "--problem-id", "obp_dev_v1",
            "--out", str(trace_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )
    assert collect_proc.returncode == 0, collect_proc.stderr
    trace_lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert trace_lines and all(line.strip() for line in trace_lines)
    assert all(isinstance(json.loads(line), dict) for line in trace_lines)
    action_path = tmp_path / "actions.json"
    action_path.write_text(json.dumps(actions, sort_keys=True) + "\n")
    policy_path = tmp_path / "controller_policy.json"
    proc = subprocess.run(
        [
            sys.executable,
            "tools/freeze_controller.py",
            "--traces", str(trace_path),
            "--actions", str(action_path),
            "--out", str(policy_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    policy = load_controller_manifest(policy_path)
    assert policy.frozen is True
    assert policy.training_problem_ids == ("obp_dev_v1",)
    assert policy.choose(
        SearchState(10, 0.0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0)
    ).generator_model == actions[1]["generator_model"]


def test_collect_controller_traces_uses_deterministic_mock_output_cost(tmp_path):
    events = tmp_path / "mock-events.jsonl"
    ledger = EventLedger(events, run_id="mock-trace", max_attempts=1)
    action = {
        "generator_model": "MOCK",
        "parent_selection_policy": "elite",
        "mutation_operator": "local",
        "number_of_offspring": 1,
        "reflection_depth": 0,
        "archive_sampling_policy": "uniform",
    }
    attempt = ledger.start_attempt(
        generation=1,
        slot=0,
        model="MOCK",
        track="SAME_MODEL",
        metadata={
            "controller_action": action,
            "controller_state": {"remaining_budget": 1},
            "generation_baseline_score": 0.0,
        },
    )
    candidate = "mock candidate"
    ledger.finish_attempt(
        attempt,
        status="valid_candidate",
        candidate_hash=candidate_sha256(candidate),
        score=1.0,
        # The wall-clock value is intentionally arbitrary; mock output tokens
        # are the deterministic fit cost.
        resource_usage=generation_usage(
            wall_time_ms=999.0,
            model_identity="MOCK",
            input_tokens=500,
            output_tokens=7,
            notes=["mock_observed_token_counts"],
        ),
        metadata={
            **lineage_metadata(candidate, []),
            "evaluator_hack_audit": {
                "parseable": True,
                "suspected_hack": False,
                "findings": [],
            },
        },
    )
    ledger.record_event("incumbent_selected", {
        "attempt_id": attempt,
        "after_attempt": 1,
        "candidate_sha256": candidate_sha256(candidate),
        "score": 1.0,
    })
    ledger.assert_invariants(require_checkpoints=True)

    traces = collect_traces(events, problem_id="mock_dev_v1")
    assert traces[0]["quality_gain"] == 1.0
    assert traces[0]["cost"] == 7.0


def test_collect_controller_traces_rejects_legacy_ledger_without_actions(tmp_path):
    events = tmp_path / "events.jsonl"
    ledger = EventLedger(events, run_id="legacy", max_attempts=1)
    attempt = ledger.start_attempt(generation=1, slot=0, model="SMALL", track="SAME_MODEL")
    ledger.finish_attempt(attempt, status="empty_response")
    ledger.record_event("incumbent_selected", {
        "attempt_id": attempt,
        "after_attempt": 1,
        "candidate_sha256": "a" * 64,
        "score": 0.0,
    })
    ledger.assert_invariants(require_checkpoints=True)

    with pytest.raises(ProtocolError, match="controller_action"):
        collect_traces(events, problem_id="obp_dev_v1")


def test_collect_controller_traces_rejects_non_object_controller_state(tmp_path):
    events = tmp_path / "events.jsonl"
    ledger = EventLedger(events, run_id="bad-state", max_attempts=1)
    attempt = ledger.start_attempt(
        generation=1,
        slot=0,
        model="SMALL",
        track="SAME_MODEL",
        metadata={
            "controller_action": {
                "generator_model": "SMALL",
                "parent_selection_policy": "elite",
                "mutation_operator": "local",
                "number_of_offspring": 1,
                "reflection_depth": 0,
                "archive_sampling_policy": "uniform",
            },
            "controller_state": "not-an-object",
        },
    )
    ledger.finish_attempt(
        attempt,
        status="empty_response",
        resource_usage=generation_usage(
            wall_time_ms=1.0,
            model_identity="SMALL",
        ),
    )
    ledger.record_event("incumbent_selected", {
        "attempt_id": attempt,
        "after_attempt": 1,
        "candidate_sha256": candidate_sha256("seed"),
        "score": 0.0,
    })
    with pytest.raises(ProtocolError, match="controller_state"):
        collect_traces(events, problem_id="obp_dev_v1")


def test_collect_controller_traces_rejects_nonfinite_gain_overflow(tmp_path):
    events = tmp_path / "events.jsonl"
    ledger = EventLedger(events, run_id="overflow-gain", max_attempts=2)
    action = {
        "generator_model": "SMALL@sha256:" + "c" * 64,
        "parent_selection_policy": "elite",
        "mutation_operator": "local",
        "number_of_offspring": 1,
        "reflection_depth": 0,
        "archive_sampling_policy": "uniform",
    }
    previous = None
    for generation, score in enumerate((-1e308, 1e308), 1):
        attempt = ledger.start_attempt(
            generation=generation,
            slot=0,
            model=action["generator_model"],
            track="SAME_MODEL",
            metadata={
                "controller_action": action,
                "controller_state": {"remaining_budget": 2},
            },
        )
        candidate = f"overflow-{generation}"
        ledger.finish_attempt(
            attempt,
            status="valid_candidate",
            candidate_hash=candidate_sha256(candidate),
            score=score,
            resource_usage=generation_usage(
                wall_time_ms=1.0,
                model_identity=action["generator_model"],
            ),
            metadata={
                **lineage_metadata(candidate, []),
                "evaluator_hack_audit": {
                    "parseable": True,
                    "suspected_hack": False,
                    "findings": [],
                },
            },
        )
        ledger.record_event("incumbent_selected", {
            "attempt_id": attempt,
            "after_attempt": generation,
            "candidate_sha256": candidate_sha256(candidate),
            "score": score,
        })
        previous = score
    ledger.assert_invariants(require_checkpoints=True)
    with pytest.raises(ProtocolError, match="non-finite"):
        collect_traces(events, problem_id="obp_dev_v1")
