"""bench_tsp: TSP構築ヒューリスティック（LLM4AD/EoH準拠の確立ベンチマーク）。

forge本体の性能・頑健さを、自前問題ではなく公開された基準値と同じ土俵で測るための物差し。
データ生成・評価手順は LLM4AD の tsp_construct タスクと同一（単位正方形一様乱数、
np.random.seed(2024)、16インスタンス×50都市、最近傍順に並べた候補集合から次ノードを選ぶ）。

出典・ライセンス:
  Fei Liu, Rui Zhang, Zhuoliang Xie, Rui Sun, Kai Li, Xi Lin, Zhenkun Wang,
  Zhichao Lu, and Qingfu Zhang, "LLM4AD: A Platform for Algorithm Design with
  Large Language Model," arXiv preprint arXiv:2412.17287 (2024).
  https://github.com/Optima-CityU/llm4ad （研究目的での利用が許諾されている。要citation）
  評価手順は Fei Liu et al., "Algorithm Evolution using Large Language Model,"
  arXiv:2311.15249 (2023)（AEL/EoH）に由来する。

基準値は BASELINES.md / baselines.json を参照。tests/test_bench_packs.py が再計算して照合する。
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

from forge.codecheck import CodeRejected, check_candidate
from forge.sandbox import SandboxError, SandboxTimeout, run_python_candidate

_PACK_DIR = Path(__file__).resolve().parent

DATA_SEED = 2024

# mock走行はハーネスの配管確認が目的なので縮小サイズで回す。実走行はLLM4ADと同一サイズ。
FULL_SCALE = {"n_instances": 16, "problem_size": 50}
MOCK_SCALE = {"n_instances": 4, "problem_size": 20}


def active_scale() -> dict[str, int]:
    # Keep model mocking separate from benchmark sizing.  A matched-model
    # comparison may use FORGE_MOCK=1 while still evaluating the full
    # LLM4AD-compatible instance set.
    requested = os.environ.get("FORGE_BENCH_SCALE", "").strip().lower()
    if requested == "full":
        return dict(FULL_SCALE)
    if requested == "mock":
        return dict(MOCK_SCALE)
    return dict(MOCK_SCALE if os.environ.get("FORGE_MOCK") == "1" else FULL_SCALE)


# 候補コードの後ろに連結して子プロセスで実行する評価ハーネス（信頼済み・こちらが書いたコード）。
# LLM4AD tsp_construct の evaluate() をそのまま移したもの。ルートは node0 から開始し、
# 未訪問の近傍候補を候補集合として渡す。既訪問ノードを選んだ時点で候補は失格。
_HARNESS = '''

import numpy as _forge_np


def _forge_evaluate():
    _forge_np.random.seed({data_seed})
    _forge_size = {problem_size}
    _forge_instances = []
    for _forge_i in range({n_instances}):
        _forge_coords = _forge_np.random.rand(_forge_size, 2)
        _forge_dm = _forge_np.linalg.norm(
            _forge_coords[:, _forge_np.newaxis] - _forge_coords, axis=2
        )
        _forge_instances.append((_forge_coords, _forge_dm))

    _forge_dists = []
    for _forge_coords, _forge_dm in _forge_instances:
        _forge_nbr = _forge_np.argsort(_forge_dm, axis=1)
        _forge_cur = 0
        _forge_route = _forge_np.zeros(_forge_size)
        for _forge_i in range(1, _forge_size - 1):
            _forge_near = _forge_nbr[_forge_cur][1:]
            _forge_un = _forge_near[
                ~_forge_np.isin(_forge_near, _forge_route[:_forge_i])
            ]
            _forge_next = select_next_node(_forge_cur, 0, _forge_un, _forge_dm)
            _forge_next = int(_forge_next)
            if _forge_next not in _forge_un:
                raise ValueError("select_next_node must return one of unvisited_nodes")
            _forge_cur = _forge_next
            _forge_route[_forge_i] = _forge_cur
        _forge_left = _forge_np.arange(_forge_size)[
            ~_forge_np.isin(_forge_np.arange(_forge_size), _forge_route[:_forge_size - 1])
        ]
        _forge_route[_forge_size - 1] = _forge_left[0]

        _forge_cost = 0.0
        for _forge_j in range(_forge_size - 1):
            _forge_cost += _forge_np.linalg.norm(
                _forge_coords[int(_forge_route[_forge_j])]
                - _forge_coords[int(_forge_route[_forge_j + 1])]
            )
        _forge_cost += _forge_np.linalg.norm(
            _forge_coords[int(_forge_route[-1])] - _forge_coords[int(_forge_route[0])]
        )
        _forge_dists.append(float(_forge_cost))
    return -float(sum(_forge_dists) / len(_forge_dists))
'''


SEED_NEAREST_NEIGHBOR = '''import numpy as np


def select_next_node(current_node, destination_node, unvisited_nodes, distance_matrix):
    """Greedy nearest neighbour: go to the closest unvisited node."""
    return unvisited_nodes[np.argmin(distance_matrix[current_node][unvisited_nodes])]
'''

SEED_FARTHEST_FIRST = '''import numpy as np


def select_next_node(current_node, destination_node, unvisited_nodes, distance_matrix):
    """Balance closeness to the current node against distance from the start."""
    to_current = distance_matrix[current_node][unvisited_nodes]
    to_start = distance_matrix[destination_node][unvisited_nodes]
    return unvisited_nodes[np.argmin(to_current - 0.3 * to_start)]
'''

# baselines.json の検証用。BASELINE_PROGRAMS[key] を score() に通すと
# baselines.json の対応する値が再現する（tests/test_bench_packs.py が照合する）。
BASELINE_PROGRAMS = {
    "nearest_neighbor": SEED_NEAREST_NEIGHBOR,
    "farthest_first_blend": SEED_FARTHEST_FIRST,
}


class Problem:
    DESCRIPTION = (
        "Invent a constructive heuristic for the travelling salesman problem.\n"
        "A tour is built one node at a time starting from node 0. At every step you are "
        "given the unvisited nodes that are nearest to the current node, and you choose "
        "which one to visit next. The goal is to minimise the total tour length "
        "(including the return to node 0).\n\n"
        "Write a single function:\n"
        "    def select_next_node(current_node, destination_node, unvisited_nodes, "
        "distance_matrix):\n"
        "`current_node` is the node you are standing on, `destination_node` is the start "
        "node the tour must return to (always 0), `unvisited_nodes` is a numpy array of "
        "candidate node ids sorted by increasing distance from `current_node`, and "
        "`distance_matrix[i][j]` is the euclidean distance between node i and node j. "
        "Return the id of the next node to visit.\n\n"
        "Rules: the returned id must come from `unvisited_nodes` — returning anything "
        "else fails the whole evaluation. Only `import numpy as np` and `import math` "
        "are allowed. The function is called once per step, so keep it vectorised."
    )

    def __init__(self):
        cfg_path = _PACK_DIR / "config.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {}
        self.eval_timeout = float(cfg.get("eval_timeout", 120))
        self.scale = active_scale()
        self.harness = _HARNESS.format(data_seed=DATA_SEED, **self.scale)

    def seed(self):
        return [SEED_NEAREST_NEIGHBOR, SEED_FARTHEST_FIRST]

    def score_with_status(self, cand: str):
        try:
            check_candidate(cand, required_defs=("select_next_node",))
        except CodeRejected as exc:
            message = str(exc)
            if message.startswith("parse failed:"):
                status = "invalid_syntax"
            elif "missing required def" in message:
                status = "constraint_violation"
            else:
                status = "sandbox_rejected"
            return float("-inf"), False, status, type(exc).__name__
        try:
            value = run_python_candidate(
                cand + self.harness, "_forge_evaluate", timeout=self.eval_timeout,
                policy="v3" if os.environ.get("FORGE_PROTOCOL_V3") == "1" else None,
            )
        except SandboxTimeout as exc:
            return float("-inf"), False, "timeout", type(exc).__name__
        except SandboxError as exc:
            message = str(exc)
            status = (
                "sandbox_rejected"
                if "static policy rejected" in message or "import denied" in message
                else "constraint_violation"
                if any(token in message for token in ("must return", "invalid", "unvisited"))
                else "runtime_error"
            )
            return float("-inf"), False, status, type(exc).__name__
        if (
            not isinstance(value, float)
            or not math.isfinite(value)
        ):
            return float("-inf"), False, "constraint_violation", "InvalidScore"
        return value, True, "valid_candidate", None

    def score(self, cand: str):
        score, alive, _, _ = self.score_with_status(cand)
        return score, alive
