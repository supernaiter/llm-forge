import json
import os
import subprocess
import sys

from forge.controller import ComputeAwareController, SearchAction, write_controller_manifest


def test_cli_protocol_v3_mock_writes_attempt_ledger(tmp_path):
    run_dir = tmp_path / "v3-run"
    env = os.environ.copy()
    env["FORGE_MOCK"] = "1"
    proc = subprocess.run(
        [
            sys.executable,
            "cli.py",
            "projects/_probe_newproblem",
            "--mock",
            "--protocol-v3",
            "--run-dir",
            str(run_dir),
        ],
        text=True,
        capture_output=True,
        env=env,
        check=False,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    result = json.loads((run_dir / "result.json").read_text())
    assert result["attempt_count"] == 1
    assert (run_dir / "events.jsonl").is_file()
    assert result["event_ledger_status_counts"]["valid_candidate"] == 1
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["protocol_id"] == "FORGE_RESEARCH_V3"
    assert manifest["research_eligible"] is False


def test_v3_event_ledger_cannot_be_resumed_as_legacy_run(tmp_path):
    run_dir = tmp_path / "v3-run"
    env = os.environ.copy()
    env["FORGE_MOCK"] = "1"
    first = subprocess.run(
        [sys.executable, "cli.py", "projects/_probe_newproblem", "--mock",
         "--protocol-v3", "--run-dir", str(run_dir)],
        text=True, capture_output=True, env=env, check=False, timeout=30,
    )
    assert first.returncode == 0, first.stderr + first.stdout
    second = subprocess.run(
        [sys.executable, "cli.py", "projects/_probe_newproblem", "--mock",
         "--run-dir", str(run_dir)],
        text=True, capture_output=True, env=env, check=False, timeout=30,
    )
    assert second.returncode == 2
    assert "--protocol-v3" in second.stderr


def test_legacy_manifest_is_not_labeled_as_v3_research(tmp_path):
    run_dir = tmp_path / "legacy-run"
    env = os.environ.copy()
    env["FORGE_MOCK"] = "1"
    proc = subprocess.run(
        [
            sys.executable,
            "cli.py",
            "projects/_probe_newproblem",
            "--mock",
            "--run-dir",
            str(run_dir),
        ],
        text=True,
        capture_output=True,
        env=env,
        check=False,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["execution_mode"] == "LEGACY"
    assert manifest["protocol_v3"] is False
    assert manifest["protocol_id"] == "FORGE_LEGACY"
    assert manifest["research_eligible"] is False


def test_nonmock_v3_requires_frozen_controller_policy(tmp_path):
    run_dir = tmp_path / "v3-real"
    env = os.environ.copy()
    env.pop("FORGE_MOCK", None)
    env["FORGE_REAL_RUN_ALLOWED"] = "1"
    proc = subprocess.run(
        [
            sys.executable,
            "cli.py",
            "projects/_probe_newproblem",
            "--protocol-v3",
            "--run-dir",
            str(run_dir),
        ],
        text=True,
        capture_output=True,
        env=env,
        check=False,
        timeout=30,
    )
    assert proc.returncode == 2
    assert "--controller-policy" in proc.stderr


def test_cli_v3_binds_controller_model_routes_and_hashes_manifest(tmp_path):
    model_id = "SMALL@sha256:" + "a" * 64
    action = SearchAction(model_id, "elite", "local", 1, 0, "uniform")
    controller = ComputeAwareController([action])
    controller.fit([{
        "split": "dev",
        "problem_id": "probe",
        "action": action,
        "quality_gain": 1.0,
        "cost": 1.0,
    }])
    controller.freeze()
    policy = tmp_path / "controller.json"
    write_controller_manifest(controller, policy, source_traces_sha256="a" * 64)
    routes = tmp_path / "routes.json"
    routes.write_text(json.dumps({
        "schema_version": 1,
        "manifest_id": "ROUTES_CLI_TEST_V1",
        "routes": {
            model_id: {
                "tier": "SMALL",
                "adapter_id": "mock-small-v1",
                "model_manifest_sha256": "b" * 64,
            },
        },
    }))
    run_dir = tmp_path / "v3-routed"
    env = os.environ.copy()
    env["FORGE_MOCK"] = "1"
    proc = subprocess.run(
        [
            sys.executable,
            "cli.py",
            "projects/_probe_newproblem",
            "--mock",
            "--protocol-v3",
            "--controller-policy", str(policy),
            "--controller-model-routes", str(routes),
            "--run-dir", str(run_dir),
        ],
        text=True,
        capture_output=True,
        env=env,
        check=False,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["controller_model_routes_manifest_id"] == "ROUTES_CLI_TEST_V1"
    assert len(manifest["controller_model_routes_sha256"]) == 64
    result = json.loads((run_dir / "result.json").read_text())
    assert result["controller_actions"][0]["action"]["generator_model"] == model_id
