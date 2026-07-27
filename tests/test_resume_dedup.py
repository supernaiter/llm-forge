import json
import subprocess
import sys


def test_resume_dedup_verifier(tmp_path):
    out = tmp_path / "resume_dedup.json"

    result = subprocess.run(
        [
            sys.executable,
            "tools/check_resume_dedup.py",
            "--out",
            str(out),
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    verdict = json.loads(out.read_text(encoding="utf-8"))
    assert verdict["verdict"] == "pass"
    assert verdict["duplicate_candidates"] >= 1
    assert verdict["new_v0_evaluations"] == 0
