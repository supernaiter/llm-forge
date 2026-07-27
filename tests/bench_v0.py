#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forge.loop import score_candidates


class SleepProblem:
    def __init__(self, delay: float):
        self.delay = delay

    def score(self, cand: str):
        time.sleep(self.delay)
        return float(len(cand)), True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", dest="json_path", type=Path)
    args = parser.parse_args()

    problem = SleepProblem(1.0)
    candidates = [f"cand-{i}" for i in range(8)]

    start = time.perf_counter()
    score_candidates(problem, candidates, workers=1)
    sequential_secs = time.perf_counter() - start

    start = time.perf_counter()
    score_candidates(problem, candidates, workers=8)
    parallel_secs = time.perf_counter() - start

    ratio = parallel_secs / sequential_secs if sequential_secs else float("inf")
    print(f"sequential_secs={sequential_secs:.3f}")
    print(f"parallel_secs={parallel_secs:.3f}")
    print(f"ratio={ratio:.3f}")
    passed = ratio <= 0.5
    if args.json_path:
        args.json_path.parent.mkdir(parents=True, exist_ok=True)
        args.json_path.write_text(
            json.dumps(
                {
                    "sequential_seconds": sequential_secs,
                    "parallel_seconds": parallel_secs,
                    "ratio": ratio,
                    "passed": passed,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
