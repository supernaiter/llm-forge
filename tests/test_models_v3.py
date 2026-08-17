import pytest

from forge.models import MODEL_FIELDS, MODEL_TIERS, validate_model_manifest
from forge.protocol import ProtocolError


def _manifest():
    return {
        "manifest_id": "models-v3-test",
        "tiers": list(MODEL_TIERS),
        "models": {
            tier: {
                **{field: f"{tier.lower()}-{field}-pinned" for field in MODEL_FIELDS},
                "chat_template_sha256": "a" * 64,
                "inference_runtime_digest": "sha256:" + "b" * 64,
            }
            for tier in MODEL_TIERS
        },
    }


def test_model_manifest_requires_all_pinned_identities():
    validate_model_manifest(_manifest())
    broken = _manifest()
    broken["models"]["SMALL"]["weight_revision"] = "latest"
    with pytest.raises(ProtocolError):
        validate_model_manifest(broken)


def test_model_manifest_rejects_missing_tier():
    broken = _manifest()
    del broken["models"]["MEDIUM"]
    with pytest.raises(ProtocolError):
        validate_model_manifest(broken)


def test_model_manifest_rejects_non_object_model_entry():
    broken = _manifest()
    broken["models"]["SMALL"] = "floating"
    with pytest.raises(ProtocolError):
        validate_model_manifest(broken)


def test_model_manifest_rejects_unhashed_runtime_identity():
    broken = _manifest()
    broken["models"]["SMALL"]["chat_template_sha256"] = "not-a-hash"
    with pytest.raises(ProtocolError):
        validate_model_manifest(broken)


def test_model_manifest_rejects_unresolved_identity_aliases():
    for alias in ("unresolved", "DRAFT", "unpinned", " latest ", "small", "   "):
        broken = _manifest()
        broken["models"]["SMALL"]["weight_revision"] = alias
        with pytest.raises(ProtocolError):
            validate_model_manifest(broken)
