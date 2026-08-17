"""bench_obp: Online Bin Packing（LLM4AD/FunSearch準拠の確立ベンチマーク）。

forge本体の性能・頑健さを、自前問題ではなく公開された基準値と同じ土俵で測るための物差し。
データ生成・評価手順は LLM4AD の online_bin_packing タスクと同一（Weibull(3)×45、
clip[1,capacity]、四捨五入、np.random.seed(2024)、5インスタンス×5000品目、容量100）。

出典・ライセンス:
  Fei Liu, Rui Zhang, Zhuoliang Xie, Rui Sun, Kai Li, Xi Lin, Zhenkun Wang,
  Zhichao Lu, and Qingfu Zhang, "LLM4AD: A Platform for Algorithm Design with
  Large Language Model," arXiv preprint arXiv:2412.17287 (2024).
  https://github.com/Optima-CityU/llm4ad （研究目的での利用が許諾されている。要citation）
  問題設定そのものは Romera-Paredes et al., "Mathematical discoveries from program
  search with large language models", Nature 625 (2024)（FunSearch）に由来する。

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

CAPACITY = 100
DATA_SEED = 2024

# mock走行はハーネスの配管確認が目的なので縮小サイズで回す(1候補あたり約2秒→約0.02秒)。
# 実走行は必ずLLM4ADと同一サイズ。baselines.jsonは両方の基準値を持つ。
FULL_SCALE = {"n_instances": 5, "n_items": 5000}
MOCK_SCALE = {"n_instances": 2, "n_items": 500}


def active_scale() -> dict[str, int]:
    # ``FORGE_MOCK`` selects the model adapter, while the benchmark scale is
    # independently selectable.  This lets a deterministic mock model run
    # against the full LLM4AD data rather than silently turning a benchmark
    # comparison into a small smoke test.
    requested = os.environ.get("FORGE_BENCH_SCALE", "").strip().lower()
    if requested == "full":
        return dict(FULL_SCALE)
    if requested == "mock":
        return dict(MOCK_SCALE)
    return dict(MOCK_SCALE if os.environ.get("FORGE_MOCK") == "1" else FULL_SCALE)


# 候補コードの後ろに連結して子プロセスで実行する評価ハーネス（信頼済み・こちらが書いたコード）。
# 名前は全て _forge_ 前置きにして候補側の識別子と衝突しないようにする。
_HARNESS = '''

import numpy as _forge_np


def _forge_evaluate():
    _forge_np.random.seed({data_seed})
    _forge_cap = {capacity}
    _forge_totals = []
    for _forge_i in range({n_instances}):
        _forge_samples = _forge_np.clip(
            _forge_np.random.weibull(3, {n_items}) * 45, 1, _forge_cap
        )
        _forge_items = _forge_np.round(_forge_samples).astype(int)
        _forge_bins = _forge_np.array([_forge_cap] * {n_items})
        for _forge_item in _forge_items:
            _forge_valid = _forge_np.nonzero((_forge_bins - _forge_item) >= 0)[0]
            _forge_pr = _forge_np.asarray(
                priority(_forge_item, _forge_bins[_forge_valid]), dtype=float
            )
            if _forge_pr.shape != _forge_valid.shape:
                raise ValueError("priority must return one score per valid bin")
            if not _forge_np.all(_forge_np.isfinite(_forge_pr)):
                raise ValueError("priority returned a non-finite score")
            _forge_best = _forge_valid[int(_forge_np.argmax(_forge_pr))]
            _forge_bins[_forge_best] -= _forge_item
        _forge_totals.append(int((_forge_bins != _forge_cap).sum()))
    return -float(sum(_forge_totals) / len(_forge_totals))
'''


SEED_BEST_FIT = '''import numpy as np


def priority(item, bins):
    """Best fit: prefer the bin that leaves the least free space."""
    return -(bins - item)
'''

SEED_FIRST_FIT = '''import numpy as np


def priority(item, bins):
    """First fit: prefer the lowest-indexed valid bin."""
    return -np.arange(len(bins), dtype=float)
'''

# baselines.json の検証用。BASELINE_PROGRAMS[key] を score() に通すと
# baselines.json の対応する値が再現する（tests/test_bench_packs.py が照合する）。
BASELINE_PROGRAMS = {
    "best_fit": SEED_BEST_FIT,
    "first_fit": SEED_FIRST_FIT,
}


_PROBE_HARNESS = '''

import numpy as _forge_np


def _forge_probe():
    _forge_rng = _forge_np.random.RandomState(12345)
    _forge_out = []
    for _forge_i in range(120):
        _forge_item = int(_forge_rng.randint(1, 91))
        # 残容量は順不同(実際の評価器でもbin番号順であって容量順ではない)。
        # ソートするとbest-fitとfirst-fitが同じindexを選び、指紋が識別できなくなる。
        _forge_bins = _forge_rng.randint(_forge_item, 101, size=int(_forge_rng.randint(3, 12)))
        _forge_pr = _forge_np.asarray(priority(_forge_item, _forge_bins), dtype=float)
        if _forge_pr.shape != _forge_bins.shape or not _forge_np.all(_forge_np.isfinite(_forge_pr)):
            raise ValueError("invalid priority output")
        _forge_out.append(int(_forge_np.argmax(_forge_pr)))
    return _forge_out
'''


def behaviour_probe(source: str) -> list[int]:
    """候補の「挙動の指紋」を取る。固定入力に対してどのbinを選ぶかの列を返す。

    文面の重複排除(SimHash)は書き換えを素通しするので、多様性の指標にならない
    (2026-07-25実測: 生存プールの挙動重複率76.7%、支配的な指紋の正体はbest-fitだった)。
    挙動で見れば、同じことをしている候補は同じ指紋になる。
    """
    check_candidate(source, required_defs=("priority",))
    return run_python_candidate(source + _PROBE_HARNESS, "_forge_probe", timeout=30)


class Problem:
    DESCRIPTION = (
        "Invent a heuristic for the online bin packing problem.\n"
        "Items arrive one at a time and must be placed immediately into a bin of fixed "
        "capacity 100. The goal is to minimise the total number of bins used.\n\n"
        "Write a single function:\n"
        "    def priority(item, bins):\n"
        "`item` is the size of the arriving item (a positive integer). `bins` is a numpy "
        "array holding the remaining capacity of every bin the item still fits into, in "
        "increasing order of bin index. Return a numpy array of the same length: the item "
        "is placed into the bin with the highest score (ties go to the lowest index).\n\n"
        "Rules: only `import numpy as np` and `import math` are allowed. Every returned "
        "score must be finite. The function is called once per item, so it must be "
        "vectorised with numpy rather than looping in Python."
    )

    def __init__(self):
        cfg_path = _PACK_DIR / "config.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {}
        self.eval_timeout = float(cfg.get("eval_timeout", 120))
        self.scale = active_scale()
        self.harness = _HARNESS.format(
            data_seed=DATA_SEED, capacity=CAPACITY, **self.scale
        )

    def seed(self):
        return [SEED_BEST_FIT, SEED_FIRST_FIT]

    def score_with_status(self, cand: str):
        try:
            check_candidate(cand, required_defs=("priority",))
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
                if any(token in message for token in ("must return", "invalid priority", "non-finite"))
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
