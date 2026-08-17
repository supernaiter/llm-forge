import json

import pytest

from forge.controller import ComputeAwareController, SearchAction
from forge.model_routes import (
    build_controller_model_callers,
    load_controller_model_routes,
    validate_routes_against_model_manifest,
)
from forge.models import MODEL_FIELDS, MODEL_TIERS
from forge.protocol import ProtocolError


def _controller():
    return ComputeAwareController([
        SearchAction("SMALL@sha256:" + "a" * 64, "elite", "local", 1, 0, "uniform"),
        SearchAction("STRONG@sha256:" + "b" * 64, "diverse", "structural", 2, 1, "score_spread"),
    ])


def _write_routes(path, *, include_strong=True):
    routes = {
        "SMALL@sha256:" + "a" * 64: {
            "tier": "SMALL",
            "adapter_id": "local-small-v1",
            "model_manifest_sha256": "1" * 64,
        },
    }
    if include_strong:
        routes["STRONG@sha256:" + "b" * 64] = {
            "tier": "STRONG",
            "adapter_id": "local-strong-v1",
            "model_manifest_sha256": "2" * 64,
        }
    path.write_text(json.dumps({
        "schema_version": 1,
        "manifest_id": "ROUTES_TEST_V1",
        "routes": routes,
    }))


def test_model_routes_bind_every_frozen_controller_identity(tmp_path):
    path = tmp_path / "routes.json"
    _write_routes(path)
    routes = load_controller_model_routes(path, controller=_controller())
    assert routes.manifest_id == "ROUTES_TEST_V1"
    assert routes.routes["SMALL@sha256:" + "a" * 64] == "SMALL"
    assert len(routes.sha256) == 64


def test_model_routes_fail_closed_on_missing_controller_identity(tmp_path):
    path = tmp_path / "routes.json"
    _write_routes(path, include_strong=False)
    with pytest.raises(ProtocolError, match="missing identities"):
        load_controller_model_routes(path, controller=_controller())


def test_model_routes_build_callers_without_default_fallback(tmp_path):
    path = tmp_path / "routes.json"
    _write_routes(path)
    routes = load_controller_model_routes(path, controller=_controller())
    calls = []

    def factory(tier, *, seed):
        calls.append((tier, seed))
        return lambda prompt, temperature: "```routed```"

    callers = build_controller_model_callers(routes, caller_factory=factory, seed=9)
    assert set(callers) == set(routes.routes)
    assert calls == [("SMALL", 9), ("STRONG", 10)]


def test_model_routes_bind_to_the_actual_frozen_model_manifest(tmp_path):
    model_manifest = tmp_path / "models.json"
    model_manifest.write_text(json.dumps({
        "manifest_id": "MODELS_TEST_V1",
        "tiers": list(MODEL_TIERS),
        "models": {
            tier: {
                **{field: f"{tier.lower()}-{field}-pinned" for field in MODEL_FIELDS},
                "chat_template_sha256": "a" * 64,
                "inference_runtime_digest": "sha256:" + "b" * 64,
            }
            for tier in MODEL_TIERS
        },
    }))
    import hashlib
    model_hash = hashlib.sha256(model_manifest.read_bytes()).hexdigest()
    routes_path = tmp_path / "routes.json"
    routes_path.write_text(json.dumps({
        "schema_version": 1,
        "manifest_id": "ROUTES_MODEL_BINDING_V1",
        "routes": {
            "SMALL@sha256:" + "a" * 64: {
                "tier": "SMALL",
                "adapter_id": "small-v1",
                "model_manifest_sha256": model_hash,
            },
            "STRONG@sha256:" + "b" * 64: {
                "tier": "STRONG",
                "adapter_id": "strong-v1",
                "model_manifest_sha256": model_hash,
            },
        },
    }))
    routes = load_controller_model_routes(routes_path, controller=_controller())
    assert validate_routes_against_model_manifest(routes, model_manifest) == model_hash


def test_model_routes_reject_a_hash_not_matching_the_supplied_manifest(tmp_path):
    model_manifest = tmp_path / "models.json"
    model_manifest.write_text(json.dumps({
        "manifest_id": "MODELS_TEST_V1",
        "tiers": list(MODEL_TIERS),
        "models": {
            tier: {
                **{field: f"{tier.lower()}-{field}-pinned" for field in MODEL_FIELDS},
                "chat_template_sha256": "a" * 64,
                "inference_runtime_digest": "sha256:" + "b" * 64,
            }
            for tier in MODEL_TIERS
        },
    }))
    routes_path = tmp_path / "routes.json"
    _write_routes(routes_path)
    routes = load_controller_model_routes(routes_path, controller=_controller())
    with pytest.raises(ProtocolError, match="differs from supplied model manifest"):
        validate_routes_against_model_manifest(routes, model_manifest)
