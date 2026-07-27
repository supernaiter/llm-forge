import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_parallel_budget_cap_verifier():
    result = subprocess.run(
        [
            sys.executable,
            "tools/check_parallel_budget_cap.py",
            "--out",
            "artifacts/parallel_budget_cap.json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout

    verdict = json.loads((ROOT / "artifacts" / "parallel_budget_cap.json").read_text(encoding="utf-8"))
    assert verdict["verdict"] == "pass"
    assert verdict["budget"] == 3
    assert verdict["archive_lines"] <= 3
    assert verdict["v0_evaluations"] <= 3
