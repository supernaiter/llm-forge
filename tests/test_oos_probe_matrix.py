import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "tools" / "check_oos_probe_matrix.py"


def test_oos_probe_matrix_json_contract():
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--json", "artifacts/oos_probe_matrix.json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout

    verdict = json.loads((ROOT / "artifacts" / "oos_probe_matrix.json").read_text(encoding="utf-8"))
    assert verdict["passed"] is True
    assert verdict["pack_count"] == 3
    assert verdict["failed"] == 0
    for pack in verdict["packs"]:
        assert pack["archive_exists"] is True
        assert pack["archive_lines"] >= 1
