import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "tools" / "check_external_pack_readonly.py"


def test_external_pack_readonly_json_contract(tmp_path):
    verdict_path = tmp_path / "external_pack_readonly.json"

    result = subprocess.run(
        [sys.executable, str(CHECKER), "--json", str(verdict_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))

    assert result.returncode == 0, result.stderr + result.stdout
    assert verdict["passed"] is True
    assert verdict["changed_files"] == []
    assert verdict["created_files"] == []
    assert verdict["deleted_files"] == []
    assert verdict["archive_files"]
