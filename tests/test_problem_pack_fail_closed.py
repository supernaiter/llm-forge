import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "tools" / "check_problem_pack_fail_closed.py"


def test_problem_pack_failures_do_not_look_successful(tmp_path):
    verdict_path = tmp_path / "problem_pack_fail_closed.json"

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
        timeout=60,
        check=False,
    )
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))

    assert result.returncode == 0, result.stderr + result.stdout
    assert verdict["passed"] is True
    assert verdict["case_count"] == 4
    assert {case["name"] for case in verdict["results"]} == {
        "broken_problem_py",
        "score_missing",
        "missing_config",
        "write_attempt",
    }
    for case in verdict["results"]:
        assert case["cli_exit_nonzero"] is True
        assert case["success_archive_lines"] == 0
        assert case["write_target_exists"] is False
