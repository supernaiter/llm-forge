import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCAFFOLD = ROOT / "tools" / "scaffold_problem_pack.py"
VALIDATOR = ROOT / "tools" / "validate_problem_pack.py"


def test_scaffold_problem_pack_validates_and_runs_mock(tmp_path):
    root = tmp_path / "scaffold_probe"
    project = root / "_tmp_scaffold_probe"
    run_dir = tmp_path / "scaffold_probe_run"

    scaffold = subprocess.run(
        [
            sys.executable,
            str(SCAFFOLD),
            "--name",
            "_tmp_scaffold_probe",
            "--root",
            str(root),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert scaffold.returncode == 0, scaffold.stderr + scaffold.stdout
    assert (project / "config.json").is_file()
    assert (project / "problem.py").is_file()

    cfg = json.loads((project / "config.json").read_text(encoding="utf-8"))
    assert cfg["generations"] == 1
    assert cfg["max_smart_calls"] == 0

    validate = subprocess.run(
        [sys.executable, str(VALIDATOR), str(project)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert validate.returncode == 0, validate.stderr + validate.stdout

    env = os.environ.copy()
    env["FORGE_MOCK"] = "1"
    run = subprocess.run(
        [
            sys.executable,
            str(ROOT / "cli.py"),
            str(project),
            "--mock",
            "--run-dir",
            str(run_dir),
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr + run.stdout
    assert (run_dir / "archive.jsonl").is_file()
