#!/usr/bin/env python3
from __future__ import annotations

import argparse
import itertools
import json
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import forge.loop as loop


class _Problem:
    DESCRIPTION = "parallel budget cap verifier"

    def seed(self):
        return ["seed"]

    def score(self, candidate: str):
        if candidate == "seed":
            return 1.0, True
        if candidate in {"candidate-001", "candidate-002"}:
            return 10.0 + int(candidate.rsplit("-", 1)[1]), True
        return float("-inf"), False


def _line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def run_check(out: Path) -> dict[str, Any]:
    budget = 3
    requested_batch_size = 8
    generated_counter = itertools.count(1)
    generated_lock = threading.Lock()
    generated_candidates: list[str] = []
    v0_candidates: list[str] = []

    original_make_caller = loop.make_caller
    original_v0 = loop.v0

    def fake_caller(prompt: str, temperature: float) -> str:
        del prompt, temperature
        with generated_lock:
            candidate = f"candidate-{next(generated_counter):03d}"
            generated_candidates.append(candidate)
        return f"```\n{candidate}\n```"

    def counting_v0(problem, candidate: str):
        if candidate.startswith("candidate-"):
            v0_candidates.append(candidate)
        return original_v0(problem, candidate)

    with tempfile.TemporaryDirectory(prefix="forge-parallel-budget-") as tmp:
        run_dir = Path(tmp)
        try:
            loop.make_caller = lambda tier: fake_caller
            loop.v0 = counting_v0
            loop.run(
                _Problem(),
                {
                    "generations": 1,
                    "batch_size": requested_batch_size,
                    "max_cheap_calls": budget,
                    "max_smart_calls": 0,
                    "archive_capacity": 20,
                    "parents": 1,
                    "workers": 8,
                    "seed": 0,
                    "dedup_hamming": 0,
                    "temperature": 0.7,
                },
                str(run_dir),
            )
        finally:
            loop.make_caller = original_make_caller
            loop.v0 = original_v0

        archive_lines = _line_count(run_dir / "archive.jsonl")
        result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))

    v0_evaluations = len(v0_candidates)
    passed = (
        result["cheap_used"] == budget
        and len(generated_candidates) <= budget
        and archive_lines <= budget
        and v0_evaluations <= budget
    )
    verdict = {
        "verdict": "pass" if passed else "fail",
        "budget": budget,
        "requested_batch_size": requested_batch_size,
        "generated_candidates": len(generated_candidates),
        "archive_lines": archive_lines,
        "v0_evaluations": v0_evaluations,
        "cheap_used": result["cheap_used"],
        "stopped_by": result["stopped_by"],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(verdict, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return verdict


def main() -> int:
    parser = argparse.ArgumentParser(description="Check parallel V0 respects cheap-call budget cap.")
    parser.add_argument("--out", default="artifacts/parallel_budget_cap.json", type=Path)
    args = parser.parse_args()
    out = args.out if args.out.is_absolute() else ROOT / args.out
    verdict = run_check(out)
    print(json.dumps(verdict, ensure_ascii=False, sort_keys=True))
    return 0 if verdict["verdict"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
