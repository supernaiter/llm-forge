import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "tools" / "check_scaffold_reject_unsafe.py"


def test_scaffold_rejects_unsafe_names_without_writes(tmp_path):
    verdict_path = tmp_path / "scaffold_reject_unsafe.json"

    result = subprocess.run(
        [
            sys.executable,
            str(CHECKER),
            "--out",
            str(verdict_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))

    assert result.returncode == 0, result.stderr + result.stdout
    assert verdict["ok"] is True
    assert verdict["cases"] >= 4
    assert verdict["rejected_cases"] == verdict["cases"]
    assert verdict["created_paths"] == 0
