#!/usr/bin/env python3
"""Run every local problem pack once in mock mode and write a JSON verdict."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def discover_problem_packs(projects_root: Path) -> list[Path]:
    if not projects_root.is_dir():
        return []
    packs = []
    for path in sorted(projects_root.iterdir()):
        if path.is_dir() and (path / "config.json").is_file() and (path / "problem.py").is_file():
            packs.append(path)
    return packs


def archive_line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open(encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def run_pack(pack: Path, run_root: Path) -> dict:
    run_dir = run_root / pack.name
    env = os.environ.copy()
    env["FORGE_MOCK"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "cli.py"),
            str(pack.relative_to(ROOT)),
            "--mock",
            "--run-dir",
            str(run_dir),
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    archive = run_dir / "archive.jsonl"
    lines = archive_line_count(archive)
    return {
        "name": pack.name,
        "project": str(pack.relative_to(ROOT)),
        "returncode": result.returncode,
        "ok": result.returncode == 0 and lines >= 1,
        "run_dir": str(run_dir.relative_to(ROOT)),
        "archive_path": str(archive.relative_to(ROOT)),
        "archive_lines": lines,
        "stdout_tail": result.stdout.splitlines()[-20:],
        "stderr_tail": result.stderr.splitlines()[-20:],
    }


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-run all local forge problem packs in mock mode.")
    parser.add_argument("--run-root", default="runs/problem_pack_smoke")
    parser.add_argument("--artifact", default="artifacts/problem_pack_smoke.json")
    args = parser.parse_args()

    run_root = ROOT / args.run_root
    artifact = ROOT / args.artifact
    if run_root.exists():
        shutil.rmtree(run_root)
    run_root.mkdir(parents=True, exist_ok=True)

    packs = discover_problem_packs(ROOT / "projects")
    results = [run_pack(pack, run_root) for pack in packs]
    failed = sum(1 for item in results if not item["ok"])
    verdict = {
        "ok": bool(results) and failed == 0,
        "total": len(results),
        "failed": failed,
        "results": results,
    }
    write_json(artifact, verdict)
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    return 0 if verdict["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
