import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "tools" / "validate_problem_pack.py"


def run_negative_fixtures(verdict: Path):
    return subprocess.run(
        [
            sys.executable,
            str(VALIDATOR),
            "--negative-fixtures",
            "--out",
            str(verdict),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_problem_pack_validator_rejects_broken_builtin_fixtures(tmp_path):
    verdict_path = tmp_path / "problem_pack_validator_negative.json"

    result = run_negative_fixtures(verdict_path)
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))

    assert result.returncode == 0, result.stderr + result.stdout
    assert verdict["ok"] is True
    assert verdict["cases"] >= 4
    assert verdict["false_accepts"] == 0
    assert {case["name"] for case in verdict["results"]} >= {
        "missing_config",
        "missing_problem_py",
        "invalid_json",
        "missing_callables",
    }
