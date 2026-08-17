"""memoryfit: online storage-slot allocation with a separate interface name.

This local pack is intentionally independent of the established ``bench_obp``
pack: it uses a different function name, data seed, and scale while retaining
the common online residual decision shape.  It is used as a new, unseen LOO
target for the V2 controller diagnostic.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

import json

from forge.codecheck import CodeRejected, check_candidate
from forge.sandbox import SandboxError, SandboxTimeout, run_python_candidate


_PACK_DIR = Path(__file__).resolve().parent
CAPACITY = 100
DATA_SEED = 3
FULL_SCALE = {"n_instances": 4, "n_items": 1000}
MOCK_SCALE = {"n_instances": 2, "n_items": 200}


def active_scale() -> dict[str, int]:
    return dict(MOCK_SCALE if os.environ.get("FORGE_MOCK") == "1" else FULL_SCALE)


_HARNESS = '''

import numpy as _forge_np


def _forge_evaluate():
    _forge_rng = _forge_np.random.RandomState({data_seed})
    _forge_capacity = {capacity}
    _forge_totals = []
    for _forge_i in range({n_instances}):
        _forge_loads = _forge_np.round(
            _forge_np.clip(_forge_rng.weibull(3, {n_items}) * 45, 1, _forge_capacity)
        ).astype(int)
        _forge_slots = _forge_np.array([_forge_capacity] * {n_items})
        for _forge_load in _forge_loads:
            _forge_valid = _forge_np.nonzero((_forge_slots - _forge_load) >= 0)[0]
            _forge_scores = _forge_np.asarray(
                rank_slot(_forge_load, _forge_slots[_forge_valid]), dtype=float
            )
            if _forge_scores.shape != _forge_valid.shape:
                raise ValueError("rank_slot must return one score per valid slot")
            if not _forge_np.all(_forge_np.isfinite(_forge_scores)):
                raise ValueError("rank_slot returned a non-finite score")
            _forge_choice = _forge_valid[int(_forge_np.argmax(_forge_scores))]
            _forge_slots[_forge_choice] -= _forge_load
        _forge_totals.append(int((_forge_slots != _forge_capacity).sum()))
    return -float(sum(_forge_totals) / len(_forge_totals))
'''


SEED_TIGHT_FIT = '''import numpy as np


def rank_slot(load, capacities):
    """Prefer the slot with the least remaining capacity after placement."""
    return -(capacities - load)
'''

SEED_EARLY_SLOT = '''import numpy as np


def rank_slot(load, capacities):
    """Prefer the earliest slot among those that can accept the load."""
    return -np.arange(len(capacities), dtype=float)
'''


BASELINE_PROGRAMS = {
    "tight_fit": SEED_TIGHT_FIT,
    "early_slot": SEED_EARLY_SLOT,
}


class Problem:
    DESCRIPTION = (
        "Invent a heuristic for online storage-slot allocation.\n"
        "Loads arrive one at a time and must be assigned immediately to a slot "
        "with remaining capacity 100. Minimise the number of slots touched.\n\n"
        "Write a single function:\n"
        "    def rank_slot(load, capacities):\n"
        "`load` is the arriving positive integer and `capacities` is a numpy array "
        "of the remaining capacities of slots that can accept it. Return one finite "
        "numpy score per capacity; the highest score is selected.\n\n"
        "Rules: only `import numpy as np` and `import math` are allowed. The function "
        "is called once per load, so it must be vectorised."
    )

    def __init__(self):
        cfg_path = _PACK_DIR / "config.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {}
        self.eval_timeout = float(cfg.get("eval_timeout", 120))
        self.scale = active_scale()
        self.harness = _HARNESS.format(data_seed=DATA_SEED, capacity=CAPACITY, **self.scale)

    def seed(self):
        return [SEED_TIGHT_FIT, SEED_EARLY_SLOT]

    def score_with_status(self, cand: str):
        try:
            check_candidate(cand, required_defs=("rank_slot",))
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
                cand + self.harness,
                "_forge_evaluate",
                timeout=self.eval_timeout,
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
                if any(token in message for token in ("must return", "rank_slot", "non-finite"))
                else "runtime_error"
            )
            return float("-inf"), False, status, type(exc).__name__
        if not isinstance(value, float) or not math.isfinite(value):
            return float("-inf"), False, "constraint_violation", "InvalidScore"
        return value, True, "valid_candidate", None

    def score(self, cand: str):
        score, alive, _, _ = self.score_with_status(cand)
        return score, alive
