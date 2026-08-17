import pytest

from forge.manifest import (
    FROZEN_REQUIRED_FIELDS,
    freeze_manifest,
    validate_frozen_manifest,
    verify_manifest_unchanged,
    validate_frozen_asset_manifest,
)
from forge.protocol import ProtocolError


def _draft():
    return {
        "source_commit": "a" * 40,
        "protocol_sha256": "b" * 64,
        "baseline_registry_sha256": "c" * 64,
        "model_manifests_sha256": "d" * 64,
        "task_manifests_sha256": "e" * 64,
        "evaluator_manifests_sha256": "f" * 64,
        "container_image_digests_sha256": "1" * 64,
        "prompt_and_decoding_profiles_sha256": "2" * 64,
        "metrics_summary_sha256": "3" * 64,
    }


def test_freeze_manifest_adds_self_hash_and_validates():
    frozen = freeze_manifest(_draft())
    validate_frozen_manifest(frozen)
    assert frozen["frozen"] is True
    assert len(frozen["manifest_sha256"]) == 64
    assert set(FROZEN_REQUIRED_FIELDS) <= set(frozen)
    verify_manifest_unchanged(frozen, dict(frozen))


def test_freeze_manifest_rejects_unpinned_identity():
    draft = _draft()
    draft["protocol_sha256"] = "UNPINNED"
    with pytest.raises(ProtocolError):
        freeze_manifest(draft)
    draft = _draft()
    draft["metrics_summary_sha256"] = "not-a-hash"
    with pytest.raises(ProtocolError):
        freeze_manifest(draft)


def test_frozen_manifest_mutation_is_detected():
    frozen = freeze_manifest(_draft())
    changed = dict(frozen)
    changed["source_commit"] = "z" * 40
    with pytest.raises(ProtocolError):
        verify_manifest_unchanged(frozen, changed)


def test_hashed_asset_manifest_rejects_draft_and_null_values():
    validate_frozen_asset_manifest({"status": "frozen", "digest": "sha256:abc"}, name="asset")
    with pytest.raises(ProtocolError):
        validate_frozen_asset_manifest({"status": "DRAFT"}, name="asset")
    with pytest.raises(ProtocolError):
        validate_frozen_asset_manifest({"status": "frozen", "digest": None}, name="asset")
