#!/usr/bin/env python3
"""2本のベンチ結果JSONを突き合わせ、A/B比較markdownを書き出す。

forge本体の変更が効いたかどうかは、変更前後を同条件で並べないと分からない。
1本ずつ別の日に走らせた数字を比べても、モデル側の混み具合が変われば意味を失う。
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def seed_scores(report: dict[str, Any]) -> dict[Any, float]:
    return {
        r.get("seed"): r["best_score"]
        for r in report.get("runs", [])
        if r.get("ok") and isinstance(r.get("best_score"), (int, float))
    }


def render(label_a: str, a: dict[str, Any], label_b: str, b: dict[str, Any],
           knob: str, value_a: str, value_b: str) -> str:
    sa, sb = a["summary"], b["summary"]
    scores_a, scores_b = seed_scores(a), seed_scores(b)
    shared = sorted(set(scores_a) & set(scores_b), key=lambda s: (s is None, s))

    lines = [
        f"# A/B比較 {a['pack'].removesuffix('_ctl')} / {a['date']}",
        "",
        f"（対照群のパック名: `{a['pack']}` / 実験群: `{b['pack']}`）",
        "",
        f"- 変えたもの: `{knob}` = **{value_a}**（{label_a}） vs **{value_b}**（{label_b}）",
        f"- 指標: {a.get('metric') or '—'}（大きいほど良い）",
        f"- スケール: {a.get('scale')}"
        + ("（mock走行。配管確認用であり実力の測定値ではない）" if a.get("mock") else ""),
        "",
        "## まとめ",
        "",
        f"| | {label_a} ({knob}={value_a}) | {label_b} ({knob}={value_b}) | 差 |",
        "|---|---|---|---|",
    ]

    def row(name: str, key: str, digits: int = 4, higher_better: bool = True) -> str:
        va, vb = sa.get(key), sb.get(key)
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            diff = vb - va
            mark = ""
            if higher_better and abs(diff) > 1e-12:
                mark = " ✓" if diff > 0 else " ✗"
            delta = f"{diff:+.{digits}f}{mark}"
        else:
            delta = "—"
        return f"| {name} | {fmt(va, digits)} | {fmt(vb, digits)} | {delta} |"

    lines += [
        row("平均", "mean"),
        row("最良", "best"),
        row("最悪", "worst"),
        row("標準偏差", "stdev", higher_better=False),
        row("成功シード数", "seeds_ok", digits=0),
    ]
    if sa.get("mean_excess_over_l1_pct") is not None:
        lines.append(row("平均のL1下界超過(%)", "mean_excess_over_l1_pct", higher_better=False))
    lines.append("")

    if shared:
        lines += [
            "## シード別（同じシード同士で比較）",
            "",
            f"| seed | {label_a} | {label_b} | 差 | 空振り {label_a}→{label_b} | 秒 {label_a}→{label_b} |",
            "|---|---|---|---|---|---|",
        ]
        runs_a = {r.get("seed"): r for r in a.get("runs", [])}
        runs_b = {r.get("seed"): r for r in b.get("runs", [])}
        wins = 0
        for s in shared:
            diff = scores_b[s] - scores_a[s]
            wins += diff > 0
            wa, wb = runs_a[s].get("wall_secs"), runs_b[s].get("wall_secs")
            ratio = f" ({wb / wa:.2f}倍)" if isinstance(wa, (int, float)) and wa else ""
            lines.append(
                f"| {s} | {fmt(scores_a[s])} | {fmt(scores_b[s])} | {diff:+.4f} | "
                f"{runs_a[s].get('cheap_failed', '—')} → {runs_b[s].get('cheap_failed', '—')} | "
                f"{fmt(wa, 0)} → {fmt(wb, 0)}{ratio} |"
            )
        lines += ["", f"{label_b}が勝ったシード: **{wins} / {len(shared)}**", ""]
        if len(shared) > 1:
            paired = [scores_b[s] - scores_a[s] for s in shared]
            lines += [
                f"シード毎の差の平均 {statistics.fmean(paired):+.4f}、"
                f"ばらつき {statistics.stdev(paired):.4f}",
                "",
            ]

    lines += [
        "## 読み方の注意",
        "",
        f"- シード数が{len(shared) or sa.get('seeds_ok', 0)}本しかない。"
        "差が標準偏差より小さいなら、それは偶然と区別できない。",
        "- `scores` 列は親プール内の異なるスコアの数。ここが1〜3に潰れている走行は"
        "探索が閉ループに入っている。効果の機序を見るならスコアより先にこの列を見る。",
        "",
        f"- 元データ: `{a.get('bench_dir')}` / `{b.get('bench_dir')}`",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare two benchmark report JSONs.")
    parser.add_argument("report_a")
    parser.add_argument("report_b")
    parser.add_argument("--label-a", default="対照群")
    parser.add_argument("--label-b", default="実験群")
    parser.add_argument("--knob", default="config")
    parser.add_argument("--value-a", default="A")
    parser.add_argument("--value-b", default="B")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    a = json.loads(Path(args.report_a).read_text(encoding="utf-8"))
    b = json.loads(Path(args.report_b).read_text(encoding="utf-8"))
    md = render(args.label_a, a, args.label_b, b, args.knob, args.value_a, args.value_b)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md + "\n", encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
