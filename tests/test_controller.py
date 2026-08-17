import pytest

from forge.controller import (
    ComputeAwareController,
    ControllerNotFrozenError,
    CostUnawareController,
    FixedDevBestController,
    HoldoutUpdateError,
    NoTransferPriorController,
    SearchAction,
    SearchState,
    controller_for_mechanism,
)
from forge.protocol import ProtocolError


def _actions():
    return [
        SearchAction("SMALL", "elite", "local", 2, 0, "uniform"),
        SearchAction("STRONG", "diverse", "structural", 1, 1, "score_spread"),
    ]


def _traces():
    return [
        {"split": "dev", "problem_id": "obp_dev_v1", "action": _actions()[0],
         "quality_gain": 2.0, "cost": 1.0},
        {"split": "dev", "problem_id": "tsp_dev_v1", "action": _actions()[1],
         "quality_gain": 1.0, "cost": 4.0},
    ]


def test_controller_trains_on_dev_freezes_and_chooses_deterministically():
    controller = ComputeAwareController(_actions())
    with pytest.raises(ControllerNotFrozenError):
        controller.choose(SearchState(10, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1))
    controller.fit(_traces())
    digest = controller.freeze()
    assert len(digest) == 64
    assert controller.choose(SearchState(10, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1)) == _actions()[0]
    assert controller.training_problem_ids == ("obp_dev_v1", "tsp_dev_v1")


def test_controller_normalizes_quality_gain_per_development_problem():
    actions = _actions()
    controller = ComputeAwareController(actions)
    controller.fit([
        {
            "split": "dev", "problem_id": "large_scale",
            "action": actions[0], "quality_gain": 100.0, "cost": 1.0,
        },
        {
            "split": "dev", "problem_id": "small_scale",
            "action": actions[1], "quality_gain": 1.0, "cost": 1.0,
        },
    ])
    assert controller.gain_normalization_scales == {
        "large_scale": 100.0,
        "small_scale": 1.0,
    }
    assert controller.utilities[actions[0]] == 1.0
    assert controller.utilities[actions[1]] == 1.0


def test_primary_controller_packs_a_short_budget_around_the_best_expensive_arm():
    actions = [
        SearchAction("SMALL", "elite", "local", 1, 0, "uniform"),
        SearchAction("STRONG", "diverse", "structural", 2, 1, "score_spread"),
    ]
    controller = ComputeAwareController(actions)
    controller.fit([
        {
            "split": "dev", "problem_id": "stringmax",
            "action": actions[0], "quality_gain": 1.0, "cost": 1.0,
        },
        {
            "split": "dev", "problem_id": "stringmax",
            "action": actions[1], "quality_gain": 5.0, "cost": 1.0,
        },
    ])
    controller.freeze()

    assert controller.choose(SearchState(4, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1)) is actions[0]
    assert controller.choose(SearchState(3, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1)) is actions[1]
    assert controller.choose(SearchState(1, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1)) is actions[0]
    assert not controller.restrict_parents_to_incumbent(
        SearchState(4, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1), actions[0]
    )
    assert controller.restrict_parents_to_incumbent(
        SearchState(3, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1), actions[1]
    )
    assert controller.restrict_parents_to_incumbent(
        SearchState(1, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1), actions[0]
    )
    mid = SearchState(3, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1)
    tail = SearchState(1, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1)
    opening = SearchState(4, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1)
    assert controller.recombine_mid_parents(mid, actions[1])
    assert not controller.recombine_mid_parents(tail, actions[0])
    assert not controller.recombine_mid_parents(opening, actions[0])
    archive = [
        {"text": "hello world", "score": 7.0},
        {"text": "abc", "score": 3.0},
    ]
    assert controller.restricted_parents(mid, actions[1], archive) == archive
    covered = [
        {"text": "helldo word", "score": 7.0, "gen": 1},
        {"text": "hello world", "score": 7.0, "gen": 0},
        {"text": "abc", "score": 3.0, "gen": 0},
    ]
    assert controller.restricted_parents(mid, actions[1], covered) == [
        {"text": "helldo word", "score": 7.0, "gen": 1},
        {"text": "hello world", "score": 7.0, "gen": 0},
        {"text": "abc", "score": 3.0, "gen": 0},
    ]
    assert controller.restricted_parents(tail, actions[0], archive) == [
        {"text": "hello world", "score": 7.0}
    ]
    assert controller.restricted_parents(opening, actions[0], archive) is None
    one_arm = ComputeAwareController([actions[0]])
    one_arm.fit([{
        "split": "dev", "problem_id": "stringmax",
        "action": actions[0], "quality_gain": 1.0, "cost": 1.0,
    }])
    one_arm.freeze()
    assert not one_arm.restrict_parents_to_incumbent(
        SearchState(4, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1), actions[0]
    )
    assert not one_arm.recombine_mid_parents(
        SearchState(3, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1), actions[0]
    )


def test_primary_controller_uses_exploratory_mid_slot_on_utility_tie():
    actions = [
        SearchAction("SMALL", "elite", "local", 1, 0, "uniform"),
        SearchAction("STRONG", "diverse", "structural", 2, 1, "score_spread"),
    ]
    controller = ComputeAwareController(actions)
    controller.fit([
        {"split": "dev", "problem_id": "bench_obp", "action": action,
         "quality_gain": 0.0, "cost": 1.0}
        for action in actions
    ])
    controller.freeze()
    state = SearchState(3, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1)
    assert controller.choose(state) is actions[1]


def test_v2_controller_reserves_opening_probe_and_structural_mid_slot():
    actions = [
        SearchAction("SMALL", "elite", "local", 1, 0, "uniform"),
        SearchAction("STRONG", "diverse", "structural", 2, 1, "score_spread"),
    ]
    controller = controller_for_mechanism(
        "TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V2", actions
    )
    controller.fit([
        {"split": "dev", "problem_id": "p0", "action": actions[0],
         "quality_gain": 1.0, "cost": 1.0},
        {"split": "dev", "problem_id": "p1", "action": actions[1],
         "quality_gain": 2.0, "cost": 1.0},
    ])
    controller.freeze()
    state = lambda remaining: SearchState(
        remaining, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1
    )
    assert controller.choose(state(4)) is actions[0]
    assert controller.choose(state(3)) is actions[1]
    assert controller.choose(state(2)) is actions[0]
    assert controller.restrict_parents_to_incumbent(state(4), actions[1])
    assert controller.restrict_parents_to_incumbent(state(2), actions[1])


def test_v2_controller_adapts_a_three_arm_short_pack_from_initial_spread():
    actions = [
        SearchAction("SMALL", "elite", "local", 1, 0, "uniform"),
        SearchAction("STRONG", "diverse", "structural", 2, 1, "score_spread"),
        SearchAction("STRONG", "diverse", "structural", 3, 1, "score_spread"),
    ]
    controller = controller_for_mechanism(
        "TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V2", actions
    )
    controller.fit([
        {"split": "dev", "problem_id": "p0", "action": actions[0],
         "quality_gain": 1.0, "cost": 1.0},
        {"split": "dev", "problem_id": "p1", "action": actions[1],
         "quality_gain": 3.0, "cost": 1.0},
        {"split": "dev", "problem_id": "p2", "action": actions[2],
         "quality_gain": 2.0, "cost": 1.0},
    ])
    controller.freeze()
    state = lambda remaining, spread: SearchState(
        remaining, 0, 0, 0, spread, 0, 0, 0, 0, 0, 1
    )
    assert controller.choose(state(4, 0.2)) is actions[1]
    assert controller.choose(state(2, 0.2)) is actions[1]
    assert controller.choose(state(4, 2.0)) is actions[2]
    assert controller.choose(state(1, 2.0)) is actions[0]
    assert controller.restrict_parents_to_incumbent(state(4, 0.2), actions[1])
    assert controller.restrict_parents_to_incumbent(state(4, 2.0), actions[2])


def test_v2_routes_registered_global_arm_for_any_non_degenerate_spread():
    actions = [
        SearchAction("SMALL", "elite", "local", 1, 0, "uniform"),
        SearchAction("STRONG", "diverse", "global", 1, 1, "score_spread"),
        SearchAction("STRONG", "diverse", "structural", 2, 1, "score_spread"),
    ]
    controller = controller_for_mechanism(
        "TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V2", actions
    )
    controller.fit([
        {"split": "dev", "problem_id": "p0", "action": actions[0],
         "quality_gain": 1.0, "cost": 1.0},
        {"split": "dev", "problem_id": "p1", "action": actions[1],
         "quality_gain": 2.0, "cost": 1.0},
        {"split": "dev", "problem_id": "p2", "action": actions[2],
         "quality_gain": 3.0, "cost": 1.0},
    ])
    controller.freeze()

    non_degenerate_spread = lambda remaining: SearchState(
        remaining, 0, 0, 0, 2.0, 0, 0, 0, 0, 0, 1
    )
    assert [controller.choose(non_degenerate_spread(remaining))
            for remaining in (4, 3, 1)] == [actions[1], actions[1], actions[1]]

    broad_spread = lambda remaining: SearchState(
        remaining, 0, 0, 0, 4.0, 0, 0, 0, 0, 0, 1
    )
    assert [controller.choose(broad_spread(remaining)) for remaining in (4, 3, 2, 1)] == [
        actions[1], actions[1], actions[1], actions[1]
    ]


def test_v2_universal_router_uses_structural2_in_compact_regime():
    actions = [
        SearchAction("SMALL", "elite", "local", 1, 0, "uniform"),
        SearchAction("STRONG", "diverse", "global", 1, 1, "score_spread"),
        SearchAction("STRONG", "diverse", "structural", 2, 1, "score_spread"),
        SearchAction("STRONG", "diverse", "structural", 3, 1, "score_spread"),
    ]
    controller = controller_for_mechanism(
        "TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V2", actions
    )
    controller.fit([
        {"split": "dev", "problem_id": f"p{i}", "action": action,
         "quality_gain": float(i + 1), "cost": 1.0}
        for i, action in enumerate(actions)
    ])
    controller.freeze()

    def state(remaining, spread):
        return SearchState(remaining, 0, 0, 0, spread, 0, 0, 0, 0, 0, 1)

    assert controller.choose(state(4, 0.0)) is actions[2]
    assert controller.choose(state(2, 0.0)) is actions[2]
    assert controller.choose(state(1, 0.0)) is actions[0]
    assert [controller.choose(state(remaining, 0.2))
            for remaining in (4, 2, 1)] == [actions[1], actions[1], actions[1]]
    assert not controller.restrict_parents_to_incumbent(state(4, 0.2), actions[1])
    assert not controller.recombine_mid_parents(state(4, 0.2), actions[1])
    assert controller.choose(state(4, 2.0)) is actions[1]
    assert controller.choose(state(3, 2.0)) is actions[1]
    assert controller.choose(state(4, 4.0)) is actions[1]


def test_controller_rejects_holdout_training_and_updates():
    controller = ComputeAwareController(_actions())
    with pytest.raises(HoldoutUpdateError):
        controller.fit([{"split": "holdout", "action": _actions()[0], "quality_gain": 1, "cost": 1}])
    controller.fit(_traces())
    controller.freeze()
    with pytest.raises(HoldoutUpdateError):
        controller.update_from_holdout({"split": "holdout", "quality_gain": 100})
    assert controller.holdout_update_attempts == 2


def test_no_transfer_ablation_has_explicit_mechanism_id():
    controller = NoTransferPriorController(_actions())
    controller.fit(_traces())
    controller.freeze()
    assert controller.mechanism_id == "NO_TRANSFER_PRIOR"


def test_no_transfer_ablation_cannot_refit_after_freeze_or_accept_unknown_action():
    controller = NoTransferPriorController(_actions())
    with pytest.raises(ProtocolError):
        controller.fit([{
            "split": "dev",
            "action": SearchAction("MEDIUM", "elite", "local", 1, 0, "uniform"),
        }])
    controller.fit(_traces())
    controller.freeze()
    with pytest.raises(ControllerNotFrozenError):
        controller.fit(_traces())


def test_fixed_dev_best_is_state_and_cost_independent():
    actions = _actions()
    traces = [
        {"split": "dev", "problem_id": "p0", "action": actions[0],
         "quality_gain": 1.0, "cost": 100.0},
        {"split": "dev", "problem_id": "p1", "action": actions[1],
         "quality_gain": 0.9, "cost": 1.0},
    ]
    fixed = FixedDevBestController(actions)
    fixed.fit(traces)
    fixed.freeze()
    state = SearchState(10, 0, 0, 0, 0, 0, 0, 0, 0, 0, 10_000)
    assert fixed.choose(state) is actions[0]


def test_cost_unaware_ablation_ignores_estimated_generation_cost():
    actions = _actions()
    traces = [
        {"split": "dev", "problem_id": "p0", "action": actions[0],
         "quality_gain": 1.0, "cost": 1.0},
        {"split": "dev", "problem_id": "p1", "action": actions[1],
         "quality_gain": 0.9, "cost": 1.0},
    ]
    aware = ComputeAwareController(actions)
    unaware = CostUnawareController(actions)
    aware.fit(traces)
    unaware.fit(traces)
    aware.freeze()
    unaware.freeze()
    expensive_state = SearchState(10, 0, 0, 0, 0, 0, 0, 0, 0, 0, 10_000)
    assert aware.choose(expensive_state) is actions[1]
    assert unaware.choose(expensive_state) is actions[0]


def test_controller_rejects_nonfinite_training_values():
    controller = ComputeAwareController(_actions())
    with pytest.raises(ProtocolError):
        controller.fit([{
            "split": "dev", "action": _actions()[0],
            "quality_gain": float("nan"), "cost": 1.0,
        }])
    with pytest.raises(ProtocolError):
        controller.fit([{
            "split": "dev", "action": _actions()[0],
            "quality_gain": 1.0, "cost": float("inf"),
        }])


def test_controller_rejects_invalid_state_and_action_schema():
    with pytest.raises(ProtocolError):
        SearchState(-1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1)
    with pytest.raises(ProtocolError):
        SearchState(1, float("nan"), 0, 0, 0, 0, 0, 0, 0, 0, 1)
    with pytest.raises(ProtocolError):
        SearchAction("SMALL", "elite", "local", 0, 0, "uniform")
    with pytest.raises(ProtocolError):
        ComputeAwareController([_actions()[0], _actions()[0]])


def test_controller_rejects_untraceable_dev_trace_and_floating_model():
    with pytest.raises(ProtocolError, match="pinned"):
        SearchAction("latest", "elite", "local", 1, 0, "uniform")
    controller = ComputeAwareController(_actions())
    with pytest.raises(ProtocolError, match="problem_id"):
        controller.fit([{
            "split": "dev", "action": _actions()[0],
            "quality_gain": 1.0, "cost": 1.0,
        }])
