import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_north_star_workspace_clean_verifier():
    if os.environ.get("FORGE_SKIP_WORKSPACE_CLEAN_TEST") == "1":
        return

    result = subprocess.run(
        [
            sys.executable,
            "tools/check_north_star_workspace_clean.py",
            "--out",
            "artifacts/north_star_workspace_clean.json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=260,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout

    verdict = json.loads((ROOT / "artifacts" / "north_star_workspace_clean.json").read_text(encoding="utf-8"))
    assert verdict["verdict"] == "pass"
    assert verdict["unexpected_repo_paths"] == []
    assert verdict["used_temp_runs_dir"] is True
    assert verdict["temp_archive_exists"] is True
    assert verdict["temp_manifest_exists"] is True
