#!/usr/bin/env python3
"""Verify that accepted improvements are causally due to a candidate mutation.

This verifier is deliberately post-run and read-only with respect to the
search.  It replays each event ledger, resolves candidate/parent source text
from the archive, and evaluates every accepted child and all of its recorded
parents on the same problem pack.  Python candidates must have a non-empty
AST mutation; non-code candidate domains (for example a string construction
fixture) use the corresponding non-empty text mutation.  No model is called
and no controller is fit or updated.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forge.lineage import ast_sha256, diff_sha256, lineage_metadata  # noqa: E402
from forge.protocol import ProtocolError, canonical_json, strict_json_loads  # noqa: E402
from forge.replay import replay_summary  # noqa: E402


SCHEMA_VERSION = 1
OBJECTIVE = "REAL_COMPUTE_MATCHED_CAUSAL_TRANSFER_V1"
VALID_STATUS = "valid_candidate"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ProtocolError(f"cannot read JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise ProtocolError(f"JSON value must be an object: {path}")
    return dict(value)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ProtocolError(f"missing JSONL artifact: {path}")
    records: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise ProtocolError(f"blank JSONL line: {path}:{line_no}")
        try:
            value = strict_json_loads(line)
        except (UnicodeError, ValueError) as exc:
            raise ProtocolError(f"invalid JSONL record: {path}:{line_no}") from exc
        if not isinstance(value, dict):
            raise ProtocolError(f"JSONL record is not an object: {path}:{line_no}")
        records.append(dict(value))
    return records


def _archive_sources(path: Path) -> tuple[dict[str, str], dict[str, float], list[str]]:
    sources: dict[str, str] = {}
    scores: dict[str, float] = {}
    failures: list[str] = []
    for index, record in enumerate(_load_jsonl(path)):
        text = record.get("text")
        score = record.get("score")
        if not isinstance(text, str) or not isinstance(score, (int, float)) or isinstance(score, bool):
            failures.append(f"archive record {index} lacks text/finite score")
            continue
        if not math.isfinite(float(score)):
            failures.append(f"archive record {index} has non-finite score")
            continue
        digest = _sha256_text(text)
        previous = sources.get(digest)
        if previous is not None and previous != text:
            failures.append(f"archive hash collision for {digest}")
            continue
        sources[digest] = text
        scores[digest] = float(score)
    return sources, scores, failures


def _load_problem(problem_dir: Path) -> Any:
    problem_path = problem_dir / "problem.py"
    if not problem_dir.is_dir() or not problem_path.is_file():
        raise ProtocolError(f"invalid problem pack: {problem_dir}")
    module_name = "_forge_counterfactual_problem_" + hashlib.sha256(
        str(problem_path).encode("utf-8")
    ).hexdigest()[:16]
    spec = importlib.util.spec_from_file_location(module_name, problem_path)
    if spec is None or spec.loader is None:
        raise ProtocolError(f"cannot import problem pack: {problem_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    problem_class = getattr(module, "Problem", None)
    if not isinstance(problem_class, type):
        raise ProtocolError(f"problem pack has no Problem class: {problem_path}")
    return problem_class()


def _score(problem: Any, source: str) -> dict[str, Any]:
    previous = os.environ.get("FORGE_PROTOCOL_V3")
    os.environ["FORGE_PROTOCOL_V3"] = "1"
    try:
        scorer = getattr(problem, "score_with_status", None)
        if callable(scorer):
            value = scorer(source)
            if not isinstance(value, tuple) or len(value) < 2:
                raise ProtocolError("score_with_status returned an invalid value")
            score, alive = value[0], value[1]
            status = value[2] if len(value) > 2 else ("valid_candidate" if alive else "invalid")
        else:
            value = problem.score(source)
            if isinstance(value, tuple):
                score, alive = value[0], value[1]
            else:
                score, alive = value, True
            status = "valid_candidate" if alive else "invalid"
    finally:
        if previous is None:
            os.environ.pop("FORGE_PROTOCOL_V3", None)
        else:
            os.environ["FORGE_PROTOCOL_V3"] = previous
    if (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(float(score))
    ):
        raise ProtocolError("problem score is not numeric")
    return {"score": float(score), "alive": bool(alive), "status": status}


def _lineage_check(
    *,
    candidate_hash: Any,
    candidate: str | None,
    parents: Any,
    parent_sources: list[str],
    diffs: Any,
    parent_count: Any,
    ast_required: bool,
) -> list[str]:
    failures: list[str] = []
    if not isinstance(candidate_hash, str) or candidate is None:
        return ["candidate source/hash is missing"]
    if candidate_hash != _sha256_text(candidate):
        failures.append("candidate hash does not match archive source")
    if not isinstance(parents, list) or not all(isinstance(value, str) for value in parents):
        failures.append("parent_candidate_sha256 is incomplete")
    if not isinstance(diffs, list) or not all(isinstance(value, str) for value in diffs):
        failures.append("accepted_candidate_diff_sha256 is incomplete")
    if not isinstance(parent_count, int) or isinstance(parent_count, bool):
        failures.append("parent_count is invalid")
    elif isinstance(parents, list) and parent_count != len(parents):
        failures.append("parent_count does not match parent hashes")
    if isinstance(parents, list) and isinstance(diffs, list):
        if len(diffs) != len(parents):
            failures.append("lineage diff count does not match parent count")
        elif len(parent_sources) == len(parents):
            expected = lineage_metadata(candidate, parent_sources)
            if expected["parent_candidate_sha256"] != parents:
                failures.append("parent hashes do not match source text")
            if expected["accepted_candidate_diff_sha256"] != diffs:
                failures.append("lineage diff hashes do not match source text")
    candidate_ast = ast_sha256(candidate)
    parent_asts = [ast_sha256(parent) for parent in parent_sources]
    if ast_required:
        all_python = candidate_ast is not None and all(
            parent_ast is not None for parent_ast in parent_asts
        )
        if not all_python:
            failures.append("candidate and parent sources have mixed mutation domains")
        else:
            for parent_ast in parent_asts:
                if candidate_ast == parent_ast:
                    failures.append("candidate has no nonempty AST mutation against a parent")
    for parent in parent_sources:
        if candidate == parent:
            failures.append("candidate source is identical to a parent")
        if diff_sha256(parent, candidate) == diff_sha256(parent, parent):
            failures.append("candidate source diff is empty")
    return failures


def _mutation_domain(
    candidate: str | None,
    parent_sources: list[str],
    *,
    ast_required: bool,
) -> str:
    """Classify the source domain used by the causal mutation check."""
    if candidate is None or not parent_sources:
        return "unavailable"
    if not ast_required:
        return "text"
    candidate_ast = ast_sha256(candidate)
    parent_asts = [ast_sha256(parent) for parent in parent_sources]
    if candidate_ast is not None and all(value is not None for value in parent_asts):
        return "ast"
    if candidate_ast is None and all(value is None for value in parent_asts):
        return "text"
    return "mixed"


def audit_run(
    events_path: Path,
    archive_path: Path,
    problem_dir: Path,
    *,
    target_problem_id: str,
    mechanism: str,
    seed: int | str,
) -> dict[str, Any]:
    """Audit one recorded run and return a JSON-serializable summary."""
    failures: list[str] = []
    replay: dict[str, Any] | None = None
    try:
        replay = replay_summary(events_path)
    except Exception as exc:  # fail closed but retain all run-local findings
        failures.append(f"replay failed: {type(exc).__name__}: {exc}")
    sources, archive_scores, archive_failures = _archive_sources(archive_path)
    failures.extend(archive_failures)
    try:
        events = _load_jsonl(events_path)
    except ProtocolError as exc:
        failures.append(str(exc))
        events = []
    try:
        problem = _load_problem(problem_dir)
    except Exception as exc:
        failures.append(f"problem load failed: {type(exc).__name__}: {exc}")
        problem = None
    # Registered program-search packs expose score_with_status; legacy text
    # fixtures such as stringmax expose only score().  The former must prove
    # AST mutation, while the latter is audited as a text-domain mutation.
    ast_required = problem is not None and callable(
        getattr(problem, "score_with_status", None)
    )

    score_cache: dict[str, dict[str, Any]] = {}

    def evaluate(digest: str, source: str) -> dict[str, Any]:
        if digest not in score_cache:
            if problem is None:
                score_cache[digest] = {"error": "problem unavailable"}
            else:
                try:
                    score_cache[digest] = _score(problem, source)
                except Exception as exc:
                    score_cache[digest] = {
                        "error": f"{type(exc).__name__}: {exc}"
                    }
        return score_cache[digest]

    improvement_candidates = 0
    proven_candidates = 0
    candidate_reports: list[dict[str, Any]] = []
    for event in events:
        if event.get("event_type") != "attempt_finished":
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping) or payload.get("status") != VALID_STATUS:
            continue
        metadata = payload.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        candidate_hash = payload.get("candidate_sha256")
        candidate = sources.get(candidate_hash) if isinstance(candidate_hash, str) else None
        parent_hashes = metadata.get("parent_candidate_sha256")
        parent_hashes = parent_hashes if isinstance(parent_hashes, list) else []
        parent_sources = [sources.get(value) for value in parent_hashes]
        available_parent_sources = [value for value in parent_sources if value is not None]
        mutation_domain = _mutation_domain(
            candidate,
            available_parent_sources,
            ast_required=ast_required,
        )
        lineage_failures = _lineage_check(
            candidate_hash=candidate_hash,
            candidate=candidate,
            parents=parent_hashes,
            parent_sources=available_parent_sources,
            diffs=metadata.get("accepted_candidate_diff_sha256"),
            parent_count=metadata.get("parent_count"),
            ast_required=ast_required,
        )
        missing_parent = any(value is None for value in parent_sources)
        if missing_parent:
            lineage_failures.append("one or more parent sources are missing from archive")
        parent_evals = []
        if candidate is not None:
            child_eval = evaluate(str(candidate_hash), candidate)
        else:
            child_eval = {"error": "candidate source missing from archive"}
        for parent_hash, parent_source in zip(parent_hashes, parent_sources):
            if parent_source is None:
                parent_evals.append({"hash": parent_hash, "error": "source missing"})
            else:
                parent_evals.append({
                    "hash": parent_hash,
                    "recorded_score": archive_scores.get(parent_hash),
                    "evaluation": evaluate(parent_hash, parent_source),
                })
        recorded_child = payload.get("score")
        recorded_parents = [archive_scores.get(value) for value in parent_hashes]
        recorded_improvement = (
            isinstance(recorded_child, (int, float))
            and not isinstance(recorded_child, bool)
            and bool(recorded_parents)
            and all(isinstance(value, (int, float)) and not isinstance(value, bool)
                    and float(recorded_child) > float(value) for value in recorded_parents)
        )
        if recorded_improvement:
            improvement_candidates += 1
        child_score = child_eval.get("score")
        parent_scores = [
            item.get("evaluation", {}).get("score")
            for item in parent_evals
            if isinstance(item.get("evaluation"), Mapping)
        ]
        causal_pass = (
            not lineage_failures
            and child_eval.get("alive") is True
            and isinstance(child_score, (int, float))
            and all(isinstance(value, (int, float)) and child_score > value for value in parent_scores)
            and len(parent_scores) == len(parent_hashes)
        )
        if causal_pass:
            proven_candidates += 1
        elif recorded_improvement:
            failures.extend(
                f"{payload.get('attempt_id', 'unknown')}: {failure}"
                for failure in (lineage_failures or ["counterfactual child did not beat every parent"])
            )
        candidate_reports.append({
            "attempt_id": payload.get("attempt_id"),
            "candidate_sha256": candidate_hash,
            "mutation_domain": mutation_domain,
            "parent_candidate_sha256": parent_hashes,
            "recorded_child_score": recorded_child,
            "recorded_parent_scores": recorded_parents,
            "child_evaluation": child_eval,
            "parent_evaluations": parent_evals,
            "recorded_improvement": recorded_improvement,
            "causal_ablation_pass": causal_pass,
            "failures": lineage_failures,
        })
    coverage = (
        proven_candidates / improvement_candidates
        if improvement_candidates else 0.0
    )
    return {
        "target_problem_id": target_problem_id,
        "mechanism": mechanism,
        "seed": seed,
        "events": str(events_path),
        "archive": str(archive_path),
        "replay": replay,
        "valid_candidate_count": len(candidate_reports),
        "improvement_candidate_count": improvement_candidates,
        "causal_pass_count": proven_candidates,
        "causal_coverage": coverage,
        "causal_gate": improvement_candidates > 0 and coverage == 1.0 and not failures,
        "score_evaluation_count": len(score_cache),
        "candidates": candidate_reports,
        "failures": failures,
    }


def _parse_problem(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise ProtocolError("--problem must use problem_id=problem_dir")
    problem_id, raw_path = value.split("=", 1)
    path = Path(raw_path).expanduser().resolve()
    if not problem_id.strip():
        raise ProtocolError("problem_id must be non-empty")
    return problem_id.strip(), path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--problem", action="append", required=True)
    parser.add_argument("--mechanism", action="append")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        problem_map = dict(_parse_problem(value) for value in args.problem)
        mechanisms = set(args.mechanism or ())
        runs_root = args.run_root / "runs"
        run_reports: list[dict[str, Any]] = []
        if not runs_root.is_dir():
            raise ProtocolError(f"missing runs directory: {runs_root}")
        for target_dir in sorted(runs_root.iterdir()):
            if not target_dir.is_dir():
                continue
            target = target_dir.name.removeprefix("target-")
            if target not in problem_map:
                raise ProtocolError(f"run target has no problem pack mapping: {target}")
            for mechanism_dir in sorted(target_dir.iterdir()):
                if not mechanism_dir.is_dir() or (mechanisms and mechanism_dir.name not in mechanisms):
                    continue
                for seed_dir in sorted(mechanism_dir.iterdir()):
                    if not seed_dir.is_dir() or not seed_dir.name.startswith("seed-"):
                        continue
                    try:
                        seed: int | str = int(seed_dir.name.removeprefix("seed-"))
                    except ValueError:
                        seed = seed_dir.name.removeprefix("seed-")
                    run_reports.append(audit_run(
                        seed_dir / "events.jsonl",
                        seed_dir / "archive.jsonl",
                        problem_map[target],
                        target_problem_id=target,
                        mechanism=mechanism_dir.name,
                        seed=seed,
                    ))
        expected = len(problem_map) * (len(mechanisms) if mechanisms else 2) * 3
        summary = {
            "schema_version": SCHEMA_VERSION,
            "objective": OBJECTIVE,
            "run_root": str(args.run_root.resolve()),
            "problems": sorted(problem_map),
            "mechanisms": sorted(mechanisms) if mechanisms else "all",
            "run_count": len(run_reports),
            "expected_run_count": expected,
            "causal_improvement_candidate_count": sum(
                report["improvement_candidate_count"] for report in run_reports
            ),
            "causal_pass_count": sum(report["causal_pass_count"] for report in run_reports),
            "causal_coverage": (
                sum(report["causal_pass_count"] for report in run_reports)
                / sum(report["improvement_candidate_count"] for report in run_reports)
                if sum(report["improvement_candidate_count"] for report in run_reports) else 0.0
            ),
            "all_runs_replay_valid": (
                len(run_reports) == expected
                and all(
                    isinstance(report.get("replay"), Mapping)
                    and report["replay"].get("resource_ledger_valid") is True
                    and report["replay"].get("trace_parent_child_links_complete") is True
                    for report in run_reports
                )
            ),
            "all_causal_gates": (
                len(run_reports) == expected
                and sum(report["improvement_candidate_count"] for report in run_reports) > 0
                and all(
                    report["improvement_candidate_count"] == 0
                    or (report["causal_coverage"] == 1.0 and not report["failures"])
                    for report in run_reports
                )
            ),
            "runs": run_reports,
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(canonical_json(summary))
        print(json.dumps({
            "out": str(args.out),
            "run_count": summary["run_count"],
            "expected_run_count": summary["expected_run_count"],
            "causal_coverage": summary["causal_coverage"],
            "all_causal_gates": summary["all_causal_gates"],
        }, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ProtocolError, ValueError, ImportError) as exc:
        print(f"DENIED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
