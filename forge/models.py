"""Frozen model-manifest validation for V3."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Mapping

from .protocol import ProtocolError, sha256_bytes, strict_json_loads


MODEL_TIERS = ("SMALL", "MEDIUM", "STRONG")
MODEL_FIELDS = (
    "weight_revision",
    "tokenizer_revision",
    "chat_template_sha256",
    "quantization_profile",
    "inference_runtime_digest",
    "sampling_profile",
)
FORBIDDEN_ALIASES = frozenset({
    "", "latest", "default", "main", "master", "floating",
    "unresolved", "draft", "unpinned", "small", "medium", "strong",
})
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-fA-F]{64}$")


def validate_model_manifest(manifest: Mapping[str, Any]) -> None:
    if not isinstance(manifest.get("manifest_id"), str) or not manifest.get("manifest_id"):
        raise ProtocolError("model manifest_id must be a non-empty string")
    if tuple(manifest.get("tiers", ())) != MODEL_TIERS:
        raise ProtocolError("model manifest must contain SMALL/MEDIUM/STRONG tiers")
    entries = manifest.get("models")
    if not isinstance(entries, Mapping) or set(entries) != set(MODEL_TIERS):
        raise ProtocolError("model manifest entries do not match model tiers")
    for tier in MODEL_TIERS:
        entry = entries[tier]
        if not isinstance(entry, Mapping):
            raise ProtocolError(f"model {tier} entry is not an object")
        for field in MODEL_FIELDS:
            value = entry.get(field)
            if (
                not isinstance(value, str)
                or not value.strip()
                or value.strip().lower() in FORBIDDEN_ALIASES
            ):
                raise ProtocolError(f"model {tier} field is not frozen: {field}")
        if _SHA256_RE.fullmatch(entry["chat_template_sha256"]) is None:
            raise ProtocolError(f"model {tier} chat template is not a sha256 digest")
        if _DIGEST_RE.fullmatch(entry["inference_runtime_digest"]) is None:
            raise ProtocolError(f"model {tier} runtime digest is not pinned")
        if entry["sampling_profile"].strip().lower() == "latest":
            raise ProtocolError(f"model {tier} sampling profile is floating")


def model_manifest_sha256(path: str | Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def load_model_manifest(path: str | Path) -> dict[str, Any]:
    """Load a strict, fully pinned model manifest from disk."""
    target = Path(path)
    try:
        value = strict_json_loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError(f"cannot read model manifest: {target}") from exc
    if not isinstance(value, dict):
        raise ProtocolError("model manifest must be an object")
    validate_model_manifest(value)
    return value
