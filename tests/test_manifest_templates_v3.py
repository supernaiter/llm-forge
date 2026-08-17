import json
from pathlib import Path

import pytest

from forge.manifest import validate_frozen_manifest
from forge.models import validate_model_manifest
from forge.protocol import ProtocolError
from forge.tasks import TaskManifestError, validate_task_manifest


ROOT = Path(__file__).resolve().parents[1]


def test_external_manifest_templates_are_explicit_drafts():
    names = (
        "model_manifest_v3_template.json",
        "task_manifest_v3_template.json",
        "evaluator_manifest_v3_template.json",
        "container_manifest_v3_template.json",
        "prompt_and_decoding_manifest_v3_template.json",
        "study_manifest_v3_template.json",
        "run_matrix_v3_template.json",
        "external_verifier_receipt_v3_template.json",
    )
    for name in names:
        value = json.loads((ROOT / "protocol" / name).read_text(encoding="utf-8"))
        assert value["status"] == "DRAFT"


def test_draft_templates_cannot_be_used_as_frozen_assets():
    study = json.loads((ROOT / "protocol" / "study_manifest_v3_template.json").read_text())
    with pytest.raises(ProtocolError):
        validate_frozen_manifest(study)
    model = json.loads((ROOT / "protocol" / "model_manifest_v3_template.json").read_text())
    with pytest.raises(ProtocolError):
        validate_model_manifest(model)
    task = json.loads((ROOT / "protocol" / "task_manifest_v3_template.json").read_text())
    with pytest.raises(TaskManifestError):
        validate_task_manifest(task, require_sealed=True)
