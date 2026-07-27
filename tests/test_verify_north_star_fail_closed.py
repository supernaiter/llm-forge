import json
import subprocess
from pathlib import Path

import tools.verify_north_star as verifier


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts" / "north_star_fail_closed.json"


def _completed(command, code, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, code, stdout, stderr)


def test_north_star_verifier_fail_closed_cases(tmp_path, monkeypatch):
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    monkeypatch.setattr(verifier, "_archive_evidence", lambda started_at, run_dir=None: {"archive_files": [], "fresh_archive_count": 0})

    def fake_run(command, cwd, env, text, capture_output, timeout, check):
        assert cwd == tmp_path
        assert text is True
        assert capture_output is True
        assert check is False
        if command[:3] == ["python3", "-m", "pytest"]:
            return _completed(command, 1, "", "assertion failed\n")
        if command == ["python3", "cli.py", "projects/_probe_newproblem", "--mock"]:
            assert env["FORGE_MOCK"] == "1"
            return _completed(command, 0, "=== BEST ===\n", "")
        if command == ["python3", "tests/bench_v0.py"]:
            return _completed(
                command,
                0,
                "sequential_secs=8.000\nparallel_secs=5.000\nratio=0.625\n",
                "",
            )
        raise AssertionError(command)

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)

    cases = [
        verifier.run_check("pytest", ["python3", "-m", "pytest", "tests/", "-x", "-q"]),
        verifier.run_check(
            "probe_newproblem",
            ["python3", "cli.py", "projects/_probe_newproblem", "--mock"],
            env={"FORGE_MOCK": "1"},
        ),
        verifier.run_check("bench_v0", ["python3", "tests/bench_v0.py"]),
    ]
    false_passes = [case for case in cases if case["passed"]]

    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(
        json.dumps(
            {
                "cases": len(cases),
                "false_passes": len(false_passes),
                "case_names": [case["name"] for case in cases],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    assert len(cases) == 3
    assert false_passes == []


def test_north_star_verifier_accepts_artifact_alias(tmp_path, monkeypatch):
    monkeypatch.setattr(verifier, "verify", lambda probe_run_dir=None: {"passed": True, "checks": []})

    artifact = tmp_path / "north_star_verify.json"
    assert verifier.main(["--artifact", str(artifact)]) == 0
    assert json.loads(artifact.read_text(encoding="utf-8"))["passed"] is True
