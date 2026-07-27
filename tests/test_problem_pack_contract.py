import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_generated_problem_pack_runs_with_cli_mock(tmp_path):
    project = tmp_path / "generated_problem"
    project.mkdir()
    config = project / "config.json"
    problem = project / "problem.py"

    config.write_text(
        json.dumps(
            {
                "generations": 1,
                "batch_size": 1,
                "max_cheap_calls": 1,
                "max_smart_calls": 0,
                "archive_capacity": 5,
                "parents": 1,
                "seed": 7,
                "workers": 1,
            }
        ),
        encoding="utf-8",
    )
    problem.write_text(
        "\n".join(
            [
                "class Problem:",
                "    DESCRIPTION = 'generated contract problem'",
                "",
                "    def seed(self):",
                "        return ['seed contract']",
                "",
                "    def score(self, cand: str):",
                "        cand = cand.strip()",
                "        if not cand:",
                "            return float('-inf'), False",
                "        return float(len(cand)), True",
                "",
            ]
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["FORGE_MOCK"] = "1"
    result = subprocess.run(
        [sys.executable, str(ROOT / "cli.py"), str(project), "--mock"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert config.exists()
    assert problem.exists()

    archives = sorted((tmp_path / "runs").glob("generated_problem-*/archive.jsonl"))
    assert archives, result.stderr + result.stdout
    assert len(archives[-1].read_text(encoding="utf-8").splitlines()) >= 1
