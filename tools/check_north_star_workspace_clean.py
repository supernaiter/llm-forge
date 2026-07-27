#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "artifacts" / "north_star_workspace_clean.json"
ALLOWED_REPO_PATHS = {"artifacts/north_star_workspace_clean.json"}


def _snapshot() -> set[str]:
    paths: set[str] = set()
    watch_roots = [ROOT / "runs"]
    for root in watch_roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            rel = path.relative_to(ROOT).as_posix()
            if path.is_file() and (rel.startswith("runs/_probe_newproblem-") or rel.startswith("runs/manifest_probe")):
                paths.add(rel)
    return paths


def _tail(text: str, limit: int = 30) -> list[str]:
    return text.splitlines()[-limit:]


def check(out: Path = DEFAULT_OUT) -> dict[str, Any]:
    before = _snapshot()
    out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="forge-north-star-runs-") as tmp:
        tmp_path = Path(tmp)
        temp_runs_dir = tmp_path / "runs" / "_probe_newproblem"
        temp_artifact = tmp_path / "north_star_verify.json"
        proc = subprocess.run(
            [
                "python3",
                "tools/verify_north_star.py",
                "--out",
                str(temp_artifact),
                "--probe-run-dir",
                str(temp_runs_dir),
            ],
            cwd=ROOT,
            env={**os.environ, "FORGE_SKIP_WORKSPACE_CLEAN_TEST": "1"},
            text=True,
            capture_output=True,
            timeout=240,
            check=False,
        )
        temp_archive_exists = (temp_runs_dir / "archive.jsonl").is_file()
        temp_manifest_exists = (temp_runs_dir / "manifest.json").is_file()
        temp_verdict: dict[str, Any] | None = None
        if temp_artifact.is_file():
            temp_verdict = json.loads(temp_artifact.read_text(encoding="utf-8"))

    after = _snapshot()
    created = sorted(after - before)
    changed_or_created = [
        rel
        for rel in created
        if rel not in ALLOWED_REPO_PATHS
    ]
    passed = (
        proc.returncode == 0
        and temp_archive_exists
        and temp_manifest_exists
        and not changed_or_created
        and bool(temp_verdict and temp_verdict.get("passed") is True)
    )
    payload: dict[str, Any] = {
        "verdict": "pass" if passed else "fail",
        "used_temp_runs_dir": True,
        "unexpected_repo_paths": changed_or_created,
        "verify_exit_code": proc.returncode,
        "temp_archive_exists": temp_archive_exists,
        "temp_manifest_exists": temp_manifest_exists,
        "stdout_tail": _tail(proc.stdout),
        "stderr_tail": _tail(proc.stderr),
    }
    if temp_verdict is not None:
        payload["north_star_passed"] = temp_verdict.get("passed") is True
        if temp_verdict.get("passed") is not True:
            payload["north_star_checks"] = temp_verdict.get("checks", [])

    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify north-star checks do not create repo run artifacts.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    payload = check(args.out)
    return 0 if payload["verdict"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
