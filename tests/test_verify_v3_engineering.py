import json
import subprocess
import sys


def test_v3_engineering_verifier_reports_dry_run_and_external_gaps(tmp_path):
    output = tmp_path / "v3.json"
    proc = subprocess.run(
        [sys.executable, "tools/verify_v3_engineering.py", "--json", str(output)],
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    verdict = json.loads(output.read_text(encoding="utf-8"))
    assert verdict["mock_dry_run"] is True
    assert verdict["replay_recomputes_attempts"] is True
    assert verdict["metric_engine_smoke"] is True
    assert verdict["verdict_engine_smoke"] is True
    assert verdict["terminal_state_smoke"] == {
        "blocked_integrity_failure": "BLOCKED_INTEGRITY_FAILURE",
        "clean_falsification": "CLEAN_FALSIFICATION",
        "inconclusive": "INCONCLUSIVE",
        "strong_positive": "STRONG_POSITIVE",
    }
    assert verdict["native_track_smoke"] is True
    assert verdict["native_replay"]["tracks"] == ["NATIVE_COMPUTE"]
    assert verdict["native_replay"]["resource_summary"]["telemetry_complete"] is True
    assert verdict["native_cli_smoke"] is True
    assert verdict["native_cli_replay"]["tracks"] == ["NATIVE_COMPUTE"]
    assert verdict["native_cli_replay"]["resource_summary"]["telemetry_complete"] is True
    assert verdict["sandbox_smoke"] is True
    assert verdict["sandbox_smoke_details"]["numeric_math_result"] == 5.0
    assert verdict["sandbox_smoke_details"]["denied"] == {
        "numpy_load": True,
        "open": True,
    }
    assert verdict["replay"]["generation_slot_count"] == verdict["attempt_count"]
    assert verdict["replay"]["decision_hash"]
    assert verdict["replay"]["result_recomputation_hash"]
    # Engineering readiness is the public mock/replay contract.  External
    # baseline conformance and frozen study assets gate registered research,
    # not the local engineering dry run.
    assert verdict["v3_engineering_ready"] is True
    assert verdict["research_finished"] is False
    assert "read_only_external_verifier" in verdict["missing_external_requirements"]
    assert verdict["external_asset_checks"]["frozen_model_manifest"] is False
    assert verdict["external_asset_checks"]["sealed_task_manifest"] is False
