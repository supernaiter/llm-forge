import json
import os
import subprocess
import sys
import textwrap


def _base_env():
    env = os.environ.copy()
    env.pop("FORGE_MOCK", None)
    env.pop("FORGE_REAL_RUN_ALLOWED", None)
    return env


def test_non_mock_direct_cli_exits_2():
    proc = subprocess.run(
        [sys.executable, "cli.py", "projects/stringmax"],
        env=_base_env(),
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 2
    assert "tools/run_real.sh" in proc.stderr


def test_mock_cli_still_runs():
    proc = subprocess.run(
        [sys.executable, "cli.py", "projects/stringmax", "--mock"],
        env=_base_env(),
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr


def test_run_real_bypass_env_allows_guard_to_pass(tmp_path):
    project = tmp_path / "guard_probe"
    project.mkdir()
    (project / "config.json").write_text(
        json.dumps(
            {
                "generations": 1,
                "max_cheap_calls": 0,
                "max_smart_calls": 0,
                "archive_capacity": 5,
                "seed": 0,
            }
        )
    )
    (project / "problem.py").write_text(
        textwrap.dedent(
            """
            class Problem:
                DESCRIPTION = "guard probe"

                def seed(self):
                    return ["seed"]

                def score(self, cand):
                    return float(len(cand)), True
            """
        )
    )
    env = _base_env()
    env.update(
        {
            "FORGE_REAL_RUN_ALLOWED": "1",
            "FORGE_CHEAP_BASE_URL": "http://127.0.0.1:9",
            "FORGE_CHEAP_MODEL": "unused",
        }
    )

    proc = subprocess.run(
        [sys.executable, "cli.py", str(project)],
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    assert "非mock実行" not in proc.stderr
