#!/usr/bin/env python3
"""多シードベンチ走行を集計し、基準値との比較表を reports/ に書き出す。

入力: runs/bench/<pack>/<date>/seed*/{result.json,manifest.json,archive.jsonl}
      projects/<pack>/baselines.json
出力: reports/benchmark_<pack>_<date>.md と同名 .json

「1回だけ走らせて出た良い値」を実力と誤認しないために、必ず全シードの
平均・標準偏差・最良を並べて出す。基準値との差は平均で見る。
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def rel(path: Path) -> str:
    """表示用の相対パス。FORGE_REAL_RUNS_DIRでリポ外に出している場合は絶対パスのまま返す。"""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def best_from_archive(path: Path) -> dict[str, Any] | None:
    """archive.jsonl の最良行を返す。Archiveはtop-K順で追記されるが順序に依存しない。"""
    best = None
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict) or "score" not in item:
                continue
            if best is None or item["score"] > best["score"]:
                best = item
    return best


def hazard_by_band(path: Path, band: int = 20) -> list[dict[str, Any]]:
    """best-so-far が更新された世代の割合と、区間ごとの獲得点を返す。

    best_score だけを見ると、走行が伸びしろを使い切ったのか運が悪かったのかを
    区別できない。更新ハザード(1世代あたりの更新確率)は、分母が生存候補数に
    依存しないため世代を延ばしたときの収穫逓減を直接読める
    (2026-07-25 adversarial-panel の裁定: 主指標は best_score ではなくこれ)。
    """
    if not path.is_file():
        return []
    by_gen: dict[int, float] = {}
    for line in path.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        gen, score = item.get("gen"), item.get("score")
        if not isinstance(gen, int) or not isinstance(score, (int, float)):
            continue
        by_gen[gen] = max(by_gen.get(gen, float("-inf")), float(score))
    if not by_gen:
        return []

    last_gen = max(by_gen)
    best = by_gen.get(0, float("-inf"))
    updates: dict[int, list[float]] = {}
    for gen in range(1, last_gen + 1):
        gained = 0.0
        if gen in by_gen and by_gen[gen] > best:
            gained = by_gen[gen] - best
            best = by_gen[gen]
        updates.setdefault((gen - 1) // band, []).append(gained)

    out = []
    for idx in sorted(updates):
        gains = updates[idx]
        hits = [g for g in gains if g > 0]
        out.append({
            "band": f"gen{idx * band + 1}-{idx * band + len(gains)}",
            "generations": len(gains),
            "updates": len(hits),
            "hazard": len(hits) / len(gains) if gains else 0.0,
            "points_gained": sum(gains),
        })
    return out


def collect_seed_runs(bench_dir: Path) -> list[dict[str, Any]]:
    runs = []
    for run_dir in sorted(bench_dir.glob("seed*"), key=lambda p: p.name):
        if not run_dir.is_dir():
            continue
        result = read_json(run_dir / "result.json")
        manifest = read_json(run_dir / "manifest.json") or {}
        if result is None:
            runs.append({
                "run_dir": rel(run_dir),
                "seed": manifest.get("seed"),
                "ok": False,
                "reason": "missing result.json (run crashed or was interrupted)",
            })
            continue
        best = best_from_archive(run_dir / "archive.jsonl")
        runs.append({
            "run_dir": rel(run_dir),
            "seed": manifest.get("seed"),
            "mock": manifest.get("mock"),
            "project": manifest.get("project"),
            "ok": True,
            "best_score": result.get("best_score"),
            "generations_done": result.get("generations_done"),
            "cheap_used": result.get("cheap_used"),
            "cheap_failed": result.get("cheap_failed"),
            "archive_distinct_scores": result.get("archive_distinct_scores"),
            "smart_used": result.get("smart_used"),
            "wall_secs": result.get("wall_secs"),
            "stopped_by": result.get("stopped_by"),
            "hazard": hazard_by_band(run_dir / "archive.jsonl"),
            "best_text": (best or {}).get("text"),
        })
    return runs


def pick_scale(baselines: dict[str, Any], mock: bool) -> tuple[str, dict[str, Any]]:
    scales = baselines.get("scales", {})
    key = "mock" if mock else "full"
    return key, scales.get(key, {})


def excess_pct(score: float, l1_mean: float | None) -> float | None:
    """スコアが -(平均使用bin数) の問題でのみ意味を持つ。下界が無い問題ではNone。"""
    if l1_mean is None or l1_mean <= 0:
        return None
    return 100.0 * ((-score) - l1_mean) / l1_mean


def find_baselines(pack: str, runs: list[dict[str, Any]]) -> dict[str, Any]:
    """基準値は走行が実際に使ったパックから読む。

    A/B(tools/run_ab.sh)はパックを別名でコピーして走らせるので、パック名から
    projects/ を引くだけでは基準値が見つからない。manifest.json が記録している
    実際のパックパスを優先し、無ければ projects/<pack>/ にフォールバックする。
    """
    for run in runs:
        project = run.get("project")
        if not project:
            continue
        path = Path(project)
        if not path.is_absolute():
            path = ROOT / path
        found = read_json(path / "baselines.json")
        if found:
            return found
    return read_json(ROOT / "projects" / pack / "baselines.json") or {}


def build_report(pack: str, date: str, runs_dir: Path) -> dict[str, Any]:
    bench_dir = runs_dir / "bench" / pack / date
    runs = collect_seed_runs(bench_dir)
    baselines = find_baselines(pack, runs)
    ok_runs = [r for r in runs if r.get("ok") and isinstance(r.get("best_score"), (int, float))]
    scores = [float(r["best_score"]) for r in ok_runs]
    mock = bool(ok_runs and ok_runs[0].get("mock"))
    scale_key, scale = pick_scale(baselines, mock)
    l1 = scale.get("l1_lower_bound_mean")

    summary: dict[str, Any] = {
        "seeds_total": len(runs),
        "seeds_ok": len(ok_runs),
        "seeds_failed": len(runs) - len(ok_runs),
    }
    if scores:
        summary.update({
            "best": max(scores),
            "mean": statistics.fmean(scores),
            "stdev": statistics.stdev(scores) if len(scores) > 1 else 0.0,
            "worst": min(scores),
            "cheap_used_total": sum(r.get("cheap_used") or 0 for r in ok_runs),
            "wall_secs_total": sum(r.get("wall_secs") or 0.0 for r in ok_runs),
        })
        summary["best_excess_over_l1_pct"] = excess_pct(summary["best"], l1)
        summary["mean_excess_over_l1_pct"] = excess_pct(summary["mean"], l1)

    return {
        "pack": pack,
        "date": date,
        "bench_dir": rel(bench_dir),
        "mock": mock,
        "scale": scale_key,
        "scale_params": {k: v for k, v in scale.items() if isinstance(v, (int, float))},
        "metric": baselines.get("metric"),
        "baselines": scale.get("baselines", {}),
        "reference_not_reachable": scale.get("reference_not_reachable_by_construction", {}),
        # 文献値はfullスケール前提の数字なので、縮小スケールのmock走行では並べない
        # (並べると配管確認の数字を論文値と比較できるかのように見せてしまう)。
        "literature_reference_unverified": (
            {} if mock else baselines.get("literature_reference_unverified", {})
        ),
        "summary": summary,
        "runs": runs,
    }


def fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        if math.isinf(value):
            return "-inf" if value < 0 else "inf"
        return f"{value:.{digits}f}"
    return str(value)


def render_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        f"# ベンチ結果 {report['pack']} / {report['date']}",
        "",
        f"- 指標: {report.get('metric') or '（baselines.json未定義）'}",
        f"- スケール: **{report['scale']}**"
        + (" （mock走行。配管確認用であり実力の測定値ではない）" if report["mock"] else ""),
        f"- シード: {s['seeds_ok']}/{s['seeds_total']} 成功",
        "",
    ]
    if s["seeds_failed"]:
        lines += [f"> 警告: {s['seeds_failed']}シードが result.json を残さず終了している。", ""]

    if s.get("seeds_ok"):
        lines += [
            "## forge の成績",
            "",
            "| 指標 | 値 |",
            "|---|---|",
            f"| 最良 | {fmt(s['best'])} |",
            f"| 平均 | {fmt(s['mean'])} |",
            f"| 標準偏差 | {fmt(s['stdev'])} |",
            f"| 最悪 | {fmt(s['worst'])} |",
        ]
        if s.get("mean_excess_over_l1_pct") is not None:
            lines += [
                f"| 最良のL1下界超過 | {fmt(s['best_excess_over_l1_pct'])}% |",
                f"| 平均のL1下界超過 | {fmt(s['mean_excess_over_l1_pct'])}% |",
            ]
        lines += [
            f"| cheap呼び出し合計 | {s.get('cheap_used_total')} |",
            f"| 実時間合計(秒) | {fmt(s.get('wall_secs_total'), 1)} |",
            "",
        ]

    bands: dict[str, list[dict[str, Any]]] = {}
    for r in report["runs"]:
        for h in r.get("hazard") or []:
            bands.setdefault(h["band"], []).append(h)
    if len(bands) > 1:
        lines += [
            "## 収穫の推移（best-so-far の更新ハザード）",
            "",
            "後半の区間でハザードと獲得点が落ちていれば、世代を伸ばしても収穫は尽きている。",
            "best_score は運の影響が大きいので、世代を伸ばすかどうかはこちらで判断する。",
            "",
            "| 区間 | 更新回数(合計) | 更新確率/世代 | 獲得点(合計) | 獲得点/走行 |",
            "|---|---|---|---|---|",
        ]
        for band in sorted(bands, key=lambda b: int(b.split("-")[0][3:])):
            items = bands[band]
            gens = sum(i["generations"] for i in items)
            ups = sum(i["updates"] for i in items)
            pts = sum(i["points_gained"] for i in items)
            lines.append(
                f"| {band} | {ups} | {fmt(ups / gens if gens else 0.0, 3)} | "
                f"{fmt(pts, 1)} | {fmt(pts / len(items), 1)} |"
            )
        lines.append("")

    if report["baselines"]:
        lines += ["## 基準値との比較", "", "| 手法 | score | forge平均との差 |", "|---|---|---|"]
        mean = s.get("mean")
        for name, item in report["baselines"].items():
            base_score = item.get("score")
            delta = (
                fmt(mean - base_score)
                if isinstance(mean, float) and isinstance(base_score, (int, float))
                else "—"
            )
            lines.append(f"| {name} | {fmt(base_score)} | {delta} |")
        lines.append("")

    if report["reference_not_reachable"]:
        lines += ["## 参考（この問題設定では到達不能）", ""]
        for name, item in report["reference_not_reachable"].items():
            lines.append(f"- {name}: {item.get('mean_tour', item)} — {item.get('note', '')}")
        lines.append("")

    lit = report["literature_reference_unverified"]
    if lit:
        lines += ["## 文献参照値（未検証）", "", f"{lit.get('note', '')}", ""]
        for k, v in lit.items():
            if k in ("note", "citation"):
                continue
            lines.append(f"- {k}: {v}")
        if lit.get("citation"):
            lines += ["", f"出典: {lit['citation']}"]
        lines.append("")

    # scores列 = 親プール内の異なるスコア数。1〜3なら探索が閉ループに入っている
    # (2026-07-25 bench_obp実測: 1〜3のシードは改善ゼロ、10のシードだけが改善した)。
    lines += ["## シード別", "",
              "| seed | best_score | scores | 世代 | cheap | 空振り | 秒 | 停止理由 |",
              "|---|---|---|---|---|---|---|---|"]
    for r in report["runs"]:
        if not r.get("ok"):
            lines.append(f"| {r.get('seed')} | 失敗 | — | — | — | — | — | {r.get('reason')} |")
            continue
        lines.append(
            f"| {r.get('seed')} | {fmt(r.get('best_score'))} | "
            f"{r.get('archive_distinct_scores') if r.get('archive_distinct_scores') is not None else '—'} | "
            f"{r.get('generations_done')} | {r.get('cheap_used')} | "
            f"{r.get('cheap_failed') if r.get('cheap_failed') is not None else '—'} | "
            f"{fmt(r.get('wall_secs'), 1)} | {r.get('stopped_by')} |"
        )
    lines.append("")

    best_run = max(
        (r for r in report["runs"] if r.get("ok") and r.get("best_text")),
        key=lambda r: r["best_score"],
        default=None,
    )
    if best_run:
        lines += ["## 最良候補", "", f"seed={best_run['seed']} score={fmt(best_run['best_score'])}",
                  "", "```python", best_run["best_text"].strip(), "```", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarise a multi-seed benchmark session.")
    parser.add_argument("pack", help="problem pack name under projects/ (e.g. bench_obp)")
    parser.add_argument("--date", required=True, help="session date directory, YYYYMMDD")
    parser.add_argument("--runs-dir", default="runs")
    parser.add_argument("--reports-dir", default="reports")
    args = parser.parse_args()

    runs_dir = Path(args.runs_dir)
    if not runs_dir.is_absolute():
        runs_dir = ROOT / runs_dir
    report = build_report(args.pack, args.date, runs_dir)

    reports_dir = Path(args.reports_dir)
    if not reports_dir.is_absolute():
        reports_dir = ROOT / reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)
    stem = f"benchmark_{args.pack}_{args.date}"
    (reports_dir / f"{stem}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    md = render_markdown(report)
    (reports_dir / f"{stem}.md").write_text(md + "\n", encoding="utf-8")
    print(md)
    return 0 if report["summary"]["seeds_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
