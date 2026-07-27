#!/usr/bin/env python3
"""Check invalid problem packs fail closed under the CLI."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "cli.py"


CASES: dict[str, dict[str, str | None]] = {
    "broken_problem_py": {
        "config": "{}",
        "problem": "class Problem:\n    def seed(self):\n        return [\n",
    },
    "score_missing": {
        "config": "{}",
        "problem": """
class Problem:
    DESCRIPTION = "score missing"

    def seed(self):
        return ["seed"]
""",
    },
    "missing_config": {
        "config": None,
        "problem": """
from pathlib import Path


class Problem:
    DESCRIPTION = "missing config"

    def __init__(self):
        if not (Path(__file__).with_name("config.json")).is_file():
            raise RuntimeError("config.json missing")

    def seed(self):
        return ["seed"]

    def score(self, cand: str):
        return 1.0, True
""",
    },
    "write_attempt": {
        "config": "{}",
        "problem": """
import os
from pathlib import Path


class Problem:
    DESCRIPTION = "write attempt"

    def seed(self):
        Path(os.environ["FORGE_FORBIDDEN_WRITE_TARGET"]).write_text("boom", encoding="utf-8")
        return ["seed"]

    def score(self, cand: str):
        return 1.0, True
""",
    },
}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _archive_line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _write_pack(path: Path, case: dict[str, str | None]) -> None:
    path.mkdir(parents=True)
    if case["config"] is not None:
        (path / "config.json").write_text(str(case["config"]), encoding="utf-8")
    if case["problem"] is not None:
        (path / "problem.py").write_text(str(case["problem"]).lstrip(), encoding="utf-8")


def _run_case(name: str, pack: Path, run_dir: Path, forbidden_target: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env["FORGE_MOCK"] = "1"
    env["FORGE_FORBIDDEN_WRITE_TARGET"] = str(forbidden_target)
    result = subprocess.run(
        [
            sys.executable,
            str(CLI),
            str(pack),
            "--mock",
            "--run-dir",
            str(run_dir),
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    archive = run_dir / "archive.jsonl"
    archive_lines = _archive_line_count(archive)
    return {
        "name": name,
        "returncode": result.returncode,
        "cli_exit_nonzero": result.returncode != 0,
        "success_archive_lines": archive_lines,
        "write_target_exists": forbidden_target.exists(),
        "archive_path": str(archive),
        "stdout_tail": result.stdout.splitlines()[-20:],
        "stderr_tail": result.stderr.splitlines()[-20:],
    }


def check() -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="forge_pack_fail_closed_") as tmp:
        root = Path(tmp)
        packs_root = root / "packs"
        runs_root = root / "runs"
        forbidden_dir = root / "readonly"
        forbidden_dir.mkdir()
        forbidden_dir.chmod(0o555)
        forbidden_target = forbidden_dir / "forbidden.txt"
        for name, case in CASES.items():
            pack = packs_root / name
            run_dir = runs_root / name
            _write_pack(pack, case)
            results.append(_run_case(name, pack, run_dir, forbidden_target))

    passed = (
        len(results) == len(CASES)
        and all(item["cli_exit_nonzero"] for item in results)
        and all(item["success_archive_lines"] == 0 for item in results)
        and all(item["write_target_exists"] is False for item in results)
    )
    failed = sum(
        1
        for item in results
        if (
            not item["cli_exit_nonzero"]
            or item["success_archive_lines"] != 0
            or item["write_target_exists"] is not False
        )
    )
    return {
        "passed": passed,
        "case_count": len(results),
        "failed": failed,
        "cases": results,
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check invalid problem packs fail closed.")
    parser.add_argument("--out", default="artifacts/problem_pack_fail_closed.json")
    args = parser.parse_args(argv)

    verdict = check()
    _write_json(ROOT / args.out, verdict)
    print(json.dumps(verdict, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if verdict["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
