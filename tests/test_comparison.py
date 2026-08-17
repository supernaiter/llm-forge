import json
from pathlib import Path

import pytest

from forge.comparison import (
    ATTEMPT_CAP,
    EVALUATOR_BUDGET,
    METHOD_ORDER,
    matched_attempt_metrics,
    run_method_comparison,
    validate_comparison_bundle,
)
from forge.controller import SearchAction, controller_for_mechanism, write_controller_manifest
from forge.development import DevelopmentProblem
from forge.protocol import ProtocolError


def _write_test_policies(root: Path) -> Path:
    policies = root / "policies"
    actions = [
        SearchAction("SMALL", "elite", "local", 1, 0, "uniform"),
        SearchAction("STRONG", "diverse", "structural", 2, 1, "score_spread"),
    ]
    for mechanism in ("TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V2", "FIXED_DEV_BEST"):
        controller = controller_for_mechanism(mechanism, actions)
        controller.fit([
            {
                "split": "dev",
                "problem_id": "dev_fixture",
                "action": action.__dict__,
                "quality_gain": float(index + 1),
                "cost": 1.0,
            }
            for index, action in enumerate(actions)
        ])
        controller.freeze()
        write_controller_manifest(
            controller,
            policies / f"{mechanism}.json",
            source_traces_sha256="a" * 64,
            manifest_id=f"TEST_{mechanism}",
        )
    return policies


def _events_with_curve(statuses=("valid_candidate",) * 4):
    events = []
    for index, status in enumerate(statuses, 1):
        attempt_id = f"run:attempt:{index}"
        events.extend([
            {
                "event_type": "attempt_finished",
                "payload": {
                    "attempt_id": attempt_id,
                    "status": status,
                    "evaluator_resource_usage": {
                        "evaluator_calls": 1,
                    },
                },
            },
            {
                "event_type": "incumbent_selected",
                "payload": {
                    "attempt_id": attempt_id,
                    "after_attempt": index,
                    "score": float(index),
                },
            },
        ])
    return events


def test_matched_attempt_metrics_include_failed_slots_in_fixed_auc():
    metrics = matched_attempt_metrics(
        _events_with_curve(("valid_candidate", "runtime_error", "valid_candidate", "valid_candidate")),
        attempt_cap=ATTEMPT_CAP,
        evaluator_budget=EVALUATOR_BUDGET,
    )
    assert metrics["auc_by_generation"] == 2.5
    assert metrics["incumbent_curve"] == [1.0, 2.0, 3.0, 4.0]
    assert metrics["failure_count"] == 1
    assert metrics["evaluator_calls"] == 4


def test_matched_attempt_metrics_reject_missing_checkpoint():
    events = _events_with_curve()[:-1]
    with pytest.raises(ProtocolError, match="finished attempts/checkpoints"):
        matched_attempt_metrics(events, attempt_cap=ATTEMPT_CAP, evaluator_budget=EVALUATOR_BUDGET)


def test_method_comparison_emits_all_cells_and_auditable_receipt(tmp_path):
    policies = _write_test_policies(tmp_path)
    output = tmp_path / "comparison"
    summary = run_method_comparison(
        [DevelopmentProblem("probe", Path("projects/_probe_newproblem"))],
        output,
        seeds=(0,),
        policy_dir=policies,
        scale="mock",
    )

    assert summary["fairness_pass"] is True
    assert len(summary["rows"]) == len(METHOD_ORDER)
    receipt = json.loads((output / "fairness_receipt.json").read_text())
    assert receipt["fairness_pass"] is True
    assert validate_comparison_bundle(output)["cell_count"] == len(METHOD_ORDER)

    rows = [json.loads(line) for line in (output / "comparison_results.jsonl").read_text().splitlines()]
    assert {row["method_id"] for row in rows} == set(METHOD_ORDER)
    assert all(row["attempt_count"] == ATTEMPT_CAP for row in rows)
    assert all(len(row["incumbent_curve"]) == ATTEMPT_CAP for row in rows)
    assert all(row["evaluator_budget"] == EVALUATOR_BUDGET for row in rows)

    tampered = output / "runs" / METHOD_ORDER[0] / "probe" / "seed-0" / "result.json"
    tampered.write_text(tampered.read_text() + "\n", encoding="utf-8")
    with pytest.raises(ProtocolError, match="result hash mismatch"):
        validate_comparison_bundle(output)
