#!/usr/bin/env python3
"""Validate the minimal problem pack contract."""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType
from typing import Any


def _json_default(value: Any) -> str:
    return repr(value)


def _write_verdict(path: str | None, verdict: dict[str, Any]) -> None:
    text = json.dumps(verdict, ensure_ascii=False, indent=2, sort_keys=True, default=_json_default)
    if path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def _load_problem_module(project: Path, errors: list[str]) -> ModuleType | None:
    problem_py = project / "problem.py"
    if not problem_py.is_file():
        errors.append("missing problem.py")
        return None

    try:
        return importlib.import_module("problem")
    except Exception as exc:  # pragma: no cover - exact message varies by Python.
        errors.append(f"problem import failed: {exc}")
        return None


def _check_config(project: Path, errors: list[str]) -> dict[str, Any] | None:
    config_path = project / "config.json"
    if not config_path.is_file():
        errors.append("missing config.json")
        return None
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"config.json invalid: {exc}")
        return None
    if not isinstance(cfg, dict):
        errors.append("config.json must contain a JSON object")
        return None
    return cfg


def _call_seed(problem: Any, errors: list[str]) -> list[Any]:
    seed = getattr(problem, "seed", None)
    if not callable(seed):
        errors.append("Problem.seed must be callable")
        return []
    try:
        seeds = seed()
    except Exception as exc:
        errors.append(f"Problem.seed failed: {exc}")
        return []
    if isinstance(seeds, (str, bytes)) or not isinstance(seeds, Iterable):
        errors.append("Problem.seed must return a non-string iterable")
        return []
    seed_list = list(seeds)
    if not seed_list:
        errors.append("Problem.seed must return at least one item")
    for index, item in enumerate(seed_list):
        if not isinstance(item, str) or not item:
            errors.append(f"seed[{index}] must be a non-empty string")
    return seed_list


def _check_score(problem: Any, seeds: list[Any], errors: list[str]) -> int:
    score = getattr(problem, "score", None)
    if not callable(score):
        errors.append("Problem.score must be callable")
        return 0

    checks = 0
    for index, seed in enumerate(seeds):
        if not isinstance(seed, str) or not seed:
            continue
        try:
            result = score(seed)
        except Exception as exc:
            errors.append(f"Problem.score failed for seed[{index}]: {exc}")
            continue
        if not isinstance(result, tuple) or len(result) != 2:
            errors.append(f"Problem.score for seed[{index}] must return (score, alive)")
            continue
        value, alive = result
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            errors.append(f"Problem.score value for seed[{index}] must be numeric")
            continue
        if not isinstance(alive, bool):
            errors.append(f"Problem.score alive for seed[{index}] must be bool")
            continue
        if not alive:
            errors.append(f"seed[{index}] must pass Problem.score")
            continue
        checks += 1
    if seeds and checks == 0:
        errors.append("Problem.score must accept at least one seed")
    return checks


def validate(project: Path) -> dict[str, Any]:
    errors: list[str] = []
    project = project.resolve()
    old_path = list(sys.path)
    old_problem = sys.modules.pop("problem", None)
    project_str = str(project)
    # forgeリポルート(このファイルのtools/../)をsys.pathに含める。
    # problem.pyがforge.sandbox等forge本体をimportするパック(mc_fusion/bone_gate等)は
    # これが無いとimport失敗する(cwd=forgeルートで呼ばれてもcwdは自動的にsys.pathに入らない)。
    forge_root_str = str(Path(__file__).resolve().parents[1])

    seed_count = 0
    score_check_count = 0
    cfg = None

    try:
        if forge_root_str not in sys.path:
            sys.path.insert(0, forge_root_str)
        if project_str not in sys.path:
            sys.path.insert(0, project_str)

        if not project.is_dir():
            errors.append("project path must be a directory")

        cfg = _check_config(project, errors) if project.is_dir() else None
        mod = _load_problem_module(project, errors) if project.is_dir() else None

        if mod is not None:
            problem_cls = getattr(mod, "Problem", None)
            if not isinstance(problem_cls, type):
                errors.append("problem.py must define class Problem")
            else:
                description = getattr(problem_cls, "DESCRIPTION", None)
                if not isinstance(description, str) or not description.strip():
                    errors.append("Problem.DESCRIPTION must be a non-empty string")
                try:
                    problem = problem_cls()
                except Exception as exc:
                    errors.append(f"Problem() failed: {exc}")
                else:
                    seeds = _call_seed(problem, errors)
                    seed_count = len(seeds)
                    score_check_count = _check_score(problem, seeds, errors)
    finally:
        sys.path[:] = old_path
        sys.modules.pop("problem", None)
        if old_problem is not None:
            sys.modules["problem"] = old_problem

    return {
        "ok": not errors,
        "project": str(project),
        "config_present": cfg is not None,
        "seed_count": seed_count,
        "score_check_count": score_check_count,
        "error_count": len(errors),
        "errors": errors,
    }


VALID_PROBLEM_SOURCE = """
class Problem:
    DESCRIPTION = "valid problem"

    def seed(self):
        return ["seed"]

    def score(self, cand: str):
        return float(len(cand)), True
"""


def _write_fixture(project: Path, *, config: str | None, problem_source: str | None) -> None:
    project.mkdir(parents=True)
    if config is not None:
        (project / "config.json").write_text(config, encoding="utf-8")
    if problem_source is not None:
        (project / "problem.py").write_text(problem_source, encoding="utf-8")


def validate_negative_fixtures() -> dict[str, Any]:
    fixtures: dict[str, tuple[str | None, str | None]] = {
        "missing_config": (None, VALID_PROBLEM_SOURCE),
        "missing_problem_py": ("{}", None),
        "invalid_json": ("{not-json", VALID_PROBLEM_SOURCE),
        "missing_callables": (
            "{}",
            """
class Problem:
    DESCRIPTION = "missing callables"
""",
        ),
    }

    results = []
    false_accepts = 0
    with tempfile.TemporaryDirectory(prefix="forge_pack_negative_") as tmp:
        root = Path(tmp)
        for name, (config, problem_source) in fixtures.items():
            project = root / name
            _write_fixture(project, config=config, problem_source=problem_source)
            verdict = validate(project)
            accepted = bool(verdict["ok"])
            if accepted:
                false_accepts += 1
            results.append(
                {
                    "name": name,
                    "accepted": accepted,
                    "error_count": verdict["error_count"],
                    "errors": verdict["errors"],
                }
            )

    return {
        "ok": false_accepts == 0,
        "cases": len(results),
        "false_accepts": false_accepts,
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a forge problem pack.")
    parser.add_argument("project", nargs="?", help="problem pack directory")
    parser.add_argument("--json", "--out", dest="json_path", help="write JSON verdict to this path")
    parser.add_argument(
        "--negative-fixtures",
        action="store_true",
        help="run built-in invalid problem pack fixtures and report false accepts",
    )
    args = parser.parse_args(argv)

    if args.negative_fixtures:
        verdict = validate_negative_fixtures()
    else:
        if args.project is None:
            parser.error("project is required unless --negative-fixtures is used")
        verdict = validate(Path(args.project))
    _write_verdict(args.json_path, verdict)
    return 0 if verdict["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
