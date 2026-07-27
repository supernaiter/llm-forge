import json
import subprocess
from pathlib import Path

import tools.verify_north_star as verifier


ROOT = Path(__file__).resolve().parents[1]


def test_north_star_verifier_json_contract(tmp_path, monkeypatch):
    def fake_run(command, cwd, env, text, capture_output, timeout, check):
        assert cwd == ROOT
        assert text is True
        assert capture_output is True
        assert check is False
        if command[:3] == ["python3", "-m", "pytest"]:
            return subprocess.CompletedProcess(command, 0, "12 passed\n", "")
        if command == ["python3", "cli.py", "projects/_probe_newproblem", "--mock"]:
            assert env["FORGE_MOCK"] == "1"
            return subprocess.CompletedProcess(command, 0, "=== BEST ===\n", "")
        if command == ["python3", "tests/bench_v0.py"]:
            return subprocess.CompletedProcess(
                command,
                0,
                "sequential_secs=8.000\nparallel_secs=1.000\nratio=0.125\n",
                "",
            )
        raise AssertionError(command)

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)
    monkeypatch.setattr(
        verifier,
        "_archive_evidence",
        lambda started_at, run_dir=None: {
            "archive_files": ["runs/_probe_newproblem-test/archive.jsonl"],
            "fresh_archive_count": 1,
        },
    )

    verdict_path = tmp_path / "nested" / "north_star_verify.json"
    assert verifier.main(["--out", str(verdict_path)]) == 0

    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    assert verdict["passed"] is True
    assert len(verdict["checks"]) == 3
    assert sorted(check["name"] for check in verdict["checks"]) == [
        "bench_v0",
        "probe_newproblem",
        "pytest",
    ]
    assert {check["name"] for check in verdict["checks"]} == {"pytest", "probe_newproblem", "bench_v0"}
    for check in verdict["checks"]:
        assert check["passed"] is True
        assert isinstance(check["command"], str) and check["command"]
        assert check["exit_code"] == 0
        assert isinstance(check["stdout_tail"], list)
        assert "artifacts" in check
