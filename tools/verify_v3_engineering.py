#!/usr/bin/env python3
"""Run the public V3 mock audit and report engineering readiness honestly.

This command never treats unresolved external baselines or sealed holdout
assets as present.  A false readiness result is a useful, machine-readable
status rather than a failed dry-run.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forge.baselines import (  # noqa: E402
    baseline_registry_report,
    load_registry,
    primary_baselines,
)
from forge.ledger import EventLedger, candidate_sha256  # noqa: E402
from forge.lineage import lineage_metadata  # noqa: E402
from forge.protocol import load_protocol, protocol_hash  # noqa: E402
from forge.replay import replay_summary  # noqa: E402
from forge.research_metrics import (  # noqa: E402
    auc_attempt,
    hierarchical_bootstrap,
    oracle_delta_statistic,
)
from forge.resources import evaluator_usage, generation_usage  # noqa: E402
from forge.sandbox import SandboxError, run_python_candidate  # noqa: E402
from forge.traceability import load_traceability  # noqa: E402
from forge.verdict import (  # noqa: E402
    INTEGRITY_BOOLEAN_FLAGS,
    INTEGRITY_ZERO_FIELDS,
    STRONG_BOOLEAN_GATES,
    final_verdict,
)


def _run(command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=ROOT, env=env, text=True, capture_output=True,
        check=False, timeout=60,
    )


def _metric_verdict_smoke() -> tuple[bool, bool, dict[str, str]]:
    """Exercise deterministic metric/bootstrap and verdict primitives.

    This is deliberately a tiny public fixture, not scientific evidence.  It
    catches an import/API regression in the engineering gate without reading a
    holdout or manufacturing a research result.
    """
    try:
        scores = {"seed": 0.0, "candidate": 2.0}
        auc = auc_attempt(
            ["seed", "candidate"], scores,
            seed_reference=0.0, fixed_reference=2.0,
        )
        rows = [
            {"problem_family": "f", "problem": "p", "seed": 1,
             "cluster": 1, "forge": 1.0, "baselines": {"b": 0.0}},
            {"problem_family": "f", "problem": "p", "seed": 1,
             "cluster": 2, "forge": 1.0, "baselines": {"b": 0.0}},
        ]
        first = hierarchical_bootstrap(
            rows, oracle_delta_statistic, replicates=4, seed=2026080901
        )
        second = hierarchical_bootstrap(
            rows, oracle_delta_statistic, replicates=4, seed=2026080901
        )
        metric_ok = auc == 0.5 and first == second and len(first) == 4

        evidence = {
            **{field: True for field in INTEGRITY_BOOLEAN_FLAGS},
            **{field: 0 for field in INTEGRITY_ZERO_FIELDS},
            **{field: True for field in STRONG_BOOLEAN_GATES},
        }
        strong = final_verdict(evidence)

        clean_evidence = {
            **{field: True for field in INTEGRITY_BOOLEAN_FLAGS},
            **{field: 0 for field in INTEGRITY_ZERO_FIELDS},
            "primary_and_required_extension_complete": True,
            "q1_status": "clean_negative",
            "q2_status": "strong_positive",
            "q3_status": "strong_positive",
            "q4_status": "strong_positive",
            "post_unblinding_changes": 0,
        }
        clean = final_verdict(clean_evidence)

        inconclusive_evidence = {
            **{field: True for field in INTEGRITY_BOOLEAN_FLAGS},
            **{field: 0 for field in INTEGRITY_ZERO_FIELDS},
            "primary_and_required_extension_complete": True,
            "q1_status": "inconclusive",
            "q2_status": "inconclusive",
            "q3_status": "inconclusive",
            "q4_status": "inconclusive",
            "post_unblinding_changes": 0,
        }
        inconclusive = final_verdict(inconclusive_evidence)

        blocked_evidence = dict(inconclusive_evidence)
        blocked_evidence["budget_violation_count"] = 1
        blocked = final_verdict(blocked_evidence)
        terminal_states = {
            "strong_positive": strong,
            "clean_falsification": clean,
            "inconclusive": inconclusive,
            "blocked_integrity_failure": blocked,
        }
        verdict_ok = set(terminal_states.values()) == {
            "STRONG_POSITIVE",
            "CLEAN_FALSIFICATION",
            "INCONCLUSIVE",
            "BLOCKED_INTEGRITY_FAILURE",
        }
        return metric_ok, verdict_ok, terminal_states
    except Exception:
        return False, False, {}


def _native_track_smoke(root: Path) -> tuple[bool, dict[str, Any]]:
    """Exercise native-compute ledger/replay with explicit mock telemetry.

    The allocation and model-forward values are tiny plumbing observations;
    this fixture is never treated as a scientific native-compute result.
    """
    path = root / "native-smoke" / "events.jsonl"
    try:
        candidate = "native mock candidate"
        ledger = EventLedger(
            path,
            run_id="forge-v3-native-smoke",
            max_attempts=1,
            resource_budgets={
                "generation": {"records": 1, "gpu_seconds": 10.0},
                "evaluator": {"calls": 1},
            },
        )
        attempt_id = ledger.start_attempt(
            generation=1,
            slot=0,
            model="STRONG-native-mock-20260809",
            track="NATIVE_COMPUTE",
        )
        ledger.finish_attempt(
            attempt_id,
            status="valid_candidate",
            candidate_hash=candidate_sha256(candidate),
            score=1.0,
            resource_usage=generation_usage(
                input_tokens=1,
                output_tokens=1,
                model_identity="STRONG-native-mock-20260809",
                sampling_profile={"temperature": 0.0},
                wall_time_ms=1.0,
                gpu_allocation={"device_type": "A100", "count": 1, "seconds": 1.0},
                model_forward_time_ms=1.0,
            ),
            metadata={
                **lineage_metadata(candidate, []),
                "evaluator_hack_audit": {
                    "parseable": True,
                    "suspected_hack": False,
                    "findings": [],
                },
            },
        )
        ledger.record_evaluation(
            attempt_id,
            resource_usage=evaluator_usage(
                wall_time_ms=1.0,
                evaluator_cost=0.001,
                evaluator_id="native-mock-evaluator",
            ),
        )
        ledger.record_event("incumbent_selected", {
            "attempt_id": attempt_id,
            "after_attempt": 1,
            "candidate_sha256": candidate_sha256(candidate),
            "score": 1.0,
        })
        summary = ledger.summary()
        replay = replay_summary(path)
        generation_totals = replay["resource_summary"]["phases"]["generation"]["totals"]
        ok = (
            summary["tracks"] == ["NATIVE_COMPUTE"]
            and replay["tracks"] == ["NATIVE_COMPUTE"]
            and replay["resource_ledger_valid"] is True
            and replay["resource_summary"]["telemetry_complete"] is True
            and generation_totals["gpu_seconds"] == 1.0
            and generation_totals["model_forward_time_ms"] == 1.0
        )
        return ok, replay
    except Exception as exc:
        return False, {"error": f"{type(exc).__name__}: {exc}"}


def _native_cli_smoke(root: Path) -> tuple[bool, dict[str, Any]]:
    """Run the actual CLI/loop native-track path with opt-in mock telemetry."""
    problem_dir = root / "native-probe"
    run_dir = root / "native-cli-run"
    try:
        problem_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "projects" / "_probe_newproblem" / "problem.py", problem_dir / "problem.py")
        config = {
            "generations": 1,
            "batch_size": 1,
            "max_cheap_calls": 1,
            "max_smart_calls": 0,
            "archive_capacity": 5,
            "parents": 1,
            "seed": 11,
            "workers": 1,
            "track": "NATIVE_COMPUTE",
            "max_attempts": 1,
        }
        (problem_dir / "config.json").write_text(
            json.dumps(config, sort_keys=True) + "\n", encoding="utf-8"
        )
        env = {
            **os.environ,
            "FORGE_MOCK": "1",
            "FORGE_MOCK_NATIVE_TELEMETRY": "1",
        }
        proc = _run([
            sys.executable,
            "cli.py",
            str(problem_dir),
            "--mock",
            "--protocol-v3",
            "--run-dir",
            str(run_dir),
        ], env)
        events_path = run_dir / "events.jsonl"
        result_path = run_dir / "result.json"
        if proc.returncode != 0 or not events_path.is_file() or not result_path.is_file():
            return False, {"returncode": proc.returncode, "stderr": proc.stderr[-500:]}
        replay = replay_summary(events_path)
        result = json.loads(result_path.read_text(encoding="utf-8"))
        generation_totals = replay["resource_summary"]["phases"]["generation"]["totals"]
        ok = (
            result.get("track") == "NATIVE_COMPUTE"
            and replay.get("tracks") == ["NATIVE_COMPUTE"]
            and replay.get("attempt_count") == result.get("attempt_count")
            and replay.get("resource_ledger_valid") is True
            and replay.get("resource_summary") == result.get("resource_summary")
            and replay["resource_summary"]["telemetry_complete"] is True
            and generation_totals.get("gpu_seconds") == 0.001
            and generation_totals.get("model_forward_time_ms") == 0.5
        )
        return ok, replay
    except Exception as exc:
        return False, {"error": f"{type(exc).__name__}: {exc}"}


def _sandbox_smoke() -> tuple[bool, dict[str, Any]]:
    """Exercise safe numeric execution and explicit hidden-file denials.

    The public engineering command intentionally runs with the system Python,
    so the safe execution probe uses the stdlib ``math`` module rather than
    assuming that optional numpy is installed in that interpreter.  Numpy
    file-backed access is still checked through the V3 AST gate.
    """
    details: dict[str, Any] = {}
    try:
        details["numeric_math_result"] = run_python_candidate(
            "import math\n"
            "def f(x):\n"
            "    return math.sqrt(x * x)\n",
            "f",
            args=(5.0,),
            policy="v3",
        )
        denied_sources = {
            "open": "def f():\n    return open('hidden-score')\n",
            "numpy_load": (
                "import numpy as np\n"
                "def f():\n"
                "    return np.load('hidden.npy')\n"
            ),
        }
        details["denied"] = {}
        for name, source in denied_sources.items():
            try:
                run_python_candidate(source, "f", policy="v3")
            except SandboxError:
                details["denied"][name] = True
            else:
                details["denied"][name] = False
        ok = details["numeric_math_result"] == 5.0 and all(
            details["denied"].values()
        )
        return ok, details
    except Exception as exc:
        details["error"] = f"{type(exc).__name__}: {exc}"
        return False, details


def verify() -> dict[str, Any]:
    protocol = load_protocol()
    registry = load_registry()
    with tempfile.TemporaryDirectory(prefix="forge-v3-engineering-") as tmp:
        run_dir = Path(tmp) / "run"
        env = {**os.environ, "FORGE_MOCK": "1"}
        proc = _run([
            sys.executable, "cli.py", "projects/_probe_newproblem", "--mock",
            "--protocol-v3", "--run-dir", str(run_dir),
        ], env)
        dry_run_ok = proc.returncode == 0 and (run_dir / "events.jsonl").is_file()
        replay = None
        replay_ok = False
        result: dict[str, Any] = {}
        if dry_run_ok:
            replay = replay_summary(run_dir / "events.jsonl")
            result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
            replay_ok = (
                replay["attempt_count"] == result.get("attempt_count")
                and replay["finished_attempt_count"] == replay["attempt_count"]
                and replay.get("generation_slot_count") == replay["attempt_count"]
                and len({
                    (item.get("generation"), item.get("slot"))
                    for item in replay.get("generation_slots", [])
                }) == replay.get("attempt_count")
                and replay.get("decision_hash") == result.get("decision_hash")
                and replay.get("result_recomputation_hash")
                == result.get("result_recomputation_hash")
                and replay.get("resource_ledger_valid") is True
                and replay.get("resource_summary") == result.get("resource_summary")
                and replay.get("resource_ledger_hash") == result.get("resource_ledger_hash")
            )
        native_track_smoke, native_replay = _native_track_smoke(Path(tmp))
        native_cli_smoke, native_cli_replay = _native_cli_smoke(Path(tmp))
        sandbox_smoke, sandbox_details = _sandbox_smoke()

    baseline_conformance = True
    baseline_report: list[dict[str, Any]] = []
    try:
        primary_baselines(registry)
    except ValueError:
        baseline_conformance = False
    try:
        baseline_report = baseline_registry_report(registry)
    except ValueError:
        baseline_report = []

    traceability_valid = True
    try:
        load_traceability()
    except ValueError:
        traceability_valid = False
    metric_engine_smoke, verdict_engine_smoke, terminal_state_smoke = _metric_verdict_smoke()

    external_asset_checks = {
        "frozen_model_manifest": (ROOT / "protocol" / "model_manifest_v3.json").is_file(),
        "sealed_task_manifest": (ROOT / "protocol" / "task_manifest_v3.json").is_file(),
        "evaluator_manifest": (ROOT / "protocol" / "evaluator_manifest_v3.json").is_file(),
        "container_manifest": (ROOT / "protocol" / "container_manifest_v3.json").is_file(),
        "prompt_and_decoding_manifest": (
            ROOT / "protocol" / "prompt_and_decoding_manifest_v3.json"
        ).is_file(),
        "external_read_only_verifier": False,
    }

    checks = {
        "protocol_valid": protocol["protocol_id"] == "FORGE_RESEARCH_V3",
        "protocol_hash": protocol_hash(),
        "baseline_registry_valid": True,
        "baseline_conformance": baseline_conformance,
        "baseline_report": baseline_report,
        "traceability_valid": traceability_valid,
        "metric_engine_smoke": metric_engine_smoke,
        "verdict_engine_smoke": verdict_engine_smoke,
        "terminal_state_smoke": terminal_state_smoke,
        "native_track_smoke": native_track_smoke,
        "native_replay": native_replay,
        "native_cli_smoke": native_cli_smoke,
        "native_cli_replay": native_cli_replay,
        "sandbox_smoke": sandbox_smoke,
        "sandbox_smoke_details": sandbox_details,
        "external_asset_checks": external_asset_checks,
        "public_read_only_verifier_present": (
            ROOT / "tools" / "verify_v3_research.py"
        ).is_file(),
        "mock_dry_run": dry_run_ok,
        "replay_recomputes_attempts": replay_ok,
        "resource_ledger_valid": bool(
            isinstance(replay, dict) and replay.get("resource_ledger_valid") is True
        ),
        "resource_telemetry_complete": bool(
            isinstance(replay, dict)
            and replay.get("resource_summary", {}).get("telemetry_complete") is True
        ),
        "attempt_count": result.get("attempt_count"),
        "replay": replay,
    }
    missing = []
    if not baseline_conformance:
        missing.append("mandatory_baseline_conformance")
    missing.extend([
        "external_frozen_model_manifests",
        "sealed_holdout_task_and_distribution_manifests",
        "read_only_external_verifier",
        "external_verifier_receipt",
    ])
    checks["missing_external_requirements"] = missing
    checks["v3_engineering_ready"] = all([
        checks["protocol_valid"],
        checks["baseline_registry_valid"],
        checks["traceability_valid"],
        checks["public_read_only_verifier_present"],
        checks["mock_dry_run"],
        checks["replay_recomputes_attempts"],
        checks["resource_ledger_valid"],
        checks["resource_telemetry_complete"],
        checks["metric_engine_smoke"],
        checks["verdict_engine_smoke"],
        checks["native_track_smoke"],
        checks["native_cli_smoke"],
        checks["sandbox_smoke"],
    ])
    checks["research_finished"] = False
    checks["research_finished_reason"] = "no external frozen study or terminal verifier result"
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit Forge Research V3 engineering dry-run readiness.")
    parser.add_argument("--json", help="write the verdict JSON to this path")
    args = parser.parse_args(argv)
    verdict = verify()
    payload = json.dumps(verdict, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.json:
        Path(args.json).write_text(payload, encoding="utf-8")
    print(payload, end="")
    # A dry-run tool is operationally healthy even when research readiness is
    # false because required external assets are intentionally unresolved.
    return 0 if verdict["mock_dry_run"] and verdict["replay_recomputes_attempts"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
