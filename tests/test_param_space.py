"""パラメータ化探索空間モード(2026-07-06追加)の配管検証。
崖状スコア関数を模した最小Problemで、生コード変異ではなく数値ジッタのみで
改善が起きること・LLM呼び出しがゼロであることを確認する。"""
from __future__ import annotations

import json

import pytest

from forge.loop import run
from forge.operators import mutate_params
from forge.controller import ComputeAwareController, SearchAction


class _ParamProblem:
    """render(params)がコードを生成し、score()はxがtargetに近いほど高得点。
    崖: xがtargetから0.3以上ずれると即死(-inf)にして崖状ランドスケープを模す。"""

    DESCRIPTION = "param mode test"
    TARGET = 0.7

    def param_space(self):
        return {"x": (0.0, 1.0), "y": (0.0, 1.0)}

    def seed_params(self):
        return [{"x": 0.5, "y": 0.5}]  # target(0.7)から0.2差=崖(0.3)の内側で生存可能

    def render(self, params: dict) -> str:
        return f"x={params['x']!r}; y={params['y']!r}"

    def score(self, cand: str):
        ns: dict = {}
        exec(compile(cand, "<c>", "exec"), {"__builtins__": {}}, ns)
        x = ns["x"]
        if abs(x - self.TARGET) > 0.3:
            return float("-inf"), False
        return 1.0 - abs(x - self.TARGET), True


def test_param_mode_improves_via_numeric_jitter_without_llm_calls(tmp_path, monkeypatch):
    def _explode(tier):
        raise AssertionError(f"param modeではLLM呼び出し禁止のはず(tier={tier})")

    monkeypatch.setattr("forge.loop.make_caller", _explode)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    best = run(
        _ParamProblem(),
        {
            "generations": 15,
            "batch_size": 6,
            "max_cheap_calls": 90,
            "max_smart_calls": 0,
            "archive_capacity": 20,
            "parents": 3,
            "workers": 1,
            "seed": 0,
        },
        str(run_dir),
    )

    assert "params" in best
    assert best["score"] > 0.9, best
    assert abs(best["params"]["x"] - _ParamProblem.TARGET) < 0.1


def test_mutate_params_changes_only_one_or_two_keys():
    import random

    space = {"a": (0.0, 1.0), "b": (0.0, 1.0), "c": (0.0, 1.0)}
    parent = {"a": 0.5, "b": 0.5, "c": 0.5}
    rng = random.Random(0)
    changed_counts = set()
    for _ in range(50):
        child = mutate_params(space, parent, rng, alarm=False)
        n_changed = sum(1 for k in space if child[k] != parent[k])
        changed_counts.add(n_changed)
        for k in space:
            assert 0.0 <= child[k] <= 1.0
    assert changed_counts <= {1, 2}


def test_mutation_operator_is_executed_by_parametric_generator():
    import random

    space = {"a": (0.0, 1.0), "b": (0.0, 1.0), "c": (0.0, 1.0)}
    parent = {"a": 0.1, "b": 0.9, "c": 0.1}
    local = mutate_params(
        space, parent, random.Random(4), False, mutation_operator="local"
    )
    structural = mutate_params(
        space, parent, random.Random(4), False, mutation_operator="structural"
    )
    simplify = mutate_params(
        space, parent, random.Random(4), False, mutation_operator="simplify"
    )

    assert structural != local
    assert sum(value != parent[key] for key, value in structural.items()) >= 2
    changed_simplified = [
        key for key in space if simplify[key] != parent[key]
    ]
    assert changed_simplified
    assert all(
        abs(simplify[key] - 0.5) < abs(parent[key] - 0.5)
        for key in changed_simplified
    )
    assert all(0.0 <= value <= 1.0 for value in structural.values())


def test_recombine_uses_the_registered_parent_pool():
    import random

    space = {"a": (0.0, 1.0), "b": (0.0, 1.0)}
    parent = {"a": 0.0, "b": 0.0}
    pool = [parent, {"a": 1.0, "b": 1.0}]
    child = mutate_params(
        space,
        parent,
        random.Random(1),
        False,
        mutation_operator="recombine",
        parent_pool=pool,
    )
    assert child != parent
    assert all(0.0 <= value <= 1.0 for value in child.values())


class _ParamProblemNoSeedParams:
    """_ParamProblemからseed_paramsだけを除いた独立クラス(継承+del ではhasattrがTrueのまま
    残るため、独立定義でhasattr(problem, "seed_params")=Falseを保証する)。"""

    DESCRIPTION = _ParamProblem.DESCRIPTION
    TARGET = _ParamProblem.TARGET
    param_space = _ParamProblem.param_space
    render = _ParamProblem.render
    score = _ParamProblem.score


def test_seed_params_falls_back_to_midpoint_when_absent(tmp_path, monkeypatch):
    assert not hasattr(_ParamProblemNoSeedParams(), "seed_params")

    def _explode(tier):
        raise AssertionError("param modeではLLM呼び出し禁止のはず")

    monkeypatch.setattr("forge.loop.make_caller", _explode)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    best = run(
        _ParamProblemNoSeedParams(),
        {
            "generations": 1,
            "batch_size": 1,
            "max_cheap_calls": 2,
            "max_smart_calls": 0,
            "archive_capacity": 5,
            "parents": 1,
            "workers": 1,
            "seed": 0,
        },
        str(run_dir),
    )
    assert best is not None


def test_v3_param_mode_records_non_llm_generation_identity(tmp_path):
    """Parametric Forge runs must remain executable under V3 provenance checks."""
    action = SearchAction("SMALL", "elite", "local", 2, 0, "uniform")
    controller = ComputeAwareController([action])
    controller.fit([{
        "split": "dev",
        "problem_id": "obp_dev_v1",
        "action": action,
        "quality_gain": 1.0,
        "cost": 1.0,
    }])
    controller.freeze()
    run_dir = tmp_path / "v3-parametric"
    run_dir.mkdir()

    best = run(
        _ParamProblem(),
        {
            "protocol_v3": True,
            "track": "SAME_MODEL",
            "generations": 1,
            "max_attempts": 2,
            "max_cheap_calls": 2,
            "max_evaluator_calls": 4,
            "resource_budgets": {
                "generation": {
                    "records": 2,
                    "input_tokens": 10_000,
                    "output_tokens": 10_000,
                },
                "evaluator": {"calls": 4},
            },
            "workers": 1,
            "parents": 1,
            "seed": 0,
            "run_id": "v3-parametric",
        },
        str(run_dir),
        controller=controller,
    )

    assert best["score"] > 0.5
    result = json.loads((run_dir / "result.json").read_text())
    assert result["attempt_count"] == 2
    assert result["controller_actions"][0]["action"]["generator_model"] == "SMALL"
    events = [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text().splitlines()
    ]
    starts = [event for event in events if event["event_type"] == "attempt_started"]
    assert starts
    assert all(start["payload"]["model"] == "PARAM_MUTATION" for start in starts)
    assert all(
        start["payload"]["metadata"]["generation_mode"] == "parametric"
        for start in starts
    )


def test_v3_param_mode_executes_controller_mutation_operator(tmp_path, monkeypatch):
    action = SearchAction("SMALL", "elite", "structural", 2, 0, "uniform")
    controller = ComputeAwareController([action])
    controller.fit([{
        "split": "dev",
        "problem_id": "obp_dev_v1",
        "action": action,
        "quality_gain": 1.0,
        "cost": 1.0,
    }])
    controller.freeze()
    observed: list[str | None] = []
    from forge import operators as operators_module
    original = operators_module.mutate_params

    def wrapped(*args, **kwargs):
        observed.append(kwargs.get("mutation_operator"))
        return original(*args, **kwargs)

    monkeypatch.setattr("forge.loop.mutate_params", wrapped)
    run_dir = tmp_path / "v3-parametric-operator"
    run_dir.mkdir()
    run(
        _ParamProblem(),
        {
            "protocol_v3": True,
            "track": "SAME_MODEL",
            "generations": 1,
            "max_attempts": 2,
            "max_cheap_calls": 2,
            "max_evaluator_calls": 4,
            "resource_budgets": {
                "generation": {"records": 2, "input_tokens": 10_000, "output_tokens": 10_000},
                "evaluator": {"calls": 4},
            },
            "workers": 1,
            "parents": 1,
            "seed": 0,
            "run_id": "v3-parametric-operator",
        },
        str(run_dir),
        controller=controller,
    )
    assert observed == ["structural", "structural"]


def test_v3_resume_continues_at_next_generation_without_slot_collision(tmp_path):
    action = SearchAction("SMALL", "elite", "local", 2, 0, "uniform")
    controller = ComputeAwareController([action])
    controller.fit([{
        "split": "dev",
        "problem_id": "obp_dev_v1",
        "action": action,
        "quality_gain": 1.0,
        "cost": 1.0,
    }])
    controller.freeze()
    run_dir = tmp_path / "v3-resume"
    run_dir.mkdir()
    config = {
        "protocol_v3": True,
        "track": "SAME_MODEL",
        "max_attempts": 4,
        "max_cheap_calls": 4,
        "max_evaluator_calls": 8,
        "resource_budgets": {
            "generation": {
                "records": 4,
                "input_tokens": 10_000,
                "output_tokens": 10_000,
            },
            "evaluator": {"calls": 8},
        },
        "workers": 1,
        "parents": 1,
        "seed": 0,
        "run_id": "v3-resume",
    }

    run(_ParamProblem(), {**config, "generations": 1}, str(run_dir), controller=controller)
    run(_ParamProblem(), {**config, "generations": 2}, str(run_dir), controller=controller)

    events = [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text().splitlines()
    ]
    starts = [event for event in events if event["event_type"] == "attempt_started"]
    assert [(event["payload"]["generation"], event["payload"]["slot"])
            for event in starts] == [(1, 0), (1, 1), (2, 0), (2, 1)]
    result = json.loads((run_dir / "result.json").read_text())
    assert result["attempt_count"] == 4
    assert [record["generation"] for record in result["controller_actions"]] == [1, 2]
