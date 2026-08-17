import json
import subprocess
import sys
from pathlib import Path

import pytest

from forge.controller import (
    ComputeAwareController,
    SearchAction,
    load_controller_manifest,
    write_controller_manifest,
)
from forge.protocol import ProtocolError


def _actions():
    return [
        SearchAction("SMALL", "elite", "local", 1, 0, "uniform"),
        SearchAction("MEDIUM", "diverse", "global", 2, 1, "score_spread"),
    ]


def _traces():
    return [
        {
            "split": "dev",
            "problem_id": "obp_dev_v1",
            "action": _actions()[0],
            "quality_gain": 1.0,
            "cost": 1.0,
        },
        {
            "split": "dev",
            "problem_id": "tsp_dev_v1",
            "action": _actions()[1],
            "quality_gain": 3.0,
            "cost": 1.0,
        },
    ]


def test_frozen_controller_manifest_round_trips_and_hashes(tmp_path):
    controller = ComputeAwareController(_actions())
    controller.fit(_traces())
    controller.freeze()
    path = tmp_path / "controller.json"
    manifest = write_controller_manifest(
        controller, path, source_traces_sha256="a" * 64
    )
    loaded = load_controller_manifest(path)
    assert loaded.frozen is True
    assert loaded.policy_sha256 == controller.policy_sha256
    assert loaded.training_problem_ids == ("obp_dev_v1", "tsp_dev_v1")
    assert loaded.gain_normalization_scales == controller.gain_normalization_scales
    assert manifest["manifest_sha256"] == json.loads(path.read_text())["manifest_sha256"]


def test_controller_manifest_rejects_mutation_and_holdout_update(tmp_path):
    controller = ComputeAwareController(_actions())
    controller.fit(_traces())
    controller.freeze()
    path = tmp_path / "controller.json"
    write_controller_manifest(controller, path, source_traces_sha256="a" * 64)
    value = json.loads(path.read_text())
    value["controller_holdout_update_attempts"] = 1
    path.write_text(json.dumps(value) + "\n")
    with pytest.raises(ProtocolError):
        load_controller_manifest(path)


def test_freeze_controller_tool_rejects_holdout_trace(tmp_path):
    traces = tmp_path / "traces.jsonl"
    traces.write_text(json.dumps({
        "split": "holdout",
        "problem_id": "h00",
        "action": _actions()[0].__dict__,
        "quality_gain": 1.0,
        "cost": 1.0,
    }) + "\n")
    actions = tmp_path / "actions.json"
    actions.write_text(json.dumps([action.__dict__ for action in _actions()]))
    out = tmp_path / "controller.json"
    proc = subprocess.run(
        [
            sys.executable,
            "tools/freeze_controller.py",
            "--traces", str(traces),
            "--actions", str(actions),
            "--out", str(out),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 2
    assert not out.exists()


def test_freeze_controller_tool_rejects_nonfinite_trace(tmp_path):
    traces = tmp_path / "traces.jsonl"
    traces.write_text('{"split": "dev", "quality_gain": NaN}\n')
    actions = tmp_path / "actions.json"
    actions.write_text(json.dumps([action.__dict__ for action in _actions()]))
    out = tmp_path / "controller.json"
    proc = subprocess.run(
        [
            sys.executable,
            "tools/freeze_controller.py",
            "--traces", str(traces),
            "--actions", str(actions),
            "--out", str(out),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "non-finite" in proc.stderr
    assert not out.exists()
