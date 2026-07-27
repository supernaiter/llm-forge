import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "tools" / "smoke_problem_packs.py"


def test_smoke_problem_packs_runs_all_local_packs():
    result = subprocess.run(
        [
            sys.executable,
            str(SMOKE),
            "--run-root",
            "runs/problem_pack_smoke",
            "--artifact",
            "artifacts/problem_pack_smoke.json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout

    artifact = ROOT / "artifacts" / "problem_pack_smoke.json"
    verdict = json.loads(artifact.read_text(encoding="utf-8"))
    assert verdict["ok"] is True
    assert verdict["total"] >= 3
    assert verdict["failed"] == 0

    for pack in ("_probe_newproblem", "bench_obp", "bench_tsp", "stringmax"):
        archive = ROOT / "runs" / "problem_pack_smoke" / pack / "archive.jsonl"
        assert archive.is_file()
        assert len(archive.read_text(encoding="utf-8").splitlines()) >= 1
