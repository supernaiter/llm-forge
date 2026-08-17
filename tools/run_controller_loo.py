#!/usr/bin/env python3
"""Run the mock leave-one-problem-out controller development matrix.

Each target problem is excluded from the trace set used to freeze its policy.
The command emits ``loo_summary.json`` with fold-specific manifests, target
exclusion evidence, paired policy replays, and mock-only comparisons.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forge.controller import SearchAction  # noqa: E402
from forge.development import REGISTERED_MECHANISMS, DevelopmentProblem  # noqa: E402
from forge.loo import LOO_MECHANISMS, PRIMARY_MECHANISM, FIXED_MECHANISM, run_leave_one_problem_out_matrix  # noqa: E402
from forge.protocol import ProtocolError, strict_json_loads  # noqa: E402


def _load_actions(path: Path) -> list[SearchAction]:
    try:
        raw = strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError(f"cannot read development actions: {path}") from exc
    if not isinstance(raw, list) or not raw:
        raise ProtocolError("development actions JSON must be a non-empty list")
    actions: list[SearchAction] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ProtocolError(f"development action {index} is not an object")
        try:
            actions.append(SearchAction(**{
                field: item[field] for field in SearchAction.__dataclass_fields__
            }))
        except KeyError as exc:
            raise ProtocolError(
                f"development action {index} is missing {exc.args[0]}"
            ) from exc
    return actions


def _parse_problem(value: str) -> DevelopmentProblem:
    if "=" not in value:
        raise ProtocolError("--problem must use problem_id=problem_dir")
    problem_id, problem_dir = value.split("=", 1)
    return DevelopmentProblem(problem_id.strip(), Path(problem_dir).resolve())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--problem", action="append", required=True,
        help="target/development problem as problem_id=local_problem_dir; repeatable",
    )
    parser.add_argument("--actions", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--generations", type=int, default=3)
    parser.add_argument("--max-attempts", type=int, default=4)
    parser.add_argument(
        "--seed", type=int, action="append", dest="seeds", required=True,
        help="development seed; provide at least three",
    )
    parser.add_argument(
        "--mechanism", action="append", dest="mechanisms",
        choices=tuple(dict.fromkeys((*LOO_MECHANISMS, *REGISTERED_MECHANISMS))),
        help="freeze only this registered mechanism (repeatable; default: all)",
    )
    args = parser.parse_args(argv)
    try:
        problems = [_parse_problem(value) for value in args.problem]
        actions = _load_actions(args.actions)
        summary = run_leave_one_problem_out_matrix(
            problems,
            actions,
            args.out,
            generations=args.generations,
            max_attempts=args.max_attempts,
            seeds=tuple(args.seeds),
            mechanisms=tuple(args.mechanisms or LOO_MECHANISMS),
        )
    except (OSError, ProtocolError, ValueError) as exc:
        print(f"DENIED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "out": str(args.out),
        "evaluation_design": summary["evaluation_design"],
        "problem_ids": [problem.problem_id for problem in problems],
        "seeds": summary["seeds"],
        "policy_replay_count": len(summary["policy_runs"]),
        "target_comparisons": summary["target_comparisons"],
        "aggregate_comparison": summary["aggregate_comparison"],
        "audit": summary["audit"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
