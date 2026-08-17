import json
from pathlib import Path

from forge.controller import SearchAction, load_controller_manifest
from forge.development import (
    DevelopmentProblem,
    REGISTERED_MECHANISMS,
    run_development_matrix,
)


def test_development_matrix_runs_local_packs_and_freezes_all_policies(tmp_path):
    actions = [
        SearchAction("SMALL", "elite", "local", 1, 0, "uniform"),
        SearchAction("STRONG", "diverse", "structural", 2, 1, "score_spread"),
    ]
    output = tmp_path / "controller-development"
    summary = run_development_matrix(
        [
            DevelopmentProblem("stringmax", Path("projects/stringmax")),
            DevelopmentProblem("probe", Path("projects/_probe_newproblem")),
        ],
        actions,
        output,
        generations=2,
        seeds=(17, 19),
    )

    assert summary["policy_mechanisms"] == list(REGISTERED_MECHANISMS)
    assert summary["seeds"] == [17, 19]
    assert len(summary["runs"]) == 8
    # The one-offspring arm uses four generations and the two-offspring arm
    # uses two, but both consume the same four-attempt cap.
    assert sum(row["trace_count"] for row in summary["runs"]) == 24
    assert len(summary["policy_runs"]) == 16
    assert summary["attempt_cap"] == 4
    assert {row["attempt_count"] for row in summary["runs"]} == {4}
    assert {row["attempt_count"] for row in summary["policy_runs"]} == {4}
    # Action arms and frozen-policy replays are paired on the same effective
    # seed; only the problem index is intentionally separated.
    for problem_id in ("stringmax", "probe"):
        for seed in (17, 19):
            action_seeds = {
                row["effective_seed"]
                for row in summary["runs"]
                if row["problem_id"] == problem_id and row["seed"] == seed
            }
            policy_seeds = {
                row["effective_seed"]
                for row in summary["policy_runs"]
                if row["problem_id"] == problem_id and row["seed"] == seed
            }
            assert len(action_seeds) == 1
            assert policy_seeds == action_seeds
    assert {
        row["effective_seed"] for row in summary["runs"] if row["problem_id"] == "stringmax"
    } == {17, 19}
    assert {
        row["effective_seed"] for row in summary["runs"] if row["problem_id"] == "probe"
    } == {10_017, 10_019}
    assert all(row["selected_actions"] for row in summary["policy_runs"])
    comparison = summary["development_comparison"]
    assert comparison["classification"] == "development_mock_diagnostic"
    assert comparison["scientific_evidence"] is False
    assert len(comparison["action_cells"]) == 4
    assert len(comparison["policy_cells"]) == 8
    assert set(summary["development_metrics"]) >= {
        "best_score", "auc_by_candidate", "auc_by_generation",
    }
    assert len(comparison["action_metric_cells"]) == 24
    assert len(comparison["policy_metric_cells"]) == 48
    assert {
        cell["metric"] for cell in comparison["action_metric_cells"]
    } == set(summary["development_metrics"])
    assert all(cell["run_count"] == 2 for cell in comparison["action_metric_cells"])
    assert all(cell["run_count"] == 2 for cell in comparison["policy_metric_cells"])
    assert all(cell["run_count"] == 2 for cell in comparison["action_cells"])
    assert all(cell["run_count"] == 2 for cell in comparison["policy_cells"])
    assert all(
        row["controller_policy_sha256"] == summary["policies"][row["mechanism"]]["policy_sha256"]
        for row in summary["policy_runs"]
    )
    assert all(
        len(row[field]) == 64
        for row in summary["policy_runs"]
        for field in ("events_sha256", "result_sha256", "decision_hash", "result_recomputation_hash")
    )
    assert (output / "development_traces.jsonl").is_file()
    trace_lines = (output / "development_traces.jsonl").read_text(encoding="utf-8").splitlines()
    assert trace_lines and all(line.strip() for line in trace_lines)
    assert all(isinstance(json.loads(line), dict) for line in trace_lines)
    persisted = json.loads((output / "development_summary.json").read_text())
    assert persisted["trace_sha256"] == summary["trace_sha256"]

    for mechanism in REGISTERED_MECHANISMS:
        row = summary["policies"][mechanism]
        manifest = Path(row["manifest"])
        assert manifest.is_file()
        loaded = load_controller_manifest(manifest)
        assert loaded.mechanism_id == mechanism
        assert loaded.training_problem_ids == ("probe", "stringmax")
        assert row["selected_action"]["generator_model"] in {"SMALL", "STRONG"}
        assert len(row["utilities"]) == len(actions)
        assert len(row["development_replay"]) == 4


def test_declared_stringmax_goal_beats_fixed_dev_best(tmp_path):
    actions = [
        SearchAction("SMALL", "elite", "local", 1, 0, "uniform"),
        SearchAction("STRONG", "diverse", "structural", 2, 1, "score_spread"),
    ]
    summary = run_development_matrix(
        [DevelopmentProblem("stringmax", Path("projects/stringmax"))],
        actions,
        tmp_path / "declared-goal",
        generations=3,
        max_attempts=4,
        seeds=(0, 1, 2),
    )

    primary = [
        row for row in summary["policy_runs"]
        if row["mechanism"] == "TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V1"
    ]
    fixed = [
        row for row in summary["policy_runs"]
        if row["mechanism"] == "FIXED_DEV_BEST"
    ]
    assert len(primary) == len(fixed) == 3
    assert {row["attempt_count"] for row in primary + fixed} == {4}
    assert all(
        [action["number_of_offspring"] for action in row["selected_actions"]]
        == [1, 2, 1]
        for row in primary
    )
    primary_auc = sum(row["metrics"]["auc_by_generation"] for row in primary) / len(primary)
    fixed_auc = sum(row["metrics"]["auc_by_generation"] for row in fixed) / len(fixed)
    assert primary_auc - fixed_auc >= 0.25
    fixed_by_seed = {row["seed"]: row for row in fixed}
    assert sum(
        row["best_score"] >= fixed_by_seed[row["seed"]]["best_score"]
        for row in primary
    ) >= 2


def test_development_problem_ids_are_safe_and_unique(tmp_path):
    import pytest
    from forge.protocol import ProtocolError

    with pytest.raises(ProtocolError):
        DevelopmentProblem("../hidden", Path("projects/stringmax"))
    with pytest.raises(ProtocolError):
        run_development_matrix(
            [
                DevelopmentProblem("same", Path("projects/stringmax")),
                DevelopmentProblem("same", Path("projects/_probe_newproblem")),
            ],
            [SearchAction("SMALL", "elite", "local", 1, 0, "uniform")],
            tmp_path / "out",
        )


def test_development_matrix_repeats_identical_hashes_for_same_seeds(tmp_path):
    actions = [
        SearchAction("SMALL", "elite", "local", 1, 0, "uniform"),
        SearchAction("STRONG", "diverse", "structural", 2, 1, "score_spread"),
    ]
    problems = [
        DevelopmentProblem("stringmax", Path("projects/stringmax")),
        DevelopmentProblem("probe", Path("projects/_probe_newproblem")),
    ]
    left = run_development_matrix(
        problems, actions, tmp_path / "left", generations=2, seeds=(17, 19)
    )
    right = run_development_matrix(
        problems, actions, tmp_path / "right", generations=2, seeds=(17, 19)
    )

    left_runs = [
        (row["mechanism"], row["problem_id"], row["seed"], row["best_score"],
         tuple(tuple(sorted(action.items())) for action in row["selected_actions"]),
         row["decision_hash"])
        for row in left["policy_runs"]
    ]
    right_runs = [
        (row["mechanism"], row["problem_id"], row["seed"], row["best_score"],
         tuple(tuple(sorted(action.items())) for action in row["selected_actions"]),
         row["decision_hash"])
        for row in right["policy_runs"]
    ]
    assert left_runs == right_runs
