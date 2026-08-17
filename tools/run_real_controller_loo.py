#!/usr/bin/env python3
"""Run a frozen-policy real-model leave-one-problem-out comparison.

The policy artifacts are produced before this command starts.  This runner
only copies the registered problem packs into an isolated, four-attempt real
run configuration and executes the primary and FIXED_DEV_BEST policies with
the same route/model manifests and resource caps.  It never fits a policy
from target runs and never enables the mock adapter.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from statistics import fmean
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forge.controller import load_controller_manifest  # noqa: E402
from forge.model_routes import (  # noqa: E402
    load_controller_model_routes,
    validate_routes_against_model_manifest,
)
from forge.protocol import canonical_json, load_protocol, ProtocolError, strict_json_loads  # noqa: E402
from forge.replay import replay_summary  # noqa: E402


PRIMARY = "TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V2"
FIXED = "FIXED_DEV_BEST"
MECHANISMS = (PRIMARY, FIXED)
ATTEMPT_CAP = 4


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ProtocolError(f"cannot read JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise ProtocolError(f"JSON value must be an object: {path}")
    return dict(value)


def _parse_problem(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise ProtocolError("--problem must use problem_id=problem_dir")
    problem_id, problem_dir = value.split("=", 1)
    problem_id = problem_id.strip()
    path = Path(problem_dir).expanduser().resolve()
    if not problem_id or not path.is_dir() or not (path / "problem.py").is_file():
        raise ProtocolError(f"invalid problem pack: {value}")
    return problem_id, path


def _copy_pack(source: Path, target: Path, *, evaluator_calls: int) -> dict[str, Any]:
    if target.exists():
        raise ProtocolError(f"derived problem pack already exists: {target}")
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "runs"),
    )
    config_path = target / "config.json"
    config = _load_object(config_path)
    config.update({
        # The controller action owns the offspring count; this upper bound
        # only keeps the pack alive long enough for all four slots.
        "generations": ATTEMPT_CAP,
        "max_attempts": ATTEMPT_CAP,
        "max_cheap_calls": ATTEMPT_CAP,
        "max_evaluator_calls": evaluator_calls,
        "max_smart_calls": 0,
        "workers": 1,
        "cheap_workers": 2,
    })
    protocol = load_protocol()
    config["resource_budgets"] = {
        "generation": {
            "records": ATTEMPT_CAP,
            "input_tokens": protocol["budgets"]["max_input_tokens"],
            "output_tokens": protocol["budgets"]["max_output_tokens"],
        },
        "evaluator": {"calls": evaluator_calls},
    }
    config_path.write_bytes(canonical_json(config))
    return {
        "source_problem_dir": str(source),
        "source_problem_sha256": _sha256(source / "problem.py"),
        "derived_problem_dir": str(target),
        "derived_config_sha256": _sha256(config_path),
        "attempt_cap": ATTEMPT_CAP,
    }


def _existing_pack_row(source: Path, target: Path) -> dict[str, Any]:
    """Describe a derived pack already materialized by a previous run."""
    config_path = target / "config.json"
    if not target.is_dir() or not config_path.is_file():
        raise ProtocolError(f"incomplete derived problem pack: {target}")
    return {
        "source_problem_dir": str(source),
        "source_problem_sha256": _sha256(source / "problem.py"),
        "derived_problem_dir": str(target),
        "derived_config_sha256": _sha256(config_path),
        "attempt_cap": ATTEMPT_CAP,
    }


def _require_json_cli_environment(env: Mapping[str, str]) -> None:
    for tier in ("CHEAP", "SMALL", "STRONG"):
        raw = env.get(f"FORGE_{tier}_CLI")
        if not raw:
            raise ProtocolError(f"FORGE_{tier}_CLI is required for real LOO")
        try:
            argv = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"FORGE_{tier}_CLI is not JSON: {tier}") from exc
        if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            raise ProtocolError(f"FORGE_{tier}_CLI must be a JSON argv list")
        if "--json" not in argv:
            raise ProtocolError(
                f"FORGE_{tier}_CLI must include --json for observed token telemetry: {tier}"
            )


def _run_one(
    *,
    pack: Path,
    policy: Path,
    routes: Path,
    model_manifest: Path,
    run_dir: Path,
    identity: dict[str, Any],
    env: dict[str, str],
    timeout: int,
    reuse: bool = False,
) -> dict[str, Any]:
    complete_files = (
        run_dir / "run_identity.json",
        run_dir / "manifest.json",
        run_dir / "result.json",
        run_dir / "events.jsonl",
        run_dir / "archive.jsonl",
    )
    if reuse:
        if not run_dir.is_dir() or not all(path.is_file() for path in complete_files):
            raise ProtocolError(f"cannot reuse incomplete run directory: {run_dir}")
    else:
        if run_dir.exists():
            raise ProtocolError(f"run directory already exists and is incomplete: {run_dir}")
        run_dir.mkdir(parents=True, exist_ok=False)
        identity_path = run_dir / "run_identity.json"
        identity_path.write_bytes(canonical_json(identity))
        command = [
            sys.executable,
            str(ROOT / "cli.py"),
            str(pack),
            "--protocol-v3",
            "--controller-policy", str(policy),
            "--controller-model-routes", str(routes),
            "--model-manifest", str(model_manifest),
            "--run-identity", str(identity_path),
            "--run-dir", str(run_dir),
        ]
        child_env = dict(env)
        child_env["FORGE_REAL_RUN_ALLOWED"] = "1"
        child_env.pop("FORGE_MOCK", None)
        child_env.pop("FORGE_PROTOCOL_V3", None)
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=child_env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        (run_dir / "stdout.log").write_text(completed.stdout, encoding="utf-8")
        (run_dir / "stderr.log").write_text(completed.stderr, encoding="utf-8")
        if completed.returncode != 0:
            raise ProtocolError(
                f"real run failed ({completed.returncode}) for {identity['run_id']}: "
                f"{completed.stderr[-500:]}"
            )
    result_path = run_dir / "result.json"
    events_path = run_dir / "events.jsonl"
    if not result_path.is_file() or not events_path.is_file():
        raise ProtocolError(f"real run did not emit result/ledger: {run_dir}")
    result = _load_object(result_path)
    run_manifest = _load_object(run_dir / "manifest.json")
    run_model_manifest_sha = run_manifest.get("model_manifest_sha256")
    expected_model_manifest_sha = _sha256(model_manifest)
    if run_model_manifest_sha != expected_model_manifest_sha:
        raise ProtocolError(
            f"run model manifest identity differs from frozen model manifest: {run_dir}"
        )
    replay = replay_summary(events_path)
    policy_data = _load_object(policy)
    policy_sha = policy_data.get("policy_sha256")
    training_ids = policy_data.get("training_problem_ids")
    if not isinstance(policy_sha, str) or not isinstance(training_ids, list):
        raise ProtocolError(f"frozen policy manifest is incomplete: {policy}")
    if result.get("controller_policy_sha256") != policy_sha:
        raise ProtocolError(
            f"run controller policy identity differs from frozen policy: {run_dir}"
        )
    resource_summary = replay.get("resource_summary", {})
    generation_phase = resource_summary.get("phases", {}).get("generation", {})
    evaluator_phase = resource_summary.get("phases", {}).get("evaluator", {})
    row = {
        "target_problem_id": identity["problem_id"],
        "mechanism": identity["method_id"],
        "seed": identity["seed"],
        "run_id": identity["run_id"],
        "run_dir": str(run_dir),
        "policy_path": str(policy),
        "controller_policy_sha256": result.get("controller_policy_sha256"),
        "frozen_policy_sha256": policy_sha,
        "policy_manifest_sha256": _sha256(policy),
        "training_problem_ids": training_ids,
        "target_trace_excluded": identity["problem_id"] not in training_ids,
        # The CLI result schema keeps this identity in manifest.json.  Older
        # runs may leave the result field null, so use the authoritative run
        # manifest for the cross-run equality gate.
        "model_manifest_sha256": run_model_manifest_sha,
        "attempt_count": result.get("attempt_count"),
        "attempt_cap": result.get("attempt_cap"),
        "best_score": result.get("best_score"),
        "metrics": result.get("metrics", {}),
        "alive_candidates": result.get("metrics", {}).get("alive_candidates", 0),
        "baseline_score": result.get("metrics", {}).get("baseline_score"),
        "decision_hash": result.get("decision_hash"),
        "result_recomputation_hash": result.get("result_recomputation_hash"),
        "replay_decision_hash": replay.get("decision_hash"),
        "replay_result_recomputation_hash": replay.get("result_recomputation_hash"),
        "replay_hash_match": (
            result.get("decision_hash") == replay.get("decision_hash")
            and result.get("result_recomputation_hash")
            == replay.get("result_recomputation_hash")
        ),
        "replay_resource_ledger_valid": replay.get("resource_ledger_valid"),
        "trace_parent_child_links_complete": replay.get("trace_parent_child_links_complete"),
        "lineage_cycle_count": replay.get("lineage_cycle_count"),
        "candidate_ast_hash_coverage": replay.get("candidate_ast_hash_coverage"),
        "accepted_candidate_diff_coverage": replay.get("accepted_candidate_diff_coverage"),
        "generation_budget": result.get("generation_budget"),
        "evaluator_budget": result.get("evaluator_budget"),
        "generation_required_missing_fields": generation_phase.get("required_missing_fields", []),
        "evaluator_required_missing_fields": evaluator_phase.get("required_missing_fields", []),
        "generation_telemetry_complete": generation_phase.get("telemetry_complete"),
        "resource_summary": resource_summary,
    }
    return row


def _mean(rows: list[dict[str, Any]], field: str) -> float:
    values = [row.get(field) for row in rows]
    if not values or any(not isinstance(value, (int, float)) for value in values):
        raise ProtocolError(f"non-numeric comparison field: {field}")
    return fmean(float(value) for value in values)


def _compare(rows: list[dict[str, Any]], target: str) -> dict[str, Any]:
    primary = [row for row in rows if row["target_problem_id"] == target and row["mechanism"] == PRIMARY]
    fixed = [row for row in rows if row["target_problem_id"] == target and row["mechanism"] == FIXED]
    if len(primary) != 3 or len(fixed) != 3:
        raise ProtocolError(f"expected three seeds for both policies: {target}")
    primary_auc = fmean(
        float(row["metrics"]["auc_by_generation"]) for row in primary
    )
    fixed_auc = fmean(float(row["metrics"]["auc_by_generation"]) for row in fixed)
    primary_best = fmean(float(row["best_score"]) for row in primary)
    fixed_best = fmean(float(row["best_score"]) for row in fixed)
    improvement_runs = sum(
        row["best_score"] > row["baseline_score"] for row in primary
        if isinstance(row["best_score"], (int, float))
        and isinstance(row["baseline_score"], (int, float))
    )
    return {
        "target_problem_id": target,
        "primary_mean_auc_by_generation": primary_auc,
        "fixed_mean_auc_by_generation": fixed_auc,
        "auc_margin": primary_auc - fixed_auc,
        "auc_gate": primary_auc - fixed_auc >= 0.25,
        "primary_mean_best_score": primary_best,
        "fixed_mean_best_score": fixed_best,
        "best_score_margin": primary_best - fixed_best,
        "best_score_gate": primary_best >= fixed_best,
        "primary_improvement_run_count": improvement_runs,
        "improvement_candidate_gate": improvement_runs >= 1,
        "primary_alive_candidate_runs": sum(row["alive_candidates"] > 0 for row in primary),
        "fixed_alive_candidate_runs": sum(row["alive_candidates"] > 0 for row in fixed),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--problem", action="append", required=True)
    parser.add_argument("--policy-root", type=Path, required=True)
    parser.add_argument("--controller-model-routes", type=Path, required=True)
    parser.add_argument("--model-manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", action="append", dest="seeds", type=int, required=True)
    parser.add_argument("--attempts", type=int, default=ATTEMPT_CAP)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="reuse complete run directories already present under --out",
    )
    args = parser.parse_args(argv)
    try:
        if args.attempts != ATTEMPT_CAP:
            raise ProtocolError("real causal transfer requires exactly four attempts")
        if len(args.seeds) != 3 or len(set(args.seeds)) != 3:
            raise ProtocolError("real causal transfer requires exactly three unique seeds")
        if args.out.exists() and any(args.out.iterdir()) and not args.resume:
            raise ProtocolError(f"output directory is not empty: {args.out}")
        args.out.mkdir(parents=True, exist_ok=True)
        problems = [_parse_problem(value) for value in args.problem]
        problem_ids = [problem_id for problem_id, _ in problems]
        if len(set(problem_ids)) != len(problem_ids):
            raise ProtocolError("problem IDs must be unique")
        if len(problem_ids) != 4:
            raise ProtocolError(
                "real causal transfer requires exactly four registered problem packs"
            )
        routes = load_controller_model_routes(args.controller_model_routes)
        model_hash = validate_routes_against_model_manifest(routes, args.model_manifest)
        env = dict(os.environ)
        _require_json_cli_environment(env)
        protocol = load_protocol()
        evaluator_calls = int(protocol["budgets"]["max_search_evaluations"])
        packs_dir = args.out / "derived_packs"
        packs_dir.mkdir(parents=True, exist_ok=True)
        pack_rows = {}
        for problem_id, source in problems:
            target_pack = packs_dir / problem_id
            if args.resume and target_pack.exists():
                pack_rows[problem_id] = _existing_pack_row(source, target_pack)
            else:
                pack_rows[problem_id] = _copy_pack(
                    source,
                    target_pack,
                    evaluator_calls=evaluator_calls,
                )

        rows: list[dict[str, Any]] = []
        for target_id, _source in problems:
            fold = args.policy_root / f"target-{target_id}" / "policies"
            for mechanism in MECHANISMS:
                policy_path = fold / f"{mechanism}.json"
                controller = load_controller_manifest(policy_path)
                if controller.mechanism_id != mechanism:
                    raise ProtocolError(f"policy mechanism mismatch: {policy_path}")
                load_controller_model_routes(args.controller_model_routes, controller=controller)
                if target_id in controller.training_problem_ids:
                    raise ProtocolError(f"target trace leakage in policy: {policy_path}")
                for seed in args.seeds:
                    run_id = f"real-loo-{mechanism}-{target_id}-s{seed}"
                    run_dir = args.out / "runs" / f"target-{target_id}" / mechanism / f"seed-{seed}"
                    identity = {
                        "study_id": "REAL_COMPUTE_MATCHED_CAUSAL_TRANSFER_V1",
                        "study_version": "real-cli-1",
                        "run_id": run_id,
                        "method_id": mechanism,
                        "problem_id": target_id,
                        "problem_family": "registered_local_pack",
                        "distribution": "real_model_local_pack",
                        "model_tier": "SAME_FROZEN_MODEL_MANIFEST",
                        "seed": seed,
                        "seed_role": "real_loo",
                    }
                    row = _run_one(
                        pack=packs_dir / target_id,
                        policy=policy_path,
                        routes=args.controller_model_routes,
                        model_manifest=args.model_manifest,
                        run_dir=run_dir,
                        identity=identity,
                        env=env,
                        timeout=args.timeout,
                        reuse=(
                            args.resume
                            and run_dir.is_dir()
                            and all(
                                (run_dir / filename).is_file()
                                for filename in (
                                    "run_identity.json",
                                    "manifest.json",
                                    "result.json",
                                    "events.jsonl",
                                    "archive.jsonl",
                                )
                            )
                        ),
                    )
                    row["problem_pack"] = pack_rows[target_id]
                    row["shared_model_manifest_sha256"] = model_hash
                    rows.append(row)

        comparisons = [_compare(rows, target_id) for target_id in problem_ids]
        # Compare the declared resource ceilings, not realized usage.  A run
        # may legitimately stop with fewer evaluator calls after its live
        # candidate pool is exhausted; that does not change the matched
        # compute contract shared by all runs.
        def budget_contract(row: dict[str, Any]) -> str:
            budgets = row.get("resource_summary", {}).get("budgets", {})
            generation = budgets.get("generation", {})
            evaluator = budgets.get("evaluator", {})
            return json.dumps({
                "generation": {
                    field: generation.get(field, {}).get("limit")
                    for field in ("records", "input_tokens", "output_tokens")
                },
                "evaluator": {
                    "calls": evaluator.get("calls", {}).get("limit"),
                },
            }, sort_keys=True)

        budget_contracts = {
            budget_contract(row)
            for row in rows
        }
        model_hashes = {row["model_manifest_sha256"] for row in rows}
        summary = {
            "schema_version": 1,
            "objective": "REAL_COMPUTE_MATCHED_CAUSAL_TRANSFER_V1",
            "classification": "real_model_frozen_policy_loo_diagnostic",
            "scientific_evidence": False,
            "model_manifest": str(args.model_manifest),
            "model_manifest_sha256": model_hash,
            "controller_model_routes": str(args.controller_model_routes),
            "controller_model_routes_sha256": _sha256(args.controller_model_routes),
            "policy_root": str(args.policy_root),
            "problems": [problem_id for problem_id, _ in problems],
            "seeds": list(args.seeds),
            "attempt_cap": ATTEMPT_CAP,
            "resource_budget_contract_count": len(budget_contracts),
            "same_resource_budget_contract": len(budget_contracts) == 1,
            "model_manifest_hash_count": len(model_hashes),
            "same_model_manifest": len(model_hashes) == 1 and model_hash in model_hashes,
            "primary_policy_sha256_count": len({
                row["controller_policy_sha256"]
                for row in rows if row["mechanism"] == PRIMARY
            }),
            "fixed_policy_sha256_count": len({
                row["controller_policy_sha256"]
                for row in rows if row["mechanism"] == FIXED
            }),
            "same_primary_frozen_policy": len({
                row["controller_policy_sha256"]
                for row in rows if row["mechanism"] == PRIMARY
            }) == 1,
            "same_fixed_frozen_policy": len({
                row["controller_policy_sha256"]
                for row in rows if row["mechanism"] == FIXED
            }) == 1,
            "runs": rows,
            "target_comparisons": comparisons,
            "aggregate": {
                "primary_mean_auc_by_generation": fmean(
                    row["primary_mean_auc_by_generation"] for row in comparisons
                ),
                "fixed_mean_auc_by_generation": fmean(
                    row["fixed_mean_auc_by_generation"] for row in comparisons
                ),
                "auc_margin": fmean(row["auc_margin"] for row in comparisons),
                # The objective is the mean AUC over all registered targets;
                # keep the per-target flags below as diagnostics.  Requiring
                # every target to clear +0.25 would contradict the stated
                # aggregate objective when a ceiling target (stringmax) ties
                # the fixed baseline at its maximum score.
                "auc_gate": fmean(row["auc_margin"] for row in comparisons) >= 0.25,
                "all_target_auc_gates": all(row["auc_gate"] for row in comparisons),
                "all_target_best_score_gates": all(row["best_score_gate"] for row in comparisons),
                "all_target_improvement_candidate_gates": all(
                    row["improvement_candidate_gate"] for row in comparisons
                ),
            },
            "audit": {
                "run_count": len(rows),
                "expected_run_count": len(problems) * len(MECHANISMS) * len(args.seeds),
                "attempt_cap_failures": sum(row["attempt_count"] != ATTEMPT_CAP for row in rows),
                "target_trace_leakage_count": sum(not row["target_trace_excluded"] for row in rows),
                "replay_mismatch_count": sum(not row["replay_hash_match"] for row in rows),
                "policy_identity_mismatch_count": sum(
                    row["controller_policy_sha256"] != row["frozen_policy_sha256"]
                    for row in rows
                ),
                "resource_ledger_invalid_count": sum(
                    row["replay_resource_ledger_valid"] is not True for row in rows
                ),
                "generation_required_telemetry_missing_count": sum(
                    bool(row["generation_required_missing_fields"]) for row in rows
                ),
                "lineage_incomplete_count": sum(
                    row["trace_parent_child_links_complete"] is not True for row in rows
                ),
                "lineage_cycle_count": sum(row["lineage_cycle_count"] or 0 for row in rows),
            },
        }
        (args.out / "real_loo_summary.json").write_bytes(canonical_json(summary))
        print(json.dumps({
            "out": str(args.out),
            "target_comparisons": comparisons,
            "aggregate": summary["aggregate"],
            "audit": summary["audit"],
        }, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ProtocolError, ValueError, subprocess.SubprocessError) as exc:
        print(f"DENIED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
