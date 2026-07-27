#!/usr/bin/env python3
"""Generate dynamic out-of-sample problem packs and run each through the CLI."""
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


BASE_CONFIG = {
    "generations": 1,
    "batch_size": 1,
    "max_cheap_calls": 2,
    "max_smart_calls": 0,
    "archive_capacity": 5,
    "parents": 1,
    "seed": 29,
    "workers": 1,
    "temperature": 0.7,
}


PACKS = {
    "text": '''
class Problem:
    DESCRIPTION = "dynamic text OOS probe"

    def seed(self):
        return ["alpha probe"]

    def score(self, cand: str):
        cand = cand.strip()
        if not cand or len(cand) > 120:
            return float("-inf"), False
        return float(len(set(cand)) + len(cand) / 100), True
''',
    "digits": '''
class Problem:
    DESCRIPTION = "dynamic digit OOS probe"

    def seed(self):
        return ["12321"]

    def score(self, cand: str):
        cand = "".join(ch for ch in cand if ch.isdigit())
        if not cand:
            return float("-inf"), False
        symmetry = sum(1 for a, b in zip(cand, reversed(cand)) if a == b)
        return float(symmetry + len(cand) / 100), True
''',
    "params": '''
class Problem:
    DESCRIPTION = "dynamic param OOS probe"

    def param_space(self):
        return {"x": (0.0, 10.0), "y": (0.0, 10.0)}

    def seed_params(self):
        return [{"x": 3.0, "y": 4.0}]

    def render(self, params):
        return f"x={params['x']:.3f}, y={params['y']:.3f}"

    def seed(self):
        return [self.render(self.seed_params()[0])]

    def score(self, cand: str):
        try:
            parts = dict(piece.strip().split("=") for piece in cand.split(","))
            x = float(parts["x"])
            y = float(parts["y"])
        except Exception:
            return float("-inf"), False
        return 100.0 - abs(x - 7.0) - abs(y - 2.0), True
''',
}


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


def write_pack(pack_dir: Path, source: str, seed: int) -> None:
    pack_dir.mkdir(parents=True, exist_ok=False)
    config = dict(BASE_CONFIG)
    config["seed"] = seed
    (pack_dir / "problem.py").write_text(source.lstrip(), encoding="utf-8")
    (pack_dir / "config.json").write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_pack(pack_dir: Path, run_dir: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env["FORGE_MOCK"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "cli.py"),
            str(pack_dir),
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
    lines = archive_line_count(archive)
    return {
        "name": pack_dir.name,
        "project": display_path(pack_dir),
        "run_dir": display_path(run_dir),
        "returncode": proc.returncode,
        "archive_path": display_path(archive),
        "archive_exists": archive.is_file(),
        "archive_lines": lines,
        "ok": proc.returncode == 0 and archive.is_file() and lines >= 1,
        "stdout_tail": proc.stdout.splitlines()[-10:],
        "stderr_tail": proc.stderr.splitlines()[-10:],
    }


def check() -> dict[str, Any]:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    root = ROOT / "artifacts" / "oos_probe_matrix_runs" / f"run-{stamp}-{os.getpid()}"
    packs_root = root / "packs"
    runs_root = root / "runs"
    results = []
    for idx, (kind, source) in enumerate(PACKS.items(), start=1):
        pack_dir = packs_root / f"_oos_{kind}_{stamp}_{idx}"
        run_dir = runs_root / pack_dir.name
        write_pack(pack_dir, source, seed=29 + idx)
        results.append(run_pack(pack_dir, run_dir))
    failed = sum(1 for item in results if not item["ok"])
    return {
        "passed": failed == 0 and len(results) == 3,
        "pack_count": len(results),
        "failed": failed,
        "work_root": display_path(root),
        "packs": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check dynamic OOS problem packs through forge CLI.")
    parser.add_argument("--json", default="artifacts/oos_probe_matrix.json")
    args = parser.parse_args(argv)

    verdict = check()
    path = Path(args.json)
    if not path.is_absolute():
        path = ROOT / path
    write_json(path, verdict)
    print(json.dumps(verdict, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if verdict["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
