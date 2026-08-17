import json
from pathlib import Path

import pytest

from forge.controller import SearchAction
from forge.development import DevelopmentProblem
from forge.loo import (
    FIXED_MECHANISM,
    PRIMARY_MECHANISM,
    run_leave_one_problem_out_matrix,
)
from forge.protocol import ProtocolError
from forge.replay import replay_summary


def _actions():
    return [
        SearchAction("SMALL", "elite", "local", 1, 0, "uniform"),
        SearchAction("STRONG", "diverse", "structural", 2, 1, "score_spread"),
    ]


def _problems():
    return [
        DevelopmentProblem("stringmax", Path("projects/stringmax")),
        DevelopmentProblem("probe", Path("projects/_probe_newproblem")),
    ]


def test_leave_one_problem_out_excludes_target_and_meets_transfer_gate(tmp_path):
    summary = run_leave_one_problem_out_matrix(
        _problems(),
        _actions(),
        tmp_path / "loo",
        generations=3,
        max_attempts=4,
        seeds=(0, 1, 2),
        mechanisms=(PRIMARY_MECHANISM, FIXED_MECHANISM),
    )

    assert summary["evaluation_design"] == "leave_one_problem_out"
    assert summary["model"] == "mock"
    assert summary["track"] == "SAME_MODEL"
    assert summary["seeds"] == [0, 1, 2]
    assert summary["attempt_cap"] == 4
    assert summary["audit"] == {
        "target_trace_exclusion_failures": 0,
        "target_trace_rows_used_for_fit": 0,
        "replay_hash_mismatch_count": 0,
        "attempt_cap_failures": 0,
        "resource_ledger_invalid_count": 0,
    }
    assert summary["aggregate_comparison"]["auc_gate"] is True
    assert summary["aggregate_comparison"]["all_target_auc_gates"] is True
    assert summary["aggregate_comparison"]["all_target_best_score_gates"] is True

    problem_ids = {problem.problem_id for problem in _problems()}
    for target_problem_id, fold in summary["folds"].items():
        assert target_problem_id not in fold["fit_problem_ids"]
        assert set(fold["fit_problem_ids"]) == problem_ids - {target_problem_id}
        assert fold["fit_trace_count"] > 0
        assert fold["target_trace_count_available"] > 0
        assert fold["target_trace_count_used"] == 0
        assert fold["target_trace_excluded"] is True

    assert len(summary["policy_runs"]) == 12
    assert all(row["attempt_count"] == 4 for row in summary["policy_runs"])
    assert all(row["target_trace_count_used"] == 0 for row in summary["policy_runs"])
    assert all(row["target_trace_excluded"] is True for row in summary["policy_runs"])
    assert all(
        len(row[field]) == 64
        for row in summary["policy_runs"]
        for field in ("fit_trace_sha256", "decision_hash", "result_recomputation_hash")
    )
    for row in summary["policy_runs"]:
        run_dir = Path(row["run_dir"])
        result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
        replay = replay_summary(run_dir / "events.jsonl")
        assert row["decision_hash"] == result["decision_hash"] == replay["decision_hash"]
        assert (
            row["result_recomputation_hash"]
            == result["result_recomputation_hash"]
            == replay["result_recomputation_hash"]
        )
        assert replay["attempt_count"] == 4
        assert replay["resource_ledger_valid"] is True


def test_leave_one_problem_out_requires_declared_budget_and_seed_count(tmp_path):
    with pytest.raises(ProtocolError, match="at least three seeds"):
        run_leave_one_problem_out_matrix(
            _problems(), _actions(), tmp_path / "too-few-seeds", seeds=(0, 1)
        )
    with pytest.raises(ProtocolError, match="exactly four attempts"):
        run_leave_one_problem_out_matrix(
            _problems(), _actions(), tmp_path / "wrong-budget",
            max_attempts=3, seeds=(0, 1, 2)
        )
