"""Pinned controller-model routing manifests for CLI and API execution.

The controller stores the exact generator identity it selected.  This module
binds each such identity to an existing Forge adapter tier without pretending
that the route manifest itself freezes weights, tokenizer, or runtime assets.
Those model assets remain a separate externally frozen input to a registered
study.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .controller import ComputeAwareController
from .models import load_model_manifest, model_manifest_sha256
from .protocol import ProtocolError, sha256_file, strict_json_loads


MODEL_ROUTE_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_TIERS = frozenset({"SMALL", "MEDIUM", "STRONG"})
_FORBIDDEN_IDENTITIES = frozenset({
    "", "latest", "default", "main", "master", "floating", "unresolved",
})


@dataclass(frozen=True)
class ControllerModelRoutes:
    """Validated route manifest plus its content hash."""

    manifest_id: str
    routes: dict[str, str]
    adapter_ids: dict[str, str]
    model_manifest_hashes: dict[str, str]
    path: Path
    sha256: str


def _validate_identity(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(f"{field} must be a non-empty model identity")
    normalized = value.strip()
    if normalized.lower() in _FORBIDDEN_IDENTITIES:
        raise ProtocolError(f"{field} must be pinned, not a floating alias")
    return normalized


def load_controller_model_routes(
    path: str | Path,
    *,
    controller: ComputeAwareController | None = None,
) -> ControllerModelRoutes:
    """Load and validate a route manifest for a frozen controller.

    Every route names a Forge adapter tier and a content hash for the external
    model manifest that is expected to define that tier.  The route file does
    not load hidden assets or credentials.
    """
    target = Path(path)
    try:
        raw = strict_json_loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError(f"cannot read controller model routes: {target}") from exc
    if not isinstance(raw, Mapping):
        raise ProtocolError("controller model route manifest must be an object")
    if raw.get("schema_version") != MODEL_ROUTE_SCHEMA_VERSION:
        raise ProtocolError("unsupported controller model route schema version")
    manifest_id = raw.get("manifest_id")
    if not isinstance(manifest_id, str) or not manifest_id.strip():
        raise ProtocolError("controller model route manifest_id must be non-empty")
    entries = raw.get("routes")
    if not isinstance(entries, Mapping) or not entries:
        raise ProtocolError("controller model route manifest requires routes")

    routes: dict[str, str] = {}
    adapter_ids: dict[str, str] = {}
    model_manifest_hashes: dict[str, str] = {}
    for identity_value, entry in entries.items():
        identity = _validate_identity(identity_value, field="route model identity")
        if identity in routes:
            raise ProtocolError(f"duplicate controller model route: {identity}")
        if not isinstance(entry, Mapping):
            raise ProtocolError(f"controller model route is not an object: {identity}")
        tier = entry.get("tier")
        if not isinstance(tier, str) or tier.strip().upper() not in _ALLOWED_TIERS:
            raise ProtocolError(f"controller model route tier is invalid: {identity}")
        adapter_id = entry.get("adapter_id")
        if not isinstance(adapter_id, str) or not adapter_id.strip():
            raise ProtocolError(f"controller model route adapter_id is missing: {identity}")
        model_hash = entry.get("model_manifest_sha256")
        if not isinstance(model_hash, str) or _SHA256_RE.fullmatch(model_hash) is None:
            raise ProtocolError(
                f"controller model route model_manifest_sha256 is invalid: {identity}"
            )
        routes[identity] = tier.strip().upper()
        adapter_ids[identity] = adapter_id.strip()
        model_manifest_hashes[identity] = model_hash

    if controller is not None:
        required = {action.generator_model for action in controller.actions}
        missing = sorted(required - set(routes))
        if missing:
            raise ProtocolError(
                "controller model route manifest is missing identities: "
                + ", ".join(missing)
            )

    return ControllerModelRoutes(
        manifest_id=manifest_id.strip(),
        routes=routes,
        adapter_ids=adapter_ids,
        model_manifest_hashes=model_manifest_hashes,
        path=target,
        sha256=sha256_file(target),
    )


def build_controller_model_callers(
    routes: ControllerModelRoutes,
    *,
    caller_factory,
    seed: int = 0,
) -> dict[str, Any]:
    """Instantiate one adapter caller per routed model identity.

    ``caller_factory`` is injected so the CLI can use ``make_caller`` while
    tests and API users can provide a pinned adapter factory.  A route never
    silently falls back to the default cheap caller.
    """
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ProtocolError("controller model route seed must be an integer")
    callers: dict[str, Any] = {}
    for index, (identity, tier) in enumerate(sorted(routes.routes.items())):
        try:
            caller = caller_factory(
                tier,
                seed=seed + index,
                model_identity=identity,
            )
        except TypeError as exc:
            # Keep the factory injection seam backwards compatible for tests
            # and older embedders while allowing the production adapter to
            # retain the controller's logical route identity in telemetry.
            if "unexpected keyword argument" not in str(exc):
                raise
            try:
                caller = caller_factory(tier, seed=seed + index)
            except TypeError as seed_exc:
                if "unexpected keyword argument 'seed'" not in str(seed_exc):
                    raise
                caller = caller_factory(tier)
        if not callable(caller):
            raise ProtocolError(f"controller model adapter is not callable: {identity}")
        callers[identity] = caller
    return callers


def validate_routes_against_model_manifest(
    routes: ControllerModelRoutes,
    model_manifest_path: str | Path,
) -> str:
    """Require every route hash to match one validated frozen model manifest."""
    manifest = load_model_manifest(model_manifest_path)
    actual_hash = model_manifest_sha256(model_manifest_path)
    for identity, route_hash in routes.model_manifest_hashes.items():
        if route_hash != actual_hash:
            raise ProtocolError(
                "controller model route hash differs from supplied model manifest: "
                + identity
            )
        tier = routes.routes[identity]
        if tier not in manifest["models"]:
            raise ProtocolError(f"model manifest is missing routed tier: {tier}")
    return actual_hash
