#!/usr/bin/env python3
"""Run a local Forge development matrix and freeze controller policies.

Example::

    python tools/run_controller_development.py \
      --problem stringmax=projects/stringmax \
      --problem probe=projects/_probe_newproblem \
      --actions actions.json --out runs/controller-dev

The command is intentionally mock-only.  It is for producing development
traces and reviewing controller behavior, not for generating holdout evidence.
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
from forge.development import (  # noqa: E402
    DevelopmentProblem,
    REGISTERED_MECHANISMS,
    run_development_matrix,
)
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
        help="development problem as problem_id=local_problem_dir; repeatable",
    )
    parser.add_argument("--actions", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--generations", type=int, default=3)
    parser.add_argument("--max-attempts", type=int)
    parser.add_argument(
        "--seed", type=int, action="append", dest="seeds",
        help="development seed; repeat for independent local runs (default: 0)",
    )
    parser.add_argument(
        "--mechanism", action="append", dest="mechanisms",
        choices=tuple(REGISTERED_MECHANISMS) + (
            "TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V2",
        ),
        help="freeze only this registered mechanism (repeatable; default: all)",
    )
    args = parser.parse_args(argv)
    try:
        problems = [_parse_problem(value) for value in args.problem]
        actions = _load_actions(args.actions)
        summary = run_development_matrix(
            problems,
            actions,
            args.out,
            generations=args.generations,
            max_attempts=args.max_attempts,
            seeds=tuple(args.seeds or (0,)),
            mechanisms=tuple(args.mechanisms or REGISTERED_MECHANISMS),
        )
    except (OSError, ProtocolError, ValueError) as exc:
        print(f"DENIED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "out": str(args.out),
        "trace_count": sum(row["trace_count"] for row in summary["runs"]),
        "problem_ids": [problem.problem_id for problem in problems],
        "seeds": summary["seeds"],
        "policy_replay_count": len(summary["policy_runs"]),
        "policy_mechanisms": summary["policy_mechanisms"],
        "policies": {
            mechanism: {
                "policy_sha256": row["policy_sha256"],
                "selected_action": row["selected_action"],
            }
            for mechanism, row in summary["policies"].items()
        },
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
