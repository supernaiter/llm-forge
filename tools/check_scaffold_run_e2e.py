#!/usr/bin/env python3
"""Scaffold fresh problem packs, validate them, and run them through the CLI."""
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
SCAFFOLD = ROOT / "tools" / "scaffold_problem_pack.py"
VALIDATOR = ROOT / "tools" / "validate_problem_pack.py"
PACK_NAMES = ("_scaffold_alpha", "_scaffold_beta", "_scaffold_gamma")


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


def run_command(args: list[str], *, env: dict[str, str] | None = None, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def check_pack(name: str, packs_root: Path, runs_root: Path) -> dict[str, Any]:
    scaffold = run_command(
        [
            sys.executable,
            str(SCAFFOLD),
            "--name",
            name,
            "--root",
            str(packs_root),
        ]
    )
    project = packs_root / name

    validate = run_command([sys.executable, str(VALIDATOR), str(project)]) if scaffold.returncode == 0 else None

    run_dir = runs_root / name
    env = os.environ.copy()
    env["FORGE_MOCK"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    cli = (
        run_command(
            [
                sys.executable,
                str(ROOT / "cli.py"),
                str(project),
                "--mock",
                "--run-dir",
                str(run_dir),
            ],
            env=env,
            timeout=30,
        )
        if validate is not None and validate.returncode == 0
        else None
    )

    archive = run_dir / "archive.jsonl"
    archive_lines = archive_line_count(archive)
    ok = (
        scaffold.returncode == 0
        and validate is not None
        and validate.returncode == 0
        and cli is not None
        and cli.returncode == 0
        and archive.is_file()
        and archive_lines >= 1
    )
    return {
        "name": name,
        "project": display_path(project),
        "run_dir": display_path(run_dir),
        "scaffold_exit": scaffold.returncode,
        "validate_exit": validate.returncode if validate is not None else None,
        "cli_exit": cli.returncode if cli is not None else None,
        "archive_path": display_path(archive),
        "archive_exists": archive.is_file(),
        "archive_lines": archive_lines,
        "ok": ok,
        "stdout_tail": (cli.stdout.splitlines()[-10:] if cli is not None else scaffold.stdout.splitlines()[-10:]),
        "stderr_tail": (
            cli.stderr.splitlines()[-10:]
            if cli is not None
            else (validate.stderr.splitlines()[-10:] if validate is not None else scaffold.stderr.splitlines()[-10:])
        ),
    }


def check() -> dict[str, Any]:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    work_root = ROOT / "artifacts" / "scaffold_run_e2e_runs" / f"run-{stamp}-{os.getpid()}"
    packs_root = work_root / "packs"
    runs_root = work_root / "runs"
    packs = [check_pack(name, packs_root, runs_root) for name in PACK_NAMES]
    failed = sum(1 for pack in packs if not pack["ok"])
    return {
        "verdict": "pass" if failed == 0 and len(packs) == 3 else "fail",
        "pack_count": len(packs),
        "failed": failed,
        "work_root": display_path(work_root),
        "packs": packs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check scaffolded problem packs through validate and mock CLI.")
    parser.add_argument("--json", default="artifacts/scaffold_run_e2e.json")
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
