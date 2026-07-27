"""パラメータ化探索空間モード(2026-07-06追加)の配管検証。
崖状スコア関数を模した最小Problemで、生コード変異ではなく数値ジッタのみで
改善が起きること・LLM呼び出しがゼロであることを確認する。"""
from __future__ import annotations

import pytest

from forge.loop import run
from forge.operators import mutate_params


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
