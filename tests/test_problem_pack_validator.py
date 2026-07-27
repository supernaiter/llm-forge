import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "tools" / "validate_problem_pack.py"


def run_validator(project: Path, verdict: Path):
    return subprocess.run(
        [sys.executable, str(VALIDATOR), str(project), "--json", str(verdict)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def write_pack(project: Path, problem_source: str, config="{}"):
    project.mkdir()
    (project / "config.json").write_text(config, encoding="utf-8")
    (project / "problem.py").write_text(problem_source, encoding="utf-8")


VALID_PROBLEM = """
class Problem:
    DESCRIPTION = "valid problem"

    def seed(self):
        return ["seed"]

    def score(self, cand: str):
        return float(len(cand)), True
"""


def test_probe_problem_pack_validator_json_contract(tmp_path):
    verdict_path = tmp_path / "nested" / "problem_pack_contract.json"

    result = run_validator(ROOT / "projects" / "_probe_newproblem", verdict_path)
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))

    assert result.returncode == 0, result.stderr + result.stdout
    assert verdict["ok"] is True
    assert verdict["seed_count"] >= 1
    assert verdict["score_check_count"] >= 1
    assert verdict["error_count"] == 0


def test_invalid_missing_description_exits_2(tmp_path):
    project = tmp_path / "missing_description"
    write_pack(
        project,
        """
class Problem:
    def seed(self):
        return ["seed"]

    def score(self, cand: str):
        return 1.0, True
""",
    )
    verdict_path = tmp_path / "missing_description.json"

    result = run_validator(project, verdict_path)
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))

    assert result.returncode == 2
    assert verdict["ok"] is False
    assert verdict["error_count"] >= 1


def test_invalid_empty_seed_exits_2(tmp_path):
    project = tmp_path / "empty_seed"
    write_pack(
        project,
        """
class Problem:
    DESCRIPTION = "empty seed"

    def seed(self):
        return []

    def score(self, cand: str):
        return 1.0, True
""",
    )
    verdict_path = tmp_path / "empty_seed.json"

    result = run_validator(project, verdict_path)
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))

    assert result.returncode == 2
    assert verdict["ok"] is False
    assert verdict["error_count"] >= 1


def test_invalid_bad_score_exits_2(tmp_path):
    project = tmp_path / "bad_score"
    write_pack(
        project,
        """
class Problem:
    DESCRIPTION = "bad score"

    def seed(self):
        return ["seed"]

    def score(self, cand: str):
        return "bad"
""",
    )
    verdict_path = tmp_path / "bad_score.json"

    result = run_validator(project, verdict_path)
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))

    assert result.returncode == 2
    assert verdict["ok"] is False
    assert verdict["error_count"] >= 1
