import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_cli_writes_run_manifest():
    run_dir = ROOT / "runs" / "manifest_probe"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    env = os.environ.copy()
    env["FORGE_MOCK"] = "1"

    proc = subprocess.run(
        [
            sys.executable,
            "cli.py",
            "projects/_probe_newproblem",
            "--mock",
            "--run-dir",
            "runs/manifest_probe",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout

    manifest_path = run_dir / "manifest.json"
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    config_bytes = (ROOT / "projects" / "_probe_newproblem" / "config.json").read_bytes()

    assert manifest["project"] == "projects/_probe_newproblem"
    assert manifest["mock"] is True
    assert manifest["run_dir"] == "runs/manifest_probe"
    assert manifest["config_sha256"] == hashlib.sha256(config_bytes).hexdigest()
    assert len(manifest["config_sha256"]) == 64
    assert manifest["archive_path"] == "runs/manifest_probe/archive.jsonl"
    assert (run_dir / "archive.jsonl").is_file()
