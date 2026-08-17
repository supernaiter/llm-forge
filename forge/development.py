"""Reproducible development runs for Forge controller fitting.

This module is deliberately development-only.  It runs the real Forge loop on
local problem packs under the mock adapter, collects search-side ledgers, and
freezes the registered controller plus its ablations from the merged traces.
No holdout pack or holdout score is loaded here.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import re
import statistics
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .controller import (
    ComputeAwareController,
    SearchAction,
    SearchState,
    controller_for_mechanism,
    load_controller_manifest,
    write_controller_manifest,
)
from .protocol import ProtocolError, canonical_json
from .loop import run


REGISTERED_MECHANISMS = (
    "TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V1",
    "FIXED_DEV_BEST",
    "NO_TRANSFER_PRIOR",
    "COST_UNAWARE_CONTROLLER",
)
_PROBLEM_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class DevelopmentProblem:
    """A local problem pack and the stable ID used in development traces."""

    problem_id: str
    problem_dir: Path

    def __post_init__(self) -> None:
        if not isinstance(self.problem_id, str) or not _PROBLEM_ID_RE.fullmatch(self.problem_id):
            raise ProtocolError(
                "development problem_id must contain only letters, numbers, '.', '_' or '-'"
            )
        if not isinstance(self.problem_dir, Path):
            raise ProtocolError("development problem_dir must be a Path")


def _load_problem(spec: DevelopmentProblem) -> tuple[Any, dict[str, Any]]:
    problem_dir = spec.problem_dir.resolve()
    config_path = problem_dir / "config.json"
    problem_path = problem_dir / "problem.py"
    if not problem_path.is_file() or not config_path.is_file():
        raise ProtocolError(f"development problem pack is incomplete: {problem_dir}")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"cannot read development config: {config_path}") from exc
    if not isinstance(config, dict):
        raise ProtocolError(f"development config must be an object: {config_path}")

    module_name = "_forge_development_problem_" + hashlib.sha256(
        str(problem_path).encode("utf-8")
    ).hexdigest()[:16]
    spec_obj = importlib.util.spec_from_file_location(module_name, problem_path)
    if spec_obj is None or spec_obj.loader is None:
        raise ProtocolError(f"cannot import development problem: {problem_path}")
    module = importlib.util.module_from_spec(spec_obj)
    previous_path = list(sys.path)
    previous_module = sys.modules.get(module_name)
    try:
        # Problem packs may import local helper modules.  Match the CLI import
        # path while avoiding the process-global ``problem`` module name.
        sys.path.insert(0, str(problem_dir))
        sys.modules[module_name] = module
        spec_obj.loader.exec_module(module)
    except Exception as exc:  # pragma: no cover - exercised by malformed packs
        raise ProtocolError(f"cannot import development problem: {problem_path}") from exc
    finally:
        sys.path[:] = previous_path
        if previous_module is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous_module
    problem_factory = getattr(module, "Problem", None)
    if not callable(problem_factory):
        raise ProtocolError(f"development problem has no Problem class: {problem_path}")
    return problem_factory, config


@contextmanager
def _mock_environment():
    previous = os.environ.get("FORGE_MOCK")
    os.environ["FORGE_MOCK"] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("FORGE_MOCK", None)
        else:
            os.environ["FORGE_MOCK"] = previous


def _bootstrap_controller(action: SearchAction, problem_id: str) -> ComputeAwareController:
    """Create a frozen one-action policy used to execute one development arm.

    The one-action policy is a routing fixture, not the fitted research
    controller.  Its ledger is later converted into a trace and all arms are
    refit together by :func:`freeze_development_policies`.
    """
    controller = ComputeAwareController([action])
    controller.fit([{
        "split": "dev",
        "problem_id": problem_id,
        "action": asdict(action),
        "quality_gain": 0.0,
        "cost": 1.0,
    }])
    controller.freeze()
    return controller


def _run_config(
    source: Mapping[str, Any],
    *,
    problem_id: str,
    action: SearchAction,
    action_index: int,
    problem_index: int,
    generations: int,
    max_attempts: int | None,
    seed: int,
) -> dict[str, Any]:
    if generations <= 0:
        raise ProtocolError("development generations must be positive")
    if max_attempts is not None and (
        isinstance(max_attempts, bool)
        or not isinstance(max_attempts, int)
        or max_attempts <= 0
    ):
        raise ProtocolError("development max_attempts must be a positive integer")
    cfg = dict(source)
    attempts = max_attempts or generations * action.number_of_offspring
    if attempts <= 0:
        raise ProtocolError("development max_attempts must be positive")
    cfg.update({
        "protocol_v3": True,
        "mock": True,
        "track": "SAME_MODEL",
        "generations": generations,
        "max_attempts": attempts,
        "max_cheap_calls": max(attempts, int(cfg.get("max_cheap_calls", attempts))),
        # A non-parametric candidate may consume one V0 and one V1 evaluator
        # call.  Reserve enough room for both while loop.py enforces the V3
        # global cap.
        "max_evaluator_calls": max(2 * attempts, int(cfg.get("max_evaluator_calls", 0) or 0)),
        "max_smart_calls": 0,
        "workers": 1,
        "cheap_workers": 1,
        # Pair action arms and frozen-policy replays on the same declared
        # seed.  Only the problem index is offset so independent development
        # problems do not share a mock RNG stream; using action_index here
        # would confound an action effect with a different random sequence.
        "seed": seed + (problem_index * 10_000),
        "run_id": f"dev-{problem_id}-a{action_index}",
    })
    source_budgets = source.get("resource_budgets")
    if not isinstance(source_budgets, Mapping):
        source_budgets = {}
    source_generation = source_budgets.get("generation")
    if not isinstance(source_generation, Mapping):
        source_generation = {}
    cfg["resource_budgets"] = {
        "generation": {
            "records": attempts,
            "input_tokens": source_generation.get("input_tokens"),
            "output_tokens": source_generation.get("output_tokens"),
        },
        "evaluator": {"calls": cfg["max_evaluator_calls"]},
    }
    return cfg


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> str:
    # ``canonical_json`` already terminates each record with one POSIX
    # newline.  Do not append a second delimiter: blank records make the
    # development trace artifact invalid for strict JSONL consumers and can
    # silently desynchronise line-oriented hash/replay tooling.
    payload = b"".join(canonical_json(dict(row)) for row in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _score_summary(rows: Sequence[Mapping[str, Any]], *, group_field: str) -> list[dict[str, Any]]:
    """Summarize development scores without mixing problem identities.

    Development packs are allowed to use different score scales and directions,
    so the report keeps every problem in its own cell.  These aggregates are
    for controller tuning only; they are deliberately not research metrics.
    """
    cells: dict[tuple[Any, str], list[tuple[int, float]]] = {}
    for row in rows:
        group = row.get(group_field)
        problem_id = row.get("problem_id")
        seed = row.get("seed")
        score = row.get("best_score")
        if not isinstance(group, (str, int)) or isinstance(group, bool):
            raise ProtocolError(f"development {group_field} is missing or invalid")
        if not isinstance(problem_id, str) or not problem_id:
            raise ProtocolError("development score row has invalid problem_id")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ProtocolError("development score row has invalid seed")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ProtocolError("development score row has invalid best_score")
        score_value = float(score)
        if not math.isfinite(score_value):
            raise ProtocolError("development best_score must be finite")
        cells.setdefault((group, problem_id), []).append((seed, score_value))

    summary: list[dict[str, Any]] = []
    for (group, problem_id), values in sorted(cells.items(), key=lambda item: (str(item[0][0]), item[0][1])):
        ordered = sorted(values)
        scores = [score for _, score in ordered]
        summary.append({
            group_field: group,
            "problem_id": problem_id,
            "seeds": [seed for seed, _ in ordered],
            "scores": scores,
            "run_count": len(scores),
            "mean_best_score": statistics.fmean(scores),
            "median_best_score": statistics.median(scores),
        })
    return summary


_DEVELOPMENT_METRICS = (
    "best_score",
    "auc_by_candidate",
    "auc_by_generation",
    "alive_per_call",
    "gain_per_call",
    "cheap_failure_rate",
)


def _metric_summary(
    rows: Sequence[Mapping[str, Any]], *, group_field: str
) -> list[dict[str, Any]]:
    """Summarize several visible run metrics without mixing problem scales.

    ``_score_summary`` remains the compact best-score view used by existing
    consumers.  This companion view is the development tuning surface: every
    metric is kept in a problem-local, seed-separated cell so action choices
    are not promoted from a single terminal score.
    """
    cells: dict[tuple[str, Any, str], list[tuple[int, float]]] = {}
    for row in rows:
        group = row.get(group_field)
        problem_id = row.get("problem_id")
        seed = row.get("seed")
        if not isinstance(group, (str, int)) or isinstance(group, bool):
            raise ProtocolError(f"development {group_field} is missing or invalid")
        if not isinstance(problem_id, str) or not problem_id:
            raise ProtocolError("development metric row has invalid problem_id")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ProtocolError("development metric row has invalid seed")
        metrics = row.get("metrics")
        if not isinstance(metrics, Mapping):
            raise ProtocolError("development metric row is missing metrics")
        for metric in _DEVELOPMENT_METRICS:
            value = metrics.get(metric)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ProtocolError(f"development metric {metric} must be numeric")
            numeric = float(value)
            if not math.isfinite(numeric):
                raise ProtocolError(f"development metric {metric} must be finite")
            cells.setdefault((metric, group, problem_id), []).append((seed, numeric))

    summary: list[dict[str, Any]] = []
    for (metric, group, problem_id), values in sorted(
        cells.items(), key=lambda item: (item[0][0], str(item[0][1]), item[0][2])
    ):
        ordered = sorted(values)
        numeric_values = [value for _, value in ordered]
        summary.append({
            group_field: group,
            "metric": metric,
            "problem_id": problem_id,
            "seeds": [seed for seed, _ in ordered],
            "values": numeric_values,
            "run_count": len(numeric_values),
            "mean": statistics.fmean(numeric_values),
            "median": statistics.median(numeric_values),
        })
    return summary


def freeze_development_policies(
    traces: Sequence[Mapping[str, Any]],
    actions: Sequence[SearchAction],
    output_dir: str | Path,
    *,
    mechanisms: Sequence[str] = REGISTERED_MECHANISMS,
) -> dict[str, Any]:
    """Fit every registered mechanism and emit manifests plus a comparison."""
    if not traces:
        raise ProtocolError("development trace matrix is empty")
    if not actions:
        raise ProtocolError("development action space is empty")
    unknown = set(mechanisms) - (
        set(REGISTERED_MECHANISMS) | {"TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V2"}
    )
    if unknown:
        raise ProtocolError("unknown controller mechanisms: " + ", ".join(sorted(unknown)))
    target = Path(output_dir)
    trace_path = target / "development_traces.jsonl"
    trace_hash = _write_jsonl(trace_path, traces)
    policies_dir = target / "policies"
    baseline_state = SearchState(
        remaining_budget=10_000,
        improvement_slope=0.0,
        time_since_last_improvement=0,
        archive_behavioral_entropy=0.0,
        archive_score_dispersion=0.0,
        candidate_invalid_rate=0.0,
        duplicate_rate=0.0,
        parent_lineage_depth=0.0,
        recent_operator_success=0.0,
        recent_model_success=0.0,
        estimated_generation_cost=1.0,
    )
    expensive_state = SearchState(**{
        **asdict(baseline_state),
        "estimated_generation_cost": 10_000.0,
    })
    policy_rows: dict[str, Any] = {}
    for mechanism in mechanisms:
        controller = controller_for_mechanism(mechanism, actions)
        controller.fit(traces)
        controller.freeze()
        manifest_path = policies_dir / f"{mechanism}.json"
        manifest = write_controller_manifest(
            controller,
            manifest_path,
            source_traces_sha256=trace_hash,
            manifest_id=f"FORGE_DEV_{mechanism}_V1",
        )
        policy_rows[mechanism] = {
            "manifest": str(manifest_path),
            "manifest_sha256": manifest["manifest_sha256"],
            "policy_sha256": manifest["policy_sha256"],
            "training_problem_ids": list(controller.training_problem_ids),
            "utilities": [
                {"action": asdict(action), "value": controller.utilities[action]}
                for action in actions
            ],
            "supports": [
                {"action": asdict(action), "count": controller.supports[action]}
                for action in actions
            ],
            "selected_action": asdict(controller.choose(baseline_state)),
            "selected_action_expensive": asdict(controller.choose(expensive_state)),
        }
    return {
        "schema_version": 1,
        "trace_path": str(trace_path),
        "trace_sha256": trace_hash,
        "policy_mechanisms": list(mechanisms),
        "policies": policy_rows,
    }


def run_development_matrix(
    problems: Sequence[DevelopmentProblem],
    actions: Sequence[SearchAction],
    output_dir: str | Path,
    *,
    generations: int = 3,
    max_attempts: int | None = None,
    seed: int = 0,
    seeds: Sequence[int] | None = None,
    mechanisms: Sequence[str] = REGISTERED_MECHANISMS,
) -> dict[str, Any]:
    """Run every action on every local development problem and freeze policies."""
    if not problems:
        raise ProtocolError("development problem matrix is empty")
    if not actions:
        raise ProtocolError("development action space is empty")
    problem_ids = [item.problem_id for item in problems]
    if len(set(problem_ids)) != len(problem_ids):
        raise ProtocolError("development problem IDs must be unique")
    if len(set(actions)) != len(actions):
        raise ProtocolError("development action space contains duplicates")
    run_seeds = tuple(seeds) if seeds is not None else (seed,)
    if not run_seeds:
        raise ProtocolError("development seed matrix is empty")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in run_seeds):
        raise ProtocolError("development seeds must be integers")
    if len(set(run_seeds)) != len(run_seeds):
        raise ProtocolError("development seeds must be unique")
    # All action arms must receive the same attempt budget.  If the caller did
    # not provide one, derive it once from the largest registered offspring
    # count instead of deriving a different budget inside each arm.
    attempt_cap = max_attempts
    if attempt_cap is None:
        attempt_cap = generations * max(action.number_of_offspring for action in actions)
    if isinstance(attempt_cap, bool) or not isinstance(attempt_cap, int) or attempt_cap <= 0:
        raise ProtocolError("development attempt cap must be a positive integer")
    min_offspring = min(action.number_of_offspring for action in actions)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    runs_dir = target / "runs"
    traces: list[dict[str, Any]] = []
    run_rows: list[dict[str, Any]] = []
    with _mock_environment():
        for problem_index, spec in enumerate(problems):
            problem_factory, source_config = _load_problem(spec)
            for run_seed in run_seeds:
                for action_index, action in enumerate(actions):
                    run_dir = (
                        runs_dir / spec.problem_id / f"seed-{run_seed}"
                        / f"action-{action_index:03d}"
                    )
                    if run_dir.exists() and any(run_dir.iterdir()):
                        raise ProtocolError(f"development run directory is not empty: {run_dir}")
                    run_dir.mkdir(parents=True, exist_ok=True)
                    cfg = _run_config(
                        source_config,
                        problem_id=spec.problem_id,
                        action=action,
                        action_index=action_index,
                        problem_index=problem_index,
                        generations=math.ceil(attempt_cap / action.number_of_offspring),
                        max_attempts=attempt_cap,
                        seed=run_seed,
                    )
                    cfg["run_id"] = f"dev-{spec.problem_id}-s{run_seed}-a{action_index}"
                    try:
                        problem = problem_factory()
                    except Exception as exc:  # pragma: no cover - malformed pack
                        raise ProtocolError(
                            f"cannot construct development problem: {spec.problem_dir}"
                        ) from exc
                    best = run(
                        problem,
                        cfg,
                        str(run_dir),
                        controller=_bootstrap_controller(action, spec.problem_id),
                    )
                    from tools.collect_controller_traces import collect_traces

                    run_traces = collect_traces(
                        run_dir / "events.jsonl", problem_id=spec.problem_id
                    )
                    trace_path = run_dir / "traces.jsonl"
                    _write_jsonl(trace_path, run_traces)
                    traces.extend(run_traces)
                    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
                    run_rows.append({
                        "problem_id": spec.problem_id,
                        "seed": run_seed,
                        "effective_seed": cfg["seed"],
                        "action_index": action_index,
                        "action": asdict(action),
                        "run_dir": str(run_dir),
                        "trace_count": len(run_traces),
                        "best_score": result.get("best_score", best.get("score")),
                        "generations_done": result.get("generations_done"),
                        "attempt_count": result.get("attempt_count"),
                        "metrics": result.get("metrics", {}),
                    })
    policies = freeze_development_policies(
        traces, actions, target, mechanisms=mechanisms
    )
    policies["development_comparison"] = {
        "classification": "development_mock_diagnostic",
        "scientific_evidence": False,
        "action_cells": _score_summary(run_rows, group_field="action_index"),
        "action_metric_cells": _metric_summary(run_rows, group_field="action_index"),
    }
    # Re-run each frozen policy on every development problem.  These runs are
    # deliberately not appended to ``development_traces.jsonl``: doing so
    # would let post-freeze policy behavior train the policy that produced it.
    policy_runs: list[dict[str, Any]] = []
    max_action = max(actions, key=lambda action: action.number_of_offspring)
    with _mock_environment():
        for mechanism_index, mechanism in enumerate(mechanisms):
            policy_path = Path(policies["policies"][mechanism]["manifest"])
            frozen_policy = load_controller_manifest(policy_path)
            for problem_index, spec in enumerate(problems):
                problem_factory, source_config = _load_problem(spec)
                for run_seed in run_seeds:
                    policy_run_dir = (
                        target / "policy_runs" / mechanism / f"seed-{run_seed}"
                        / spec.problem_id
                    )
                    if policy_run_dir.exists() and any(policy_run_dir.iterdir()):
                        raise ProtocolError(
                            f"development policy run directory is not empty: {policy_run_dir}"
                        )
                    policy_run_dir.mkdir(parents=True, exist_ok=True)
                    cfg = _run_config(
                        source_config,
                        problem_id=spec.problem_id,
                        action=max_action,
                        action_index=10_000 + mechanism_index,
                        problem_index=problem_index,
                        # A policy may choose the smallest offspring arm for
                        # every generation, so give it enough generations to
                        # consume the same cap as the action arms.
                        generations=math.ceil(attempt_cap / min_offspring),
                        max_attempts=attempt_cap,
                        seed=run_seed,
                    )
                    cfg["run_id"] = f"dev-policy-{mechanism}-{spec.problem_id}-s{run_seed}"
                    try:
                        problem = problem_factory()
                    except Exception as exc:  # pragma: no cover - malformed pack
                        raise ProtocolError(
                            f"cannot construct development problem: {spec.problem_dir}"
                        ) from exc
                    run(
                        problem,
                        cfg,
                        str(policy_run_dir),
                        controller=frozen_policy,
                    )
                    result = json.loads(
                        (policy_run_dir / "result.json").read_text(encoding="utf-8")
                    )
                    action_records = result.get("controller_actions")
                    if not isinstance(action_records, list) or not action_records:
                        raise ProtocolError(
                            f"frozen policy run has no controller action records: {policy_run_dir}"
                        )
                    selected_actions = []
                    for record in action_records:
                        if not isinstance(record, Mapping) or not isinstance(
                            record.get("action"), Mapping
                        ):
                            raise ProtocolError(
                                f"frozen policy action record is malformed: {policy_run_dir}"
                            )
                        selected_actions.append(dict(record["action"]))
                    policy_runs.append({
                        "mechanism": mechanism,
                        "problem_id": spec.problem_id,
                        "seed": run_seed,
                        "effective_seed": cfg["seed"],
                        "run_dir": str(policy_run_dir),
                        "best_score": result.get("best_score"),
                        "attempt_count": result.get("attempt_count"),
                        "metrics": result.get("metrics", {}),
                        "selected_actions": selected_actions,
                        "controller_policy_sha256": result.get("controller_policy_sha256"),
                        "events_sha256": _sha256_file(policy_run_dir / "events.jsonl"),
                        "result_sha256": _sha256_file(policy_run_dir / "result.json"),
                        "decision_hash": result.get("decision_hash"),
                        "result_recomputation_hash": result.get("result_recomputation_hash"),
                    })
            policies["policies"][mechanism]["development_replay"] = [
                row for row in policy_runs if row["mechanism"] == mechanism
            ]
    policies["development_comparison"]["policy_cells"] = _score_summary(
        policy_runs, group_field="mechanism"
    )
    policies["development_comparison"]["policy_metric_cells"] = _metric_summary(
        policy_runs, group_field="mechanism"
    )
    summary = {
        "schema_version": 1,
        "problems": [
            {"problem_id": spec.problem_id, "problem_dir": str(spec.problem_dir.resolve())}
            for spec in problems
        ],
        "actions": [asdict(action) for action in actions],
        "generations": generations,
        "attempt_cap": attempt_cap,
        "development_metrics": list(_DEVELOPMENT_METRICS),
        "seed": run_seeds[0],
        "seeds": list(run_seeds),
        "runs": run_rows,
        "policy_runs": policy_runs,
        **policies,
    }
    (target / "development_summary.json").write_bytes(canonical_json(summary) + b"\n")
    return summary
