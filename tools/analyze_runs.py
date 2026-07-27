#!/usr/bin/env python3
"""走行群を全指標で分析し、2群の比較なら検出力つきで判定する。

archive.jsonl から計算するだけなので**無料・決定論的**。何度でもやり直せるし、
指標を選び直しても再走行は要らない。エージェントが改善を回すときの土台。

使い方:
  # 1群を全指標で見る
  python3 tools/analyze_runs.py 'runs/bench/bench_obp/20260726/seed*'

  # 2群を比較する(検出力つき)
  python3 tools/analyze_runs.py --control 'runs/bench/*_ctl/*/seed*' \
                                --treatment 'runs/bench/*_trt/*/seed*'

  # 条件を揃える(API失敗のばらつきを消したいときは候補数で揃える)
  python3 tools/analyze_runs.py --candidate-cap 200 ...
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forge.metrics import compare, run_metrics, summarise  # noqa: E402


def find_baselines(run_dir: Path) -> dict[str, Any]:
    """走行が実際に使ったパックから基準値を読む(A/Bは別名コピーで走るため)。"""
    manifest = run_dir / "manifest.json"
    if not manifest.is_file():
        return {}
    try:
        project = json.loads(manifest.read_text(encoding="utf-8")).get("project")
    except json.JSONDecodeError:
        return {}
    if not project:
        return {}
    path = Path(project)
    if not path.is_absolute():
        path = ROOT / path
    bl = path / "baselines.json"
    if not bl.is_file():
        return {}
    try:
        return json.loads(bl.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def scale_facts(baselines: dict[str, Any], mock: bool) -> tuple[float | None, float | None]:
    """(基準ヒューリスティックのscore, 理論下界) を返す。"""
    scale = baselines.get("scales", {}).get("mock" if mock else "full", {})
    lb = scale.get("l1_lower_bound_mean")
    entries = scale.get("baselines", {})
    scores = [e.get("score") for e in entries.values() if isinstance(e.get("score"), (int, float))]
    return (max(scores) if scores else None), lb


def collect(patterns: list[str], gen_cap: int | None,
            candidate_cap: int | None) -> list[dict[str, Any]]:
    out = []
    for pattern in patterns:
        for d in sorted(glob.glob(pattern)):
            run_dir = Path(d)
            archive = run_dir / "archive.jsonl"
            if not archive.is_file():
                continue
            result = None
            rp = run_dir / "result.json"
            if rp.is_file():
                try:
                    result = json.loads(rp.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    result = None
            manifest = {}
            mp = run_dir / "manifest.json"
            if mp.is_file():
                try:
                    manifest = json.loads(mp.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    manifest = {}
            base, lb = scale_facts(find_baselines(run_dir), bool(manifest.get("mock")))
            m = run_metrics(archive, result=result, baseline_score=base, lower_bound=lb,
                            gen_cap=gen_cap, candidate_cap=candidate_cap)
            m["run"] = str(run_dir)
            m["seed"] = manifest.get("seed")
            out.append(m)
    return out


def fmt(v: Any, digits: int = 4) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "はい" if v else "いいえ"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


def print_group(metrics: list[dict[str, Any]], keys: list[str]) -> None:
    print(f"走行 {len(metrics)} 本\n")
    print(f"{'指標':<32}{'平均':>13}{'最良':>13}{'標準偏差':>11}{'MDE(n=3)':>11}{'MDE(n=10)':>11}")
    for key in keys:
        s = summarise([m.get(key) for m in metrics])
        if not s["n"]:
            continue
        mde = s["mde_at_n"]
        print(f"{key:<32}{fmt(s['mean']):>13}{fmt(s['best']):>13}"
              f"{fmt(s['stdev']):>11}{fmt(mde['3']):>11}{fmt(mde['10']):>11}")


def print_compare(ctl: list[dict[str, Any]], trt: list[dict[str, Any]]) -> None:
    res = compare(ctl, trt)
    print(f"対照群 {len(ctl)} 本 vs 実験群 {len(trt)} 本\n")
    print(f"{'指標':<32}{'対照':>13}{'実験':>13}{'差':>12}{'MDE':>11}  判定")
    for key, r in res.items():
        mark = "**有意**" if r["decisive"] else "偶然と区別できない"
        print(f"{key:<32}{fmt(r['control']['mean']):>13}{fmt(r['treatment']['mean']):>13}"
              f"{r['diff']:>+12.4f}{fmt(r['mde_at_this_n']):>11}  {mark}")
    print("\nMDEは、そのシード数で拾える最小の群間差。差がMDE以下なら「効果が無い」ではなく")
    print("「測れていない」。判定を出すにはシードを増やすか、分散の小さい指標を使う。")


DEFAULT_KEYS = [
    "best_score", "best_excess_over_lb_pct", "auc_by_candidate", "auc_by_generation",
    "top5_mean", "top10_mean", "gain_over_baseline", "candidates_to_beat_baseline",
    "hit_rate", "distinct_scores", "distinct_scores_ratio", "catastrophic_rate",
    "largest_single_jump", "final_best_gen", "alive_candidates", "alive_per_call",
    "gain_per_call", "cheap_failure_rate", "wall_secs",
]


def main() -> int:
    ap = argparse.ArgumentParser(description="Analyse forge runs with the full metric set.")
    ap.add_argument("patterns", nargs="*", help="走行ディレクトリのglob")
    ap.add_argument("--control", action="append", default=[])
    ap.add_argument("--treatment", action="append", default=[])
    ap.add_argument("--gen-cap", type=int, help="この世代までで揃える")
    ap.add_argument("--candidate-cap", type=int,
                    help="この生存候補数までで揃える(API失敗のばらつきを消せる)")
    ap.add_argument("--json", help="全指標をこのファイルへJSONで書く")
    args = ap.parse_args()

    payload: dict[str, Any] = {}
    if args.control or args.treatment:
        ctl = collect(args.control, args.gen_cap, args.candidate_cap)
        trt = collect(args.treatment, args.gen_cap, args.candidate_cap)
        if not ctl or not trt:
            print("対照群または実験群の走行が見つかりません", file=sys.stderr)
            return 2
        print_compare(ctl, trt)
        payload = {"control": ctl, "treatment": trt, "compare": compare(ctl, trt)}
    else:
        if not args.patterns:
            ap.error("走行のglobか --control/--treatment を指定してください")
        metrics = collect(args.patterns, args.gen_cap, args.candidate_cap)
        if not metrics:
            print("走行が見つかりません", file=sys.stderr)
            return 2
        print_group(metrics, DEFAULT_KEYS)
        payload = {"runs": metrics}

    if args.json:
        Path(args.json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
