"""走行の性能指標。archive.jsonl から計算するだけなので**すべて無料・決定論的**。

LLMもネットワークも使わない。だから指標は1つに絞らず、片っ端から計算して並べる。
どれが効くかは後から選べばよく、選び直しても再走行は要らない。

指標を1つに絞ると測定が壊れる。2026-07-25〜27の実測で、既定の `best_score`
(1走行の最大値)は標準偏差19.1、n=3での最小検出可能効果45.2点だった。ところが
人手のbest-fitからFunSearch公開値までの伸びしろ全体が66.7点しかない。つまり
「forgeを世界記録級にする改良」ですら3シードでは有意にならない。同じデータから
best-so-far曲線のAUCを計算すると標準偏差10.5、MDE 25.0点まで下がる。
測るものを変えるだけで検出力が倍近く変わる。

用語:
  生存候補   V0(決定論採点)を通った候補。archive.jsonl に載っているのはこれだけ。
  best-so-far  その時点までの最良スコア。単調非減少。
  AUC        best-so-far 曲線の平均値。終端値より分散が小さく、途中の伸びも拾う。
"""
from __future__ import annotations

import json
import math
import statistics
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def load_rows(archive_path: str | Path) -> list[dict[str, Any]]:
    """archive.jsonl を読む。壊れた行と島リセットの境界行は飛ばす。"""
    rows = []
    path = Path(archive_path)
    if not path.is_file():
        return rows
    for line in path.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict) or item.get("island_reset"):
            continue
        if isinstance(item.get("score"), (int, float)) and isinstance(item.get("gen"), int):
            rows.append(item)
    return rows


def best_so_far(scores: Iterable[float], start: float | None = None) -> list[float]:
    out, cur = [], start if start is not None else float("-inf")
    for s in scores:
        cur = max(cur, s)
        out.append(cur)
    return out


def _auc(curve: list[float]) -> float | None:
    finite = [c for c in curve if c > float("-inf")]
    return statistics.fmean(finite) if finite else None


def _pct(value: float | None, lower_bound: float | None) -> float | None:
    """スコアが -(最小化したい量) のとき、下界からの超過率(%)を返す。

    箱詰めのように「小さいほど良い量」を負にしてスコアにしている問題でのみ意味を持つ。
    baselines.json に下界が無ければ None。
    """
    if value is None or not lower_bound or lower_bound <= 0:
        return None
    return 100.0 * ((-value) - lower_bound) / lower_bound


def _attach_efficiency_metrics(
    output: dict[str, Any], result: dict[str, Any] | None
) -> None:
    """Attach run-counter metrics even when no candidate survives."""
    if not result:
        return
    used = result.get("cheap_used") or 0
    failed = result.get("cheap_failed")
    v3_attempts = result.get("attempt_count")
    if (
        isinstance(v3_attempts, int)
        and not isinstance(v3_attempts, bool)
        and v3_attempts >= 0
    ):
        generation_calls = v3_attempts
        throughput_calls = generation_calls
    else:
        generation_calls = used + failed if failed is not None else used
        # Legacy runs only charge successful responses to cheap_used.
        throughput_calls = used
    wall = result.get("wall_secs") or 0.0
    output.update({
        "cheap_used": used,
        "cheap_failed": failed,
        "generation_calls": generation_calls,
        "cheap_failure_rate": (
            failed / generation_calls
            if failed is not None and generation_calls else None
        ),
        "wall_secs": wall,
        "alive_per_call": (
            output.get("alive_candidates", 0) / throughput_calls
            if throughput_calls else None
        ),
        "gain_per_call": (
            (output["best_score"] - output["baseline_score"]) / throughput_calls
            if throughput_calls
            and output.get("best_score") is not None
            and output.get("baseline_score") is not None else None
        ),
        "stopped_by": result.get("stopped_by"),
    })


def run_metrics(
    archive_path: str | Path,
    *,
    result: dict[str, Any] | None = None,
    archive_label: str | None = None,
    baseline_score: float | None = None,
    lower_bound: float | None = None,
    gen_cap: int | None = None,
    candidate_cap: int | None = None,
    band: int = 20,
    catastrophic_ratio: float = 1.2,
) -> dict[str, Any]:
    """1走行の全指標。走行間で条件を揃えたいときは gen_cap / candidate_cap を使う。

    baseline_score: 比較の基準(通常はシードの最良 = 人手のヒューリスティック)。
    lower_bound:    理論下界(あれば超過率を出す)。
    """
    rows = load_rows(archive_path)
    seeds = [r for r in rows if r["gen"] == 0]
    born = [r for r in rows if r["gen"] > 0]
    born.sort(key=lambda r: r["gen"])
    start = max((r["score"] for r in seeds), default=None)
    if baseline_score is None:
        baseline_score = start

    if gen_cap:
        born = [r for r in born if r["gen"] <= gen_cap]
    if candidate_cap:
        born = born[:candidate_cap]

    out: dict[str, Any] = {
        # A run-local result should not embed an absolute temporary/workspace
        # path.  Callers that aggregate external runs retain the real path;
        # the Forge loop supplies a stable archive_label for portable results.
        "archive_path": archive_label if archive_label is not None else str(archive_path),
        "seed_count": len(seeds),
        "alive_candidates": len(born),
        "generations_seen": max((r["gen"] for r in born), default=0),
        "baseline_score": baseline_score,
        "lower_bound": lower_bound,
    }
    if not born:
        _attach_efficiency_metrics(out, result)
        return out

    scores = [r["score"] for r in born]
    curve_c = best_so_far(scores, start)
    best = curve_c[-1]

    # --- 到達点 ---
    ranked = sorted(scores, reverse=True)
    out.update({
        "best_score": best,
        "best_excess_over_lb_pct": _pct(best, lower_bound),
        "top1": ranked[0],
        "top5_mean": statistics.fmean(ranked[:5]),
        "top10_mean": statistics.fmean(ranked[:10]),
        "beats_baseline": (best > baseline_score) if baseline_score is not None else None,
        "gain_over_baseline": (best - baseline_score) if baseline_score is not None else None,
    })

    # --- 軌跡 ---
    by_gen: dict[int, float] = {}
    for r in born:
        by_gen[r["gen"]] = max(by_gen.get(r["gen"], float("-inf")), r["score"])
    last_gen = max(by_gen)
    curve_g = best_so_far((by_gen.get(g, float("-inf")) for g in range(1, last_gen + 1)), start)
    out.update({
        "auc_by_candidate": _auc(curve_c),
        "auc_by_generation": _auc(curve_g),
        "auc_by_candidate_excess_pct": _pct(_auc(curve_c), lower_bound),
        "final_best_gen": next((r["gen"] for r in born if r["score"] == best), None),
        "largest_single_jump": max(
            (b - a for a, b in zip(curve_c, curve_c[1:]) if a > float("-inf")), default=0.0
        ),
    })
    if baseline_score is not None:
        idx = next((i for i, c in enumerate(curve_c, 1) if c > baseline_score), None)
        out["candidates_to_beat_baseline"] = idx  # None = 最後まで超えられなかった
        gidx = next((g for g, c in enumerate(curve_g, 1) if c > baseline_score), None)
        out["generations_to_beat_baseline"] = gidx

    # --- 区間ごとの収穫(世代を伸ばすかの判断に使う) ---
    bands = []
    cur = start if start is not None else float("-inf")
    for lo in range(1, last_gen + 1, band):
        hi = min(lo + band - 1, last_gen)
        ups, gained = 0, 0.0
        for g in range(lo, hi + 1):
            s = by_gen.get(g, float("-inf"))
            if s > cur:
                ups += 1
                gained += s - cur
                cur = s
        bands.append({
            "band": f"gen{lo}-{hi}", "generations": hi - lo + 1,
            "updates": ups, "hazard": ups / (hi - lo + 1), "points_gained": gained,
        })
    out["bands"] = bands

    # --- 探索の健全性 ---
    threshold = baseline_score * catastrophic_ratio if baseline_score is not None else None
    out.update({
        "distinct_scores": len(set(scores)),
        "distinct_scores_ratio": len(set(scores)) / len(scores),
        "score_p50": statistics.median(scores),
        "score_p90": ranked[max(0, int(len(ranked) * 0.1) - 1)],
        "catastrophic_rate": (
            sum(1 for s in scores if s < threshold) / len(scores)
            if threshold is not None else None
        ),
        "hits_over_baseline": (
            sum(1 for s in scores if s > baseline_score) if baseline_score is not None else None
        ),
        "hit_rate": (
            sum(1 for s in scores if s > baseline_score) / len(scores)
            if baseline_score is not None else None
        ),
        "islands_used": len({r.get("island", 0) for r in born}),
    })

    _attach_efficiency_metrics(out, result)
    return out


def summarise(values: list[float], *, power_n: tuple[int, ...] = (3, 5, 10, 20)) -> dict[str, Any]:
    """群の要約に**検出力**を必ず添える。

    平均と標準偏差だけ出すと「差が無かった」と「測れていなかった」を混同する。
    mde_at_n は、そのnで両側t検定(α=.05, 検出力80%)が拾える最小の群間差。
    伸びしろ全体より mde が大きいなら、その設計では何を変えても判定できない。
    """
    vals = [v for v in values if isinstance(v, (int, float))]
    if not vals:
        return {"n": 0}
    out: dict[str, Any] = {
        "n": len(vals),
        "mean": statistics.fmean(vals),
        "best": max(vals),
        "worst": min(vals),
        "stdev": statistics.stdev(vals) if len(vals) > 1 else 0.0,
    }
    sd = out["stdev"]
    out["mde_at_n"] = {
        str(n): (2.9 * sd / math.sqrt(n) * math.sqrt(2)) if sd else 0.0 for n in power_n
    }
    return out


def compare(control: list[dict[str, Any]], treatment: list[dict[str, Any]],
            keys: Iterable[str] | None = None) -> dict[str, Any]:
    """2群を全指標で突き合わせる。差が検出力の範囲内かどうかも返す。"""
    if keys is None:
        numeric = [
            "best_score", "auc_by_candidate", "auc_by_generation", "top5_mean", "top10_mean",
            "gain_over_baseline", "hit_rate", "distinct_scores", "distinct_scores_ratio",
            "catastrophic_rate", "alive_candidates", "best_excess_over_lb_pct",
            "candidates_to_beat_baseline", "alive_per_call", "wall_secs",
        ]
        keys = numeric
    out: dict[str, Any] = {}
    for key in keys:
        a = summarise([m.get(key) for m in control])
        b = summarise([m.get(key) for m in treatment])
        if not a["n"] or not b["n"]:
            continue
        diff = b["mean"] - a["mean"]
        n = min(a["n"], b["n"])
        mde = max(a["mde_at_n"][str(n)] if str(n) in a["mde_at_n"] else 0.0,
                  b["mde_at_n"][str(n)] if str(n) in b["mde_at_n"] else 0.0)
        out[key] = {
            "control": a, "treatment": b, "diff": diff,
            "mde_at_this_n": mde,
            # 差がMDEを超えていなければ、偶然と区別できない。
            "decisive": bool(mde) and abs(diff) > mde,
        }
    return out
