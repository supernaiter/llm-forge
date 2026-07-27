#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _tail(text: str, limit: int = 40) -> list[str]:
    return text.splitlines()[-limit:]


def _archive_evidence(started_at: float, run_dir: Path | None = None) -> dict[str, Any]:
    if run_dir is not None:
        archive_paths = [run_dir / "archive.jsonl"]
    else:
        archive_paths = sorted((ROOT / "runs").glob("_probe_newproblem-*/archive.jsonl"))
    fresh = [
        path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.as_posix()
        for path in archive_paths
        if path.is_file() and path.stat().st_mtime >= started_at - 1.0 and path.stat().st_size > 0
    ]
    latest = []
    if archive_paths:
        latest_path = max(archive_paths, key=lambda path: path.stat().st_mtime)
        if latest_path.is_file():
            latest = [
                latest_path.relative_to(ROOT).as_posix()
                if latest_path.is_relative_to(ROOT)
                else latest_path.as_posix()
            ]
    return {
        "archive_files": fresh or latest,
        "fresh_archive_count": len(fresh),
        "used_temp_run_dir": run_dir is not None,
    }


def _bench_evidence(stdout: str) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    for line in stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in {"sequential_secs", "parallel_secs", "ratio"}:
            try:
                evidence[key] = float(value)
            except ValueError:
                evidence[key] = value
    if "ratio" in evidence:
        evidence["ratio_passed"] = evidence["ratio"] <= 0.5
    return evidence


def run_check(
    name: str,
    command: list[str],
    env: dict[str, str] | None = None,
    probe_run_dir: Path | None = None,
) -> dict[str, Any]:
    started_at = time.time()
    proc = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, **(env or {})},
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    artifacts: dict[str, Any] = {}
    if name == "probe_newproblem":
        artifacts = _archive_evidence(started_at, probe_run_dir)
    elif name == "bench_v0":
        artifacts = _bench_evidence(proc.stdout)

    passed = proc.returncode == 0
    if name == "probe_newproblem":
        passed = passed and bool(artifacts.get("archive_files"))
    if name == "bench_v0":
        passed = passed and artifacts.get("ratio_passed") is True

    return {
        "name": name,
        "command": " ".join(command),
        "exit_code": proc.returncode,
        "passed": passed,
        "stdout_tail": _tail(proc.stdout),
        "stderr_tail": _tail(proc.stderr),
        "artifacts": artifacts,
    }


def verify(probe_run_dir: Path | None = None) -> dict[str, Any]:
    probe_command = ["python3", "cli.py", "projects/_probe_newproblem", "--mock"]
    if probe_run_dir is not None:
        probe_command.extend(["--run-dir", str(probe_run_dir)])
    checks = [
        run_check(
            "pytest",
            ["python3", "-m", "pytest", "tests/", "-x", "-q"],
            env={"FORGE_SKIP_WORKSPACE_CLEAN_TEST": "1"} if probe_run_dir is not None else None,
        ),
        run_check(
            "probe_newproblem",
            probe_command,
            env={"FORGE_MOCK": "1"},
            probe_run_dir=probe_run_dir,
        ),
        run_check("bench_v0", ["python3", "tests/bench_v0.py"]),
    ]
    return {
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run north-star verification checks.")
    parser.add_argument(
        "--out",
        "--artifact",
        dest="out",
        type=Path,
        required=True,
        help="write verification JSON to this path",
    )
    parser.add_argument(
        "--probe-run-dir",
        type=Path,
        help="write the _probe_newproblem run artifacts outside the default repo runs/ path",
    )
    args = parser.parse_args(argv)

    verdict = verify(args.probe_run_dir)
    write_json(args.out, verdict)
    return 0 if verdict["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
