import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "tools" / "check_cli_path_parity.py"


def test_cli_path_parity_json_contract():
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--json", "artifacts/cli_path_parity.json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout

    verdict = json.loads((ROOT / "artifacts" / "cli_path_parity.json").read_text(encoding="utf-8"))
    assert verdict["verdict"] == "pass"
    assert verdict["relative_exit"] == 0
    assert verdict["absolute_exit"] == 0
    assert verdict["archive_line_counts"]["relative"] >= 1
    assert verdict["archive_line_counts"]["absolute"] >= 1
    assert verdict["manifest_exists"]["relative"] is True
    assert verdict["manifest_exists"]["absolute"] is True
