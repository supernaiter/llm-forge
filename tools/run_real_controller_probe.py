#!/usr/bin/env python3
"""Run a single-target, frozen-policy real-model probe.

This is a diagnostic companion to ``run_real_controller_loo.py``.  It accepts
one registered pack and one frozen policy, keeps the same four-attempt and
model-manifest checks, and emits per-seed rows without treating the probe as
the final four-target causal result.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_real_controller_loo as loo  # noqa: E402
from forge.controller import load_controller_manifest  # noqa: E402
from forge.model_routes import (  # noqa: E402
    load_controller_model_routes,
    validate_routes_against_model_manifest,
)
from forge.protocol import canonical_json, load_protocol, ProtocolError  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--problem", required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--mechanism", default=loo.PRIMARY, choices=loo.MECHANISMS)
    parser.add_argument("--controller-model-routes", type=Path, required=True)
    parser.add_argument("--model-manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", action="append", dest="seeds", type=int, required=True)
    parser.add_argument("--attempts", type=int, default=loo.ATTEMPT_CAP)
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args(argv)
    try:
        if args.attempts != loo.ATTEMPT_CAP:
            raise ProtocolError("real causal probe requires exactly four attempts")
        if not args.seeds or len(set(args.seeds)) != len(args.seeds):
            raise ProtocolError("probe seeds must be unique")
        if args.out.exists() and any(args.out.iterdir()):
            raise ProtocolError(f"output directory is not empty: {args.out}")
        args.out.mkdir(parents=True, exist_ok=True)
        target_id, source = loo._parse_problem(args.problem)
        controller = load_controller_manifest(args.policy)
        if controller.mechanism_id != args.mechanism:
            raise ProtocolError("probe policy mechanism mismatch")
        if target_id in controller.training_problem_ids:
            raise ProtocolError("target trace leakage in probe policy")
        routes = load_controller_model_routes(args.controller_model_routes, controller=controller)
        model_hash = validate_routes_against_model_manifest(routes, args.model_manifest)
        env = dict(os.environ)
        loo._require_json_cli_environment(env)
        evaluator_calls = int(load_protocol()["budgets"]["max_search_evaluations"])
        pack = args.out / "derived_pack"
        pack_row = loo._copy_pack(source, pack, evaluator_calls=evaluator_calls)
        rows = []
        for seed in args.seeds:
            run_id = f"probe-{args.mechanism}-{target_id}-s{seed}"
            run_dir = args.out / "runs" / args.mechanism / f"seed-{seed}"
            identity = {
                "study_id": "REAL_COMPUTE_MATCHED_CAUSAL_TRANSFER_V1",
                "study_version": "real-cli-1-probe",
                "run_id": run_id,
                "method_id": args.mechanism,
                "problem_id": target_id,
                "problem_family": "registered_local_pack",
                "distribution": "real_model_local_pack",
                "model_tier": "SAME_FROZEN_MODEL_MANIFEST",
                "seed": seed,
                "seed_role": "real_policy_probe",
            }
            row = loo._run_one(
                pack=pack,
                policy=args.policy,
                routes=args.controller_model_routes,
                model_manifest=args.model_manifest,
                run_dir=run_dir,
                identity=identity,
                env=env,
                timeout=args.timeout,
            )
            row["shared_model_manifest_sha256"] = model_hash
            rows.append(row)
        summary = {
            "schema_version": 1,
            "objective": "REAL_COMPUTE_MATCHED_CAUSAL_TRANSFER_V1",
            "classification": "real_model_frozen_policy_probe",
            "target_problem_id": target_id,
            "mechanism": args.mechanism,
            "seeds": list(args.seeds),
            "attempt_cap": loo.ATTEMPT_CAP,
            "policy": str(args.policy),
            "model_manifest": str(args.model_manifest),
            "model_manifest_sha256": model_hash,
            "problem_pack": pack_row,
            "runs": rows,
        }
        (args.out / "probe_summary.json").write_bytes(canonical_json(summary))
        print(json.dumps({
            "out": str(args.out),
            "target_problem_id": target_id,
            "mechanism": args.mechanism,
            "runs": [
                {
                    "seed": row["seed"],
                    "best_score": row["best_score"],
                    "auc_by_generation": row["metrics"].get("auc_by_generation"),
                    "attempt_count": row["attempt_count"],
                }
                for row in rows
            ],
        }, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ProtocolError, ValueError, json.JSONDecodeError) as exc:
        print(f"DENIED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
