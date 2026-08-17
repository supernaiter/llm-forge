"""Frozen-study manifest helpers.

The CLI's historical manifest is useful for locating a run, but it is not a
scientific freeze.  V3 requires every identity that can change the result to
be present and content-addressed before holdout starts.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from .protocol import ProtocolError, canonical_json, sha256_bytes


FROZEN_REQUIRED_FIELDS = (
    "source_commit",
    "protocol_sha256",
    "baseline_registry_sha256",
    "model_manifests_sha256",
    "task_manifests_sha256",
    "evaluator_manifests_sha256",
    "container_image_digests_sha256",
    "prompt_and_decoding_profiles_sha256",
    "metrics_summary_sha256",
)
_FORBIDDEN_VALUES = frozenset({"", "latest", "default", "UNPINNED", "floating"})
_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")
_UNRESOLVED_ASSET_STRINGS = frozenset({
    "", "draft", "latest", "default", "unpinned", "floating", "unresolved",
})
_STUDY_RESULT_HASH_FIELDS = (
    "events_sha256",
    "result_sha256",
    "evidence_sha256",
    "run_matrix_sha256",
)


def _payload(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in manifest.items() if key != "manifest_sha256"}


def manifest_sha256(manifest: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json(_payload(manifest)))


def freeze_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return a frozen copy with a self-hash; reject incomplete identities."""
    frozen = dict(manifest)
    frozen["frozen"] = True
    validate_frozen_manifest(frozen, require_self_hash=False)
    frozen["manifest_sha256"] = manifest_sha256(frozen)
    return frozen


def validate_frozen_manifest(manifest: Mapping[str, Any], *, require_self_hash: bool = True) -> None:
    """Fail closed if a manifest is missing an identity or has been changed."""
    if manifest.get("frozen") is not True:
        raise ProtocolError("manifest is not frozen")
    missing = [key for key in FROZEN_REQUIRED_FIELDS if key not in manifest]
    if missing:
        raise ProtocolError(f"manifest missing frozen fields: {', '.join(missing)}")
    for key in FROZEN_REQUIRED_FIELDS:
        value = manifest[key]
        if not isinstance(value, str) or value in _FORBIDDEN_VALUES:
            raise ProtocolError(f"manifest field is not pinned: {key}")
        if key == "source_commit":
            if _COMMIT_RE.fullmatch(value) is None:
                raise ProtocolError("manifest source_commit is not a full hexadecimal commit")
        elif _HASH_RE.fullmatch(value) is None:
            raise ProtocolError(f"manifest field is not a sha256 digest: {key}")
    if require_self_hash:
        expected = manifest.get("manifest_sha256")
        if not isinstance(expected, str) or expected != manifest_sha256(manifest):
            raise ProtocolError("manifest self-hash mismatch")


def validate_frozen_study_manifest(
    manifest: Mapping[str, Any], *, require_self_hash: bool = True
) -> None:
    """Validate study-only authority and sealed-holdout commitments.

    Generic asset manifests can be frozen with the hash fields above.  A
    registered study additionally needs an external authority identity and an
    opaque locator for the sealed holdout; otherwise a repository-local bundle
    could be mistaken for an externally authorized study.
    """
    validate_frozen_manifest(manifest, require_self_hash=require_self_hash)
    manifest_id = manifest.get("manifest_id")
    if not isinstance(manifest_id, str) or not manifest_id.strip():
        raise ProtocolError("study manifest_id must be a non-empty string")
    for field in ("external_authority_id", "sealed_holdout_locator"):
        value = manifest.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ProtocolError(f"study manifest missing {field}")
        if value.strip().lower() in _UNRESOLVED_ASSET_STRINGS:
            raise ProtocolError(f"study manifest {field} is unresolved")
    for field in _STUDY_RESULT_HASH_FIELDS:
        value = manifest.get(field)
        if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
            raise ProtocolError(f"study manifest {field} is not a sha256 digest")


def verify_manifest_unchanged(before: Mapping[str, Any], after: Mapping[str, Any]) -> None:
    """Reject any post-freeze mutation, including adding unrelated keys."""
    if dict(before) != dict(after):
        raise ProtocolError("frozen manifest changed")
    validate_frozen_manifest(after)


def validate_frozen_asset_manifest(value: Any, *, name: str) -> None:
    """Reject unresolved values in a hashed study asset.

    The study manifest authenticates bytes, but a hash alone does not prove
    that an evaluator/container/prompt asset is usable. This conservative
    check deliberately accepts asset-specific schemas while rejecting draft,
    floating, or null identities that could make a final run ambiguous.
    """
    if not isinstance(value, (Mapping, list)) or not value:
        raise ProtocolError(f"{name} must be a non-empty object or list")

    def walk(node: Any, path: str) -> None:
        if node is None:
            raise ProtocolError(f"{name} contains unresolved null at {path}")
        if isinstance(node, str) and node.strip().lower() in _UNRESOLVED_ASSET_STRINGS:
            raise ProtocolError(f"{name} contains unresolved value at {path}")
        if isinstance(node, Mapping):
            for key, child in node.items():
                walk(child, f"{path}.{key}")
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{path}[{index}]")

    walk(value, name)
