#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import forge.loop as loop


class _Problem:
    DESCRIPTION = "resume dedup verifier"

    def seed(self):
        return ["already archived candidate"]

    def score(self, candidate: str):
        return float(len(candidate)), bool(candidate)


def run_check(out: Path) -> dict:
    archived = "already archived candidate"
    generated = [archived]

    with tempfile.TemporaryDirectory(prefix="forge-resume-dedup-") as tmp:
        run_dir = Path(tmp)
        (run_dir / "archive.jsonl").write_text(
            json.dumps({"text": archived, "score": 10.0, "gen": 0}) + "\n",
            encoding="utf-8",
        )

        original_make_caller = loop.make_caller
        original_v0 = loop.v0
        v0_calls: list[str] = []

        def fake_caller(prompt: str, temperature: float) -> str:
            return f"```\n{generated[0]}\n```"

        def counting_v0(problem, candidate: str):
            v0_calls.append(candidate)
            return original_v0(problem, candidate)

        try:
            loop.make_caller = lambda tier: fake_caller
            loop.v0 = counting_v0
            loop.run(
                _Problem(),
                {
                    "generations": 1,
                    "batch_size": 1,
                    "max_cheap_calls": 1,
                    "max_smart_calls": 0,
                    "archive_capacity": 10,
                    "parents": 1,
                    "workers": 1,
                    "seed": 0,
                    "dedup_hamming": 3,
                },
                str(run_dir),
            )
        finally:
            loop.make_caller = original_make_caller
            loop.v0 = original_v0

        archive_lines = [
            line
            for line in (run_dir / "archive.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    new_v0_evaluations = len(v0_calls)
    duplicate_candidates = len(generated) - new_v0_evaluations
    passed = (
        len(archive_lines) == 1
        and duplicate_candidates >= 1
        and new_v0_evaluations == 0
    )
    verdict = {
        "verdict": "pass" if passed else "fail",
        "resume_archive_items": len(archive_lines),
        "generated_candidates": len(generated),
        "duplicate_candidates": duplicate_candidates,
        "new_v0_evaluations": new_v0_evaluations,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(verdict, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return verdict


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    verdict = run_check(args.out)
    print(json.dumps(verdict, ensure_ascii=False, sort_keys=True))
    return 0 if verdict["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
