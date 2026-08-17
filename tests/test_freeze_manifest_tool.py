import json
import subprocess
import sys


def test_freeze_manifest_tool_writes_self_hashed_manifest(tmp_path):
    draft = tmp_path / "draft.json"
    frozen = tmp_path / "frozen.json"
    draft.write_text(json.dumps({
        "source_commit": "a" * 40,
        "protocol_sha256": "b" * 64,
        "baseline_registry_sha256": "c" * 64,
        "model_manifests_sha256": "d" * 64,
        "task_manifests_sha256": "e" * 64,
        "evaluator_manifests_sha256": "f" * 64,
        "container_image_digests_sha256": "1" * 64,
        "prompt_and_decoding_profiles_sha256": "2" * 64,
        "metrics_summary_sha256": "3" * 64,
    }), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "tools/freeze_manifest.py", str(draft), str(frozen)],
        text=True, capture_output=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(frozen.read_text(encoding="utf-8"))
    assert payload["frozen"] is True
    assert len(payload["manifest_sha256"]) == 64


def test_freeze_manifest_tool_rejects_nonfinite_json(tmp_path):
    draft = tmp_path / "draft.json"
    frozen = tmp_path / "frozen.json"
    draft.write_text('{"source_commit": NaN}\n', encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "tools/freeze_manifest.py", str(draft), str(frozen)],
        text=True, capture_output=True, check=False,
    )
    assert proc.returncode == 2
    assert "non-finite" in proc.stderr
    assert not frozen.exists()
