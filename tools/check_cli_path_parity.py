#!/usr/bin/env python3
"""Verify mock CLI output parity for relative and absolute problem-pack paths."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "projects" / "stringmax"


def display_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def archive_line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open(encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def run_cli(problem_dir: str, run_dir: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["FORGE_MOCK"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "cli.py"),
            problem_dir,
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


def check() -> dict[str, Any]:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    work_root = ROOT / "artifacts" / "cli_path_parity_runs" / f"run-{stamp}-{os.getpid()}"
    relative_run = work_root / "relative"
    absolute_run = work_root / "absolute"

    relative = run_cli("projects/stringmax", relative_run)
    absolute = run_cli(str(PROJECT), absolute_run)

    relative_archive = relative_run / "archive.jsonl"
    absolute_archive = absolute_run / "archive.jsonl"
    relative_manifest = relative_run / "manifest.json"
    absolute_manifest = absolute_run / "manifest.json"
    archive_counts = {
        "relative": archive_line_count(relative_archive),
        "absolute": archive_line_count(absolute_archive),
    }
    manifest_exists = {
        "relative": relative_manifest.is_file(),
        "absolute": absolute_manifest.is_file(),
    }
    ok = (
        relative.returncode == 0
        and absolute.returncode == 0
        and archive_counts["relative"] >= 1
        and archive_counts["absolute"] >= 1
        and manifest_exists["relative"]
        and manifest_exists["absolute"]
    )
    return {
        "verdict": "pass" if ok else "fail",
        "project": {
            "relative": "projects/stringmax",
            "absolute": str(PROJECT),
        },
        "run_dirs": {
            "relative": display_path(relative_run),
            "absolute": display_path(absolute_run),
        },
        "relative_exit": relative.returncode,
        "absolute_exit": absolute.returncode,
        "manifest_exists": manifest_exists,
        "archive_line_counts": archive_counts,
        "stdout_tail": {
            "relative": relative.stdout.splitlines()[-10:],
            "absolute": absolute.stdout.splitlines()[-10:],
        },
        "stderr_tail": {
            "relative": relative.stderr.splitlines()[-10:],
            "absolute": absolute.stderr.splitlines()[-10:],
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check mock CLI relative/absolute path parity.")
    parser.add_argument("--json", default="artifacts/cli_path_parity.json")
    args = parser.parse_args(argv)

    path = Path(args.json)
    if not path.is_absolute():
        path = ROOT / path
    verdict = check()
    write_json(path, verdict)
    print(json.dumps(verdict, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if verdict["verdict"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
