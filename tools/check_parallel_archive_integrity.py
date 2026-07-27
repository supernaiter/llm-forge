#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forge.archive import Archive
from forge.dedup import ExactDedup
from forge.loop import score_candidates
from forge.sandbox import SandboxTimeout


class _MixedProblem:
    DESCRIPTION = "parallel archive integrity verifier"

    def score(self, candidate: str):
        if candidate == "ok-alpha":
            time.sleep(0.03)
            return 10.0, True
        if candidate == "ok-duplicate":
            time.sleep(0.01)
            return 11.0, True
        if candidate == "ok-beta":
            return 12.0, True
        if candidate == "raises":
            raise RuntimeError("candidate crashed")
        if candidate == "timeout":
            raise SandboxTimeout("timeout after 0.1s")
        return float("-inf"), False


def _read_jsonl(path: Path) -> tuple[bool, list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    return False, items
                items.append(value)
    except (OSError, json.JSONDecodeError):
        return False, items
    return True, items


def run_check(out: Path) -> dict[str, Any]:
    candidates = [
        "ok-alpha",
        "ok-duplicate",
        "ok-duplicate",
        "raises",
        "timeout",
        "dead",
        "ok-beta",
    ]
    dedup = ExactDedup()
    novel: list[str] = []
    duplicate_candidates = 0
    for candidate in candidates:
        if not dedup.is_novel(candidate):
            duplicate_candidates += 1
            continue
        novel.append(candidate)

    with tempfile.TemporaryDirectory(prefix="forge-parallel-archive-integrity-") as tmp:
        archive_path = Path(tmp) / "archive.jsonl"
        archive = Archive(str(archive_path), capacity=20)
        scored = score_candidates(_MixedProblem(), novel, workers=6)
        successful_items = [
            {"text": candidate, "score": score, "gen": 1}
            for candidate, score, alive in scored
            if alive
        ]
        for item in successful_items:
            archive.add(item)

        jsonl_valid, archive_items = _read_jsonl(archive_path)
        reloaded = Archive(str(archive_path), capacity=20)

    successful_candidates = len(successful_items)
    failed_v0_candidates = len(novel) - successful_candidates
    failed_candidates = duplicate_candidates + failed_v0_candidates
    archive_lines = len(archive_items)
    archive_texts = [item.get("text") for item in archive_items]
    successful_texts = [item["text"] for item in successful_items]
    passed = (
        jsonl_valid
        and archive_lines == successful_candidates
        and archive_texts == successful_texts
        and len(reloaded.items) == successful_candidates
        and failed_candidates >= 2
    )
    verdict = {
        "verdict": "pass" if passed else "fail",
        "jsonl_valid": jsonl_valid,
        "archive_lines": archive_lines,
        "successful_candidates": successful_candidates,
        "failed_candidates": failed_candidates,
        "duplicate_candidates": duplicate_candidates,
        "failed_v0_candidates": failed_v0_candidates,
        "candidate_count": len(candidates),
        "novel_candidates": len(novel),
        "workers": 6,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(verdict, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return verdict


def main() -> int:
    parser = argparse.ArgumentParser(description="Check parallel candidate failures keep archive JSONL intact.")
    parser.add_argument("--out", default="artifacts/parallel_archive_integrity.json", type=Path)
    args = parser.parse_args()
    out = args.out if args.out.is_absolute() else ROOT / args.out
    verdict = run_check(out)
    print(json.dumps(verdict, ensure_ascii=False, sort_keys=True))
    return 0 if verdict["verdict"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
