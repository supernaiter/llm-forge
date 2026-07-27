#!/usr/bin/env python3
"""Verify --run-dir keeps CLI artifacts out of repo-local runs/."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROJECT = "projects/_probe_newproblem"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            files[path.relative_to(root).as_posix()] = _sha256(path)
    return files


def _write_json(path: Path | None, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if path is None:
        print(text)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")


def check() -> dict[str, Any]:
    repo_runs = ROOT / "runs"
    before = _snapshot(repo_runs)

    with tempfile.TemporaryDirectory(prefix="forge_runs_dir_isolation_") as tmp:
        run_dir = Path(tmp) / "isolated_run"
        env = os.environ.copy()
        env["FORGE_MOCK"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "cli.py"),
                PROJECT,
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

        archive_path = run_dir / "archive.jsonl"
        manifest_path = run_dir / "manifest.json"
        isolated_archive_exists = archive_path.is_file()
        isolated_manifest_exists = manifest_path.is_file()
        archive_lines = (
            [line for line in archive_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            if isolated_archive_exists
            else []
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if isolated_manifest_exists else {}

    after = _snapshot(repo_runs)
    before_keys = set(before)
    after_keys = set(after)
    repo_runs_new_files = sorted(after_keys - before_keys)
    repo_runs_changed_files = sorted(path for path in before_keys & after_keys if before[path] != after[path])
    repo_runs_deleted_files = sorted(before_keys - after_keys)

    passed = (
        proc.returncode == 0
        and not repo_runs_new_files
        and not repo_runs_changed_files
        and not repo_runs_deleted_files
        and isolated_archive_exists
        and isolated_manifest_exists
        and len(archive_lines) > 0
    )
    return {
        "passed": passed,
        "repo_runs_new_files": len(repo_runs_new_files),
        "repo_runs_new_file_paths": repo_runs_new_files,
        "repo_runs_changed_files": repo_runs_changed_files,
        "repo_runs_deleted_files": repo_runs_deleted_files,
        "isolated_archive_exists": isolated_archive_exists,
        "isolated_manifest_exists": isolated_manifest_exists,
        "isolated_archive_lines": len(archive_lines),
        "manifest_archive_path_is_isolated": bool(manifest.get("archive_path", "").endswith("/archive.jsonl")),
        "manifest_project": manifest.get("project"),
        "cli_returncode": proc.returncode,
        "stdout_tail": proc.stdout.splitlines()[-10:],
        "stderr_tail": proc.stderr.splitlines()[-10:],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check CLI --run-dir isolation from repo-local runs/.")
    parser.add_argument("--out", help="write JSON verdict to this path")
    args = parser.parse_args(argv)

    verdict = check()
    _write_json(Path(args.out) if args.out else None, verdict)
    return 0 if verdict["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
