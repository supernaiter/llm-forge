#!/usr/bin/env python3
"""Smoke test that an external problem pack is only read during mock CLI runs."""
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


PROBLEM_SOURCE = """
import os
from pathlib import Path


class Problem:
    DESCRIPTION = "external readonly probe"

    def __init__(self):
        root = os.environ.get("FORGE_DATA_ROOT")
        if not root:
            raise RuntimeError("FORGE_DATA_ROOT is required")
        self.data_path = Path(root) / "seed.txt"

    def seed(self):
        return [self.data_path.read_text(encoding="utf-8").strip()]

    def score(self, cand: str):
        expected = self.data_path.read_text(encoding="utf-8").strip()
        cand = cand.strip()
        if not cand or len(cand) > 120:
            return float("-inf"), False
        return float(len(cand) + (cand == expected)), True
"""


CONFIG = {
    "generations": 1,
    "batch_size": 1,
    "max_cheap_calls": 2,
    "max_smart_calls": 0,
    "archive_capacity": 5,
    "parents": 1,
    "seed": 17,
    "workers": 1,
    "temperature": 0.7,
}


def _write_json(path: Path | None, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if path is None:
        print(text)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot(root: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            files[path.relative_to(root).as_posix()] = _sha256(path)
    return files


def _diff(before: dict[str, str], after: dict[str, str]) -> dict[str, list[str]]:
    before_keys = set(before)
    after_keys = set(after)
    common = before_keys & after_keys
    return {
        "changed_files": sorted(path for path in common if before[path] != after[path]),
        "created_files": sorted(after_keys - before_keys),
        "deleted_files": sorted(before_keys - after_keys),
    }


def _write_probe_pack(project: Path, data_root: Path) -> None:
    project.mkdir(parents=True)
    data_root.mkdir(parents=True)
    (project / "problem.py").write_text(PROBLEM_SOURCE.lstrip(), encoding="utf-8")
    (project / "config.json").write_text(json.dumps(CONFIG, indent=2) + "\n", encoding="utf-8")
    (data_root / "seed.txt").write_text("external readonly seed\n", encoding="utf-8")


def check() -> dict[str, Any]:
    before_run_dirs = set((ROOT / "runs").glob("external_readonly_probe-*"))
    with tempfile.TemporaryDirectory(prefix="forge_external_pack_") as tmp:
        external_root = Path(tmp)
        project = external_root / "external_readonly_probe"
        data_root = external_root / "data"
        _write_probe_pack(project, data_root)
        before = _snapshot(external_root)

        env = os.environ.copy()
        env["FORGE_MOCK"] = "1"
        env["FORGE_DATA_ROOT"] = str(data_root)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        proc = subprocess.run(
            [sys.executable, str(ROOT / "cli.py"), str(project), "--mock"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )

        after = _snapshot(external_root)
        diff = _diff(before, after)

    archive_files = sorted(
        path.relative_to(ROOT).as_posix()
        for run_dir in set((ROOT / "runs").glob("external_readonly_probe-*")) - before_run_dirs
        for path in [run_dir / "archive.jsonl"]
        if path.is_file()
    )
    passed = (
        proc.returncode == 0
        and not diff["changed_files"]
        and not diff["created_files"]
        and not diff["deleted_files"]
        and bool(archive_files)
    )
    return {
        "passed": passed,
        **diff,
        "archive_files": archive_files,
        "cli_returncode": proc.returncode,
        "stdout_tail": proc.stdout.splitlines()[-10:],
        "stderr_tail": proc.stderr.splitlines()[-10:],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check external problem pack read-only behavior.")
    parser.add_argument("--json", dest="json_path", help="write JSON verdict to this path")
    args = parser.parse_args(argv)

    verdict = check()
    _write_json(Path(args.json_path) if args.json_path else None, verdict)
    return 0 if verdict["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
