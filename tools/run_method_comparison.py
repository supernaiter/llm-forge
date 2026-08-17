#!/usr/bin/env python3
"""Run the matched-model LLM4AD comparison matrix.

Example::

    python tools/run_method_comparison.py \
      --problem bench_obp=projects/bench_obp \
      --problem bench_tsp=projects/bench_tsp \
      --policy-dir /tmp/forge-controller-dev \
      --out artifacts/method-comparison \
      --scale full --seed 0 --seed 1 --seed 2

The controller policies must have been frozen on development problems before
this command is run.  The target packs are never used to fit them.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forge.comparison import run_method_comparison  # noqa: E402
from forge.development import DevelopmentProblem  # noqa: E402
from forge.protocol import ProtocolError  # noqa: E402


def _parse_problem(value: str) -> DevelopmentProblem:
    if "=" not in value:
        raise ProtocolError("--problem must use problem_id=problem_dir")
    problem_id, problem_dir = value.split("=", 1)
    path = Path(problem_dir).expanduser().resolve()
    if not problem_id.strip() or not path.is_dir():
        raise ProtocolError(f"invalid problem pack: {value}")
    return DevelopmentProblem(problem_id.strip(), path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--problem", action="append", required=True)
    parser.add_argument("--policy-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scale", choices=("mock", "full"), default="full")
    parser.add_argument("--seed", type=int, action="append", dest="seeds", required=True)
    args = parser.parse_args(argv)
    try:
        summary = run_method_comparison(
            [_parse_problem(value) for value in args.problem],
            args.out,
            seeds=tuple(args.seeds),
            policy_dir=args.policy_dir,
            scale=args.scale,
        )
    except (OSError, ProtocolError, ValueError) as exc:
        print(f"DENIED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "output_dir": summary["output_dir"],
        "manifest_sha256": summary["manifest_sha256"],
        "results_sha256": summary["results_sha256"],
        "summary_sha256": summary["summary_sha256"],
        "fairness_pass": summary["fairness_pass"],
        "row_count": len(summary["rows"]),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
