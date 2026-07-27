import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "tools" / "check_runs_dir_isolation.py"


def test_cli_run_dir_isolated_from_repo_runs(tmp_path):
    verdict_path = tmp_path / "runs_dir_isolation.json"

    result = subprocess.run(
        [sys.executable, str(CHECKER), "--out", str(verdict_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))

    assert result.returncode == 0, result.stderr + result.stdout
    assert verdict["passed"] is True
    assert verdict["repo_runs_new_files"] == 0
    assert verdict["repo_runs_changed_files"] == []
    assert verdict["repo_runs_deleted_files"] == []
    assert verdict["isolated_archive_exists"] is True
    assert verdict["isolated_manifest_exists"] is True
