import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts" / "run_real_lock.json"


def _repo_lock_names() -> list[str]:
    return sorted(path.name for path in (ROOT / "runs").glob(".real_run_*.lock"))


def test_run_real_daily_lock_uses_temp_runs_dir(tmp_path):
    temp_runs = tmp_path / "runs"
    env = os.environ.copy()
    env["FORGE_MOCK"] = "1"
    env["FORGE_REAL_RUNS_DIR"] = str(temp_runs)
    env.pop("FORGE_REAL_RUN_ALLOWED", None)

    before_locks = _repo_lock_names()
    first = subprocess.run(
        ["zsh", "tools/run_real.sh", "projects/_probe_newproblem"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    second = subprocess.run(
        ["zsh", "tools/run_real.sh", "projects/_probe_newproblem"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    after_locks = _repo_lock_names()
    temp_locks = sorted(path.name for path in temp_runs.glob(".real_run_*.lock"))

    verdict = {
        "passed": first.returncode == 0
        and second.returncode != 0
        and before_locks == after_locks
        and len(temp_locks) == 1,
        "first_exit": first.returncode,
        "second_exit": second.returncode,
        "repo_locks_before": before_locks,
        "repo_locks_after": after_locks,
        "temp_lock_count": len(temp_locks),
    }
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert verdict["first_exit"] == 0, first.stderr + first.stdout
    assert verdict["second_exit"] != 0
    assert before_locks == after_locks
    assert len(temp_locks) == 1


def test_run_real_lock_is_per_pack(tmp_path):
    temp_runs = tmp_path / "runs"
    env = os.environ.copy()
    env["FORGE_MOCK"] = "1"
    env["FORGE_REAL_RUNS_DIR"] = str(temp_runs)
    env.pop("FORGE_REAL_RUN_ALLOWED", None)

    first = subprocess.run(
        ["zsh", "tools/run_real.sh", "projects/_probe_newproblem"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    other_pack = subprocess.run(
        ["zsh", "tools/run_real.sh", "projects/stringmax"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert first.returncode == 0, first.stderr + first.stdout
    assert other_pack.returncode == 0, other_pack.stderr + other_pack.stdout
    locks = sorted(path.name for path in temp_runs.glob(".real_run_*.lock"))
    assert len(locks) == 2, locks


def test_run_real_rejects_contract_violating_pack_without_consuming_lock(tmp_path):
    bad_pack = tmp_path / "badpack"
    bad_pack.mkdir()
    (bad_pack / "problem.py").write_text("class Problem:\n    pass\n")

    temp_runs = tmp_path / "runs"
    env = os.environ.copy()
    env["FORGE_MOCK"] = "1"
    env["FORGE_REAL_RUNS_DIR"] = str(temp_runs)
    env.pop("FORGE_REAL_RUN_ALLOWED", None)

    result = subprocess.run(
        ["zsh", "tools/run_real.sh", str(bad_pack)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 4, result.stderr + result.stdout
    assert not temp_runs.exists() or not list(temp_runs.glob(".real_run_*.lock"))


def test_run_real_writes_log_file_matching_run_dir(tmp_path):
    temp_runs = tmp_path / "runs"
    env = os.environ.copy()
    env["FORGE_MOCK"] = "1"
    env["FORGE_REAL_RUNS_DIR"] = str(temp_runs)
    env.pop("FORGE_REAL_RUN_ALLOWED", None)

    result = subprocess.run(
        ["zsh", "tools/run_real.sh", "projects/_probe_newproblem"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    run_dirs = [p for p in temp_runs.glob("_probe_newproblem-*") if p.is_dir()]
    assert len(run_dirs) == 1, run_dirs
    log_path = run_dirs[0].parent / f"{run_dirs[0].name}.log"
    assert log_path.exists()
    assert log_path.stat().st_size > 0
