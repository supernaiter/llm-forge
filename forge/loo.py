"""Leave-one-problem-out mock development evaluation.

The ordinary development matrix fits one policy from every supplied problem.
This module adds the stricter transferability design: for each target problem,
all target traces are excluded from fitting, a policy is frozen from the other
problems, and only then is the frozen policy replayed on the target.

The output is explicitly diagnostic and mock-only.  It is not a holdout or
scientific study artifact.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from .controller import SearchAction, load_controller_manifest
from .development import (
    REGISTERED_MECHANISMS,
    DevelopmentProblem,
    _DEVELOPMENT_METRICS,
    _load_problem,
    _metric_summary,
    _mock_environment,
    _run_config,
    _score_summary,
    _sha256_file,
    _write_jsonl,
    _bootstrap_controller,
    freeze_development_policies,
)
from .loop import run
from .protocol import ProtocolError, canonical_json
from .replay import replay_summary


PRIMARY_MECHANISM = "TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V2"
FIXED_MECHANISM = "FIXED_DEV_BEST"
LOO_MECHANISMS = (PRIMARY_MECHANISM, FIXED_MECHANISM)
LOO_SCHEMA_VERSION = 1
LOO_ATTEMPT_CAP = 4


def _validate_inputs(
    problems: Sequence[DevelopmentProblem],
    actions: Sequence[SearchAction],
    *,
    generations: int,
    max_attempts: int | None,
    seed: int,
    seeds: Sequence[int] | None,
    mechanisms: Sequence[str],
) -> tuple[tuple[int, ...], int, int]:
    if len(problems) < 2:
        raise ProtocolError("leave-one-problem-out evaluation requires at least two problems")
    if not actions:
        raise ProtocolError("leave-one-problem-out action space is empty")
    problem_ids = [item.problem_id for item in problems]
    if len(set(problem_ids)) != len(problem_ids):
        raise ProtocolError("leave-one-problem-out problem IDs must be unique")
    if len(set(actions)) != len(actions):
        raise ProtocolError("leave-one-problem-out action space contains duplicates")
    if isinstance(generations, bool) or not isinstance(generations, int) or generations <= 0:
        raise ProtocolError("leave-one-problem-out generations must be positive")
    attempt_cap = LOO_ATTEMPT_CAP if max_attempts is None else max_attempts
    if (
        isinstance(attempt_cap, bool)
        or not isinstance(attempt_cap, int)
        or attempt_cap != LOO_ATTEMPT_CAP
    ):
        raise ProtocolError("leave-one-problem-out requires exactly four attempts")
    run_seeds = tuple(seeds) if seeds is not None else (seed,)
    if len(run_seeds) < 3:
        raise ProtocolError("leave-one-problem-out requires at least three seeds per problem")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in run_seeds):
        raise ProtocolError("leave-one-problem-out seeds must be integers")
    if len(set(run_seeds)) != len(run_seeds):
        raise ProtocolError("leave-one-problem-out seeds must be unique")
    unknown = set(mechanisms) - (set(REGISTERED_MECHANISMS) | {PRIMARY_MECHANISM})
    if unknown:
        raise ProtocolError("unknown controller mechanisms: " + ", ".join(sorted(unknown)))
    required = {PRIMARY_MECHANISM, FIXED_MECHANISM}
    if not required.issubset(set(mechanisms)):
        raise ProtocolError("leave-one-problem-out requires primary and FIXED_DEV_BEST mechanisms")
    return run_seeds, attempt_cap, min(action.number_of_offspring for action in actions)


def _finite_mean(rows: Sequence[Mapping[str, Any]], metric: str) -> float | None:
    values = [row.get("metrics", {}).get(metric) for row in rows]
    if not values or any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        for value in values
    ):
        return None
    return sum(float(value) for value in values) / len(values)


def _target_policy_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    target_problem_id: str,
    mechanism: str,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in rows
        if row.get("target_problem_id") == target_problem_id
        and row.get("mechanism") == mechanism
    ]


def run_leave_one_problem_out_matrix(
    problems: Sequence[DevelopmentProblem],
    actions: Sequence[SearchAction],
    output_dir: str | Path,
    *,
    generations: int = 3,
    max_attempts: int | None = LOO_ATTEMPT_CAP,
    seed: int = 0,
    seeds: Sequence[int] | None = None,
    mechanisms: Sequence[str] = LOO_MECHANISMS,
) -> dict[str, Any]:
    """Run and audit a leave-one-problem-out controller development matrix.

    Every target fold has an explicit fit trace set containing only the other
    problem IDs.  The target's action traces are still retained as separate
    diagnostic artifacts, but ``target_trace_count_used`` is always zero and
    the manifest's training IDs are checked before target replay.
    """
    run_seeds, attempt_cap, min_offspring = _validate_inputs(
        problems,
        actions,
        generations=generations,
        max_attempts=max_attempts,
        seed=seed,
        seeds=seeds,
        mechanisms=mechanisms,
    )
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    action_runs_dir = target / "action_runs"
    problem_sources: dict[str, tuple[Any, dict[str, Any]]] = {}
    traces_by_problem: dict[str, list[dict[str, Any]]] = {
        spec.problem_id: [] for spec in problems
    }
    action_rows: list[dict[str, Any]] = []

    from tools.collect_controller_traces import collect_traces

    # First collect all visible action-arm traces.  No policy is fit in this
    # phase; fold-specific fitting happens only after the target is selected.
    with _mock_environment():
        for problem_index, spec in enumerate(problems):
            problem_factory, source_config = _load_problem(spec)
            problem_sources[spec.problem_id] = (problem_factory, source_config)
            for run_seed in run_seeds:
                for action_index, action in enumerate(actions):
                    run_dir = (
                        action_runs_dir / spec.problem_id / f"seed-{run_seed}"
                        / f"action-{action_index:03d}"
                    )
                    if run_dir.exists() and any(run_dir.iterdir()):
                        raise ProtocolError(f"LOO action run directory is not empty: {run_dir}")
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
                    cfg["run_id"] = f"loo-action-{spec.problem_id}-s{run_seed}-a{action_index}"
                    run_result = run(
                        problem_factory(),
                        cfg,
                        str(run_dir),
                        controller=_bootstrap_controller(action, spec.problem_id),
                    )
                    run_traces = collect_traces(run_dir / "events.jsonl", problem_id=spec.problem_id)
                    _write_jsonl(run_dir / "traces.jsonl", run_traces)
                    traces_by_problem[spec.problem_id].extend(run_traces)
                    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
                    action_rows.append({
                        "problem_id": spec.problem_id,
                        "seed": run_seed,
                        "effective_seed": cfg["seed"],
                        "action_index": action_index,
                        "action": asdict(action),
                        "run_dir": str(run_dir),
                        "trace_count": len(run_traces),
                        "best_score": result.get("best_score", run_result.get("score")),
                        "generations_done": result.get("generations_done"),
                        "attempt_count": result.get("attempt_count"),
                        "metrics": result.get("metrics", {}),
                    })

    all_traces = [
        trace
        for spec in problems
        for trace in traces_by_problem[spec.problem_id]
    ]
    all_trace_sha256 = _write_jsonl(target / "all_action_traces.jsonl", all_traces)
    policy_runs: list[dict[str, Any]] = []
    folds: dict[str, dict[str, Any]] = {}
    max_action = max(actions, key=lambda action: action.number_of_offspring)

    # Freeze and replay one policy set per target.  The fit rows are filtered
    # before serialization, so the target exclusion is auditable on disk.
    with _mock_environment():
        for target_index, target_spec in enumerate(problems):
            fit_traces = [
                trace for trace in all_traces
                if trace.get("problem_id") != target_spec.problem_id
            ]
            fit_problem_ids = sorted({trace.get("problem_id") for trace in fit_traces})
            if target_spec.problem_id in fit_problem_ids or not fit_problem_ids:
                raise ProtocolError(
                    f"LOO fit set is invalid for target {target_spec.problem_id}"
                )
            fold_dir = target / "folds" / f"target-{target_spec.problem_id}"
            if fold_dir.exists() and any(fold_dir.iterdir()):
                raise ProtocolError(f"LOO fold directory is not empty: {fold_dir}")
            fold_dir.mkdir(parents=True, exist_ok=True)
            frozen = freeze_development_policies(
                fit_traces,
                actions,
                fold_dir,
                mechanisms=mechanisms,
            )
            fold_trace_sha256 = frozen["trace_sha256"]
            if target_spec.problem_id in frozen["policies"][PRIMARY_MECHANISM]["training_problem_ids"]:
                raise ProtocolError("target problem leaked into primary controller fit")

            policy_paths = {
                mechanism: Path(row["manifest"])
                for mechanism, row in frozen["policies"].items()
            }
            target_factory, source_config = problem_sources[target_spec.problem_id]
            fold_policy_rows: list[dict[str, Any]] = []
            for mechanism_index, mechanism in enumerate(mechanisms):
                frozen_policy = load_controller_manifest(policy_paths[mechanism])
                if target_spec.problem_id in frozen_policy.training_problem_ids:
                    raise ProtocolError(
                        f"target problem leaked into {mechanism} manifest"
                    )
                for run_seed in run_seeds:
                    policy_run_dir = (
                        target / "policy_runs" / f"target-{target_spec.problem_id}"
                        / mechanism / f"seed-{run_seed}"
                    )
                    if policy_run_dir.exists() and any(policy_run_dir.iterdir()):
                        raise ProtocolError(
                            f"LOO policy run directory is not empty: {policy_run_dir}"
                        )
                    policy_run_dir.mkdir(parents=True, exist_ok=True)
                    cfg = _run_config(
                        source_config,
                        problem_id=target_spec.problem_id,
                        action=max_action,
                        action_index=10_000 + mechanism_index,
                        problem_index=target_index,
                        generations=math.ceil(attempt_cap / min_offspring),
                        max_attempts=attempt_cap,
                        seed=run_seed,
                    )
                    cfg["run_id"] = (
                        f"loo-policy-{mechanism}-{target_spec.problem_id}-s{run_seed}"
                    )
                    run(
                        target_factory(),
                        cfg,
                        str(policy_run_dir),
                        controller=frozen_policy,
                    )
                    result_path = policy_run_dir / "result.json"
                    events_path = policy_run_dir / "events.jsonl"
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                    replay = replay_summary(events_path)
                    replay_mismatches = sum([
                        result.get("decision_hash") != replay.get("decision_hash"),
                        result.get("result_recomputation_hash")
                        != replay.get("result_recomputation_hash"),
                    ])
                    if replay_mismatches:
                        raise ProtocolError(
                            f"LOO replay hash mismatch in {policy_run_dir}"
                        )
                    if replay.get("attempt_count") != attempt_cap:
                        raise ProtocolError(
                            f"LOO replay attempt cap mismatch in {policy_run_dir}"
                        )
                    action_records = result.get("controller_actions")
                    if not isinstance(action_records, list) or not action_records:
                        raise ProtocolError(
                            f"LOO policy run has no controller action records: {policy_run_dir}"
                        )
                    selected_actions = [
                        dict(record["action"])
                        for record in action_records
                        if isinstance(record, Mapping)
                        and isinstance(record.get("action"), Mapping)
                    ]
                    if len(selected_actions) != len(action_records):
                        raise ProtocolError(
                            f"LOO policy action record is malformed: {policy_run_dir}"
                        )
                    training_ids = list(frozen_policy.training_problem_ids)
                    policy_row = {
                        "mechanism": mechanism,
                        "problem_id": target_spec.problem_id,
                        "target_problem_id": target_spec.problem_id,
                        "seed": run_seed,
                        "effective_seed": cfg["seed"],
                        "run_dir": str(policy_run_dir),
                        "best_score": result.get("best_score"),
                        "attempt_count": result.get("attempt_count"),
                        "metrics": result.get("metrics", {}),
                        "selected_actions": selected_actions,
                        "controller_policy_sha256": result.get("controller_policy_sha256"),
                        "controller_training_problem_ids": training_ids,
                        "training_problem_ids": training_ids,
                        "fit_trace_count": len(fit_traces),
                        "fit_trace_sha256": fold_trace_sha256,
                        "target_trace_count_available": len(
                            traces_by_problem[target_spec.problem_id]
                        ),
                        "target_trace_count_used": 0,
                        "target_trace_excluded": target_spec.problem_id not in training_ids,
                        "events_sha256": _sha256_file(events_path),
                        "result_sha256": _sha256_file(result_path),
                        "decision_hash": result.get("decision_hash"),
                        "result_recomputation_hash": result.get(
                            "result_recomputation_hash"
                        ),
                        "replay_hash_mismatch_count": replay_mismatches,
                        "replay_resource_ledger_valid": replay.get(
                            "resource_ledger_valid"
                        ),
                    }
                    if not policy_row["target_trace_excluded"]:
                        raise ProtocolError("LOO target trace exclusion predicate failed")
                    policy_runs.append(policy_row)
                    fold_policy_rows.append(policy_row)
            folds[target_spec.problem_id] = {
                "target_problem_id": target_spec.problem_id,
                "fit_problem_ids": fit_problem_ids,
                "fit_trace_count": len(fit_traces),
                "fit_trace_sha256": fold_trace_sha256,
                "target_trace_count_available": len(
                    traces_by_problem[target_spec.problem_id]
                ),
                "target_trace_count_used": 0,
                "target_trace_excluded": True,
                "policy_manifest_paths": {
                    mechanism: str(path) for mechanism, path in policy_paths.items()
                },
                "policy_runs": fold_policy_rows,
            }

    target_comparisons: list[dict[str, Any]] = []
    for spec in problems:
        primary_rows = _target_policy_rows(
            policy_runs,
            target_problem_id=spec.problem_id,
            mechanism=PRIMARY_MECHANISM,
        )
        fixed_rows = _target_policy_rows(
            policy_runs,
            target_problem_id=spec.problem_id,
            mechanism=FIXED_MECHANISM,
        )
        primary_auc = _finite_mean(primary_rows, "auc_by_generation")
        fixed_auc = _finite_mean(fixed_rows, "auc_by_generation")
        auc_margin = (
            primary_auc - fixed_auc
            if primary_auc is not None and fixed_auc is not None
            else None
        )
        fixed_by_seed = {row["seed"]: row for row in fixed_rows}
        best_score_wins = sum(
            row.get("best_score") is not None
            and fixed_by_seed.get(row["seed"], {}).get("best_score") is not None
            and row["best_score"] >= fixed_by_seed[row["seed"]]["best_score"]
            for row in primary_rows
        )
        target_comparisons.append({
            "target_problem_id": spec.problem_id,
            "seed_count": len(run_seeds),
            "primary_mean_auc_by_generation": primary_auc,
            "fixed_mean_auc_by_generation": fixed_auc,
            "auc_margin": auc_margin,
            "auc_gate": auc_margin is not None and auc_margin >= 0.25,
            "primary_best_score_ge_fixed_seed_count": best_score_wins,
            "best_score_gate": best_score_wins >= 2,
        })

    primary_all = [row for row in policy_runs if row["mechanism"] == PRIMARY_MECHANISM]
    fixed_all = [row for row in policy_runs if row["mechanism"] == FIXED_MECHANISM]
    aggregate_primary_auc = _finite_mean(primary_all, "auc_by_generation")
    aggregate_fixed_auc = _finite_mean(fixed_all, "auc_by_generation")
    aggregate_margin = (
        aggregate_primary_auc - aggregate_fixed_auc
        if aggregate_primary_auc is not None and aggregate_fixed_auc is not None
        else None
    )
    summary = {
        "schema_version": LOO_SCHEMA_VERSION,
        "evaluation_design": "leave_one_problem_out",
        "classification": "development_mock_diagnostic_leave_one_problem_out",
        "scientific_evidence": False,
        "model": "mock",
        "track": "SAME_MODEL",
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
        "all_action_traces_path": str(target / "all_action_traces.jsonl"),
        "all_action_traces_sha256": all_trace_sha256,
        "runs": action_rows,
        "folds": folds,
        "policy_runs": policy_runs,
        "policy_mechanisms": list(mechanisms),
        "target_comparisons": target_comparisons,
        "aggregate_comparison": {
            "primary_mean_auc_by_generation": aggregate_primary_auc,
            "fixed_mean_auc_by_generation": aggregate_fixed_auc,
            "auc_margin": aggregate_margin,
            "auc_gate": aggregate_margin is not None and aggregate_margin >= 0.25,
            "target_count": len(problems),
            "all_target_auc_gates": all(item["auc_gate"] for item in target_comparisons),
            "all_target_best_score_gates": all(
                item["best_score_gate"] for item in target_comparisons
            ),
        },
        "development_comparison": {
            "classification": "development_mock_diagnostic_leave_one_problem_out",
            "scientific_evidence": False,
            "action_cells": _score_summary(action_rows, group_field="action_index"),
            "action_metric_cells": _metric_summary(
                action_rows, group_field="action_index"
            ),
            "policy_cells": _score_summary(policy_runs, group_field="mechanism"),
            "policy_metric_cells": _metric_summary(
                policy_runs, group_field="mechanism"
            ),
        },
        "audit": {
            "target_trace_exclusion_failures": sum(
                not fold["target_trace_excluded"] for fold in folds.values()
            ),
            "target_trace_rows_used_for_fit": sum(
                fold["target_trace_count_used"] for fold in folds.values()
            ),
            "replay_hash_mismatch_count": sum(
                row["replay_hash_mismatch_count"] for row in policy_runs
            ),
            "attempt_cap_failures": sum(
                row["attempt_count"] != attempt_cap for row in policy_runs
            ),
            "resource_ledger_invalid_count": sum(
                row["replay_resource_ledger_valid"] is not True for row in policy_runs
            ),
        },
    }
    (target / "loo_summary.json").write_bytes(canonical_json(summary) + b"\n")
    return summary
