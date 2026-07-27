"""確立ベンチマークパック(bench_obp/bench_tsp)の再現性と静的ゲートの回帰テスト。

baselines.json は「forgeが良くなったかどうか」を判定する唯一の物差しなので、
値が黙って動いたら測定そのものが無効になる。ここで毎回再計算して照合する。
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKS = ("bench_obp", "bench_tsp")


def load_pack(name: str):
    """パックはsys.pathへ入れて `problem` としてimportされる(cli.pyと同じ経路)。"""
    pack_dir = str(ROOT / "projects" / name)
    saved_path = list(sys.path)
    saved_mod = sys.modules.pop("problem", None)
    try:
        sys.path.insert(0, pack_dir)
        return importlib.import_module("problem")
    finally:
        sys.path[:] = saved_path
        sys.modules.pop("problem", None)
        if saved_mod is not None:
            sys.modules["problem"] = saved_mod


def baselines(name: str) -> dict:
    return json.loads((ROOT / "projects" / name / "baselines.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("pack", PACKS)
def test_baseline_programs_reproduce_recorded_scores(pack):
    mod = load_pack(pack)
    problem = mod.Problem()
    recorded = baselines(pack)["scales"]["full"]["baselines"]

    assert problem.scale == mod.FULL_SCALE, "テストはfullスケールで走る前提"
    assert set(recorded) == set(mod.BASELINE_PROGRAMS)

    for key, source in mod.BASELINE_PROGRAMS.items():
        score, alive = problem.score(source)
        assert alive, f"{pack}/{key} が V0 を通らない"
        assert score == pytest.approx(recorded[key]["score"], abs=1e-9), (
            f"{pack}/{key} の実測 {score} が baselines.json の "
            f"{recorded[key]['score']} と一致しない"
        )


@pytest.mark.parametrize("pack", PACKS)
def test_seed_programs_are_the_recorded_baselines(pack):
    mod = load_pack(pack)
    assert set(mod.Problem().seed()) == set(mod.BASELINE_PROGRAMS.values())


@pytest.mark.parametrize("pack", PACKS)
def test_mock_scale_is_smaller_and_recorded(pack):
    mod = load_pack(pack)
    recorded = baselines(pack)["scales"]["mock"]
    for key, value in mod.MOCK_SCALE.items():
        assert value < mod.FULL_SCALE[key]
        assert recorded[key] == value


@pytest.mark.parametrize("pack", PACKS)
def test_dangerous_candidates_die_without_spawning(pack):
    mod = load_pack(pack)
    problem = mod.Problem()
    entry = "priority" if pack == "bench_obp" else "select_next_node"
    hostile = [
        f"import os\ndef {entry}(*a):\n    return 0\n",
        f"def {entry}(*a):\n    return open('/etc/passwd').read()\n",
        f"def {entry}(*a):\n    return ().__class__\n",
        "def not_the_entrypoint(*a):\n    return 0\n",
        "this is not python",
    ]
    for source in hostile:
        score, alive = problem.score(source)
        assert not alive and score == float("-inf"), f"通してはいけない候補が生存した:\n{source}"


@pytest.mark.parametrize("pack", PACKS)
def test_scoring_is_deterministic(pack):
    mod = load_pack(pack)
    problem = mod.Problem()
    source = next(iter(mod.BASELINE_PROGRAMS.values()))
    first = problem.score(source)
    second = problem.score(source)
    assert first == second
