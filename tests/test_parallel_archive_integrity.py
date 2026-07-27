import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_parallel_archive_integrity_verifier():
    result = subprocess.run(
        [
            sys.executable,
            "tools/check_parallel_archive_integrity.py",
            "--out",
            "artifacts/parallel_archive_integrity.json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout

    verdict = json.loads((ROOT / "artifacts" / "parallel_archive_integrity.json").read_text(encoding="utf-8"))
    assert verdict["verdict"] == "pass"
    assert verdict["jsonl_valid"] is True
    assert verdict["archive_lines"] == verdict["successful_candidates"]
    assert verdict["failed_candidates"] >= 2
