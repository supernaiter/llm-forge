import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROJECT = "projects/_probe_newproblem"
RUN_DIR = "runs/resume_manifest_probe"
RUN_DIR_PATH = ROOT / RUN_DIR


def _run_cli(run_dir: str):
    env = os.environ.copy()
    env["FORGE_MOCK"] = "1"
    return subprocess.run(
        [
            sys.executable,
            "cli.py",
            PROJECT,
            "--mock",
            "--run-dir",
            run_dir,
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )


def _load_manifest(run_dir: Path):
    return json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))


def test_cli_resume_manifest_keeps_manifest_and_archive_consistent():
    if RUN_DIR_PATH.exists():
        shutil.rmtree(RUN_DIR_PATH)

    config_bytes = (ROOT / PROJECT / "config.json").read_bytes()
    expected_hash = hashlib.sha256(config_bytes).hexdigest()
    expected_manifest = {
        "run_dir": RUN_DIR,
        "archive_path": f"{RUN_DIR}/archive.jsonl",
        "config_sha256": expected_hash,
        "mock": True,
        "project": PROJECT,
    }

    first = _run_cli(RUN_DIR)
    assert first.returncode == 0, first.stderr + first.stdout
    manifest1 = _load_manifest(RUN_DIR_PATH)

    second = _run_cli(RUN_DIR)
    assert second.returncode == 0, second.stderr + second.stdout
    manifest2 = _load_manifest(RUN_DIR_PATH)

    for key, value in expected_manifest.items():
        assert manifest1[key] == value
        assert manifest2[key] == value

    assert manifest1 == manifest2

    archive_path = RUN_DIR_PATH / "archive.jsonl"
    assert archive_path.is_file()
    archive_lines = [line for line in archive_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(archive_lines) >= 2
