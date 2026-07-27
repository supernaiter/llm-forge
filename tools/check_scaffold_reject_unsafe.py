#!/usr/bin/env python3
"""Verify scaffold_problem_pack.py rejects unsafe pack names without writes."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCAFFOLD = ROOT / "tools" / "scaffold_problem_pack.py"

UNSAFE_NAMES = [
    "../evil",
    "a/b",
    "a\\b",
    "",
    ".",
    "bad name",
]


def _relative_paths(root: Path) -> set[str]:
    if not root.exists():
        return set()
    return {str(path.relative_to(root)) for path in root.rglob("*")}


def check() -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    rejected_cases = 0
    created_paths: set[str] = set()

    with tempfile.TemporaryDirectory(prefix="forge_scaffold_reject_") as tmp:
        sandbox = Path(tmp)
        projects_root = sandbox / "projects"
        before = _relative_paths(sandbox)

        for name in UNSAFE_NAMES:
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCAFFOLD),
                    "--name",
                    name,
                    "--root",
                    str(projects_root),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            rejected = result.returncode == 2
            if rejected:
                rejected_cases += 1
            results.append(
                {
                    "name": name,
                    "returncode": result.returncode,
                    "rejected": rejected,
                }
            )

        after = _relative_paths(sandbox)
        created_paths = after - before

    verdict = {
        "ok": rejected_cases == len(UNSAFE_NAMES) and len(created_paths) == 0,
        "cases": len(UNSAFE_NAMES),
        "rejected_cases": rejected_cases,
        "created_paths": len(created_paths),
        "created_path_samples": sorted(created_paths)[:20],
        "results": results,
    }
    return verdict


def _write_verdict(path: str | None, verdict: dict[str, Any]) -> None:
    text = json.dumps(verdict, ensure_ascii=False, indent=2, sort_keys=True)
    if path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check unsafe scaffold names fail closed.")
    parser.add_argument("--out", help="write JSON verdict to this path")
    args = parser.parse_args(argv)

    verdict = check()
    _write_verdict(args.out, verdict)
    return 0 if verdict["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
