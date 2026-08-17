"""V3 development/holdout task-manifest validation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .protocol import ProtocolError, sha256_bytes


class TaskManifestError(ProtocolError):
    pass


_ALLOWED_DISTRIBUTIONS = frozenset({
    "iid_heldout", "size_shift", "distribution_shift",
})


def validate_task_manifest(
    manifest: Mapping[str, Any], *, require_sealed: bool = False,
    protocol_spec: Mapping[str, Any] | None = None,
) -> None:
    """Validate the exact structural holdout requirements from the V3 contract."""
    if not isinstance(manifest.get("manifest_id"), str) or not manifest.get("manifest_id"):
        raise TaskManifestError("manifest_id must be a non-empty string")
    if "sealed" in manifest and not isinstance(manifest["sealed"], bool):
        raise TaskManifestError("sealed must be boolean when present")
    if (
        "hidden_content_in_search_bundle" in manifest
        and not isinstance(manifest["hidden_content_in_search_bundle"], bool)
    ):
        raise TaskManifestError("hidden_content_in_search_bundle must be boolean when present")
    dev = manifest.get("development_problems")
    holdout = manifest.get("holdout_problems")
    if not isinstance(dev, list) or not isinstance(holdout, list):
        raise TaskManifestError("development_problems and holdout_problems must be lists")
    if len(holdout) != 10:
        raise TaskManifestError("holdout must contain exactly 10 problems")
    if not all(isinstance(item, Mapping) for item in holdout):
        raise TaskManifestError("holdout problem entries must be objects")
    development_metadata = manifest.get("development_metadata", [])
    if not isinstance(development_metadata, list) or not all(
        isinstance(item, Mapping) for item in development_metadata
    ):
        raise TaskManifestError("development_metadata must be a list of objects")
    if not all(isinstance(item, str) and item for item in dev):
        raise TaskManifestError("development problem IDs must be non-empty strings")
    if isinstance(protocol_spec, Mapping):
        expected_dev = protocol_spec.get("development_problems")
        if isinstance(expected_dev, list) and dev != expected_dev:
            raise TaskManifestError("development problem IDs differ from frozen protocol")
    dev_ids = set(dev)
    holdout_ids = [item.get("problem_id") for item in holdout]
    if (
        len(holdout_ids) != len(set(holdout_ids))
        or any(not isinstance(item, str) or not item for item in holdout_ids)
    ):
        raise TaskManifestError("holdout problem IDs must be present and unique")
    if dev_ids & set(holdout_ids):
        raise TaskManifestError("development and holdout problem IDs overlap")
    if any(
        not isinstance(item.get("problem_family"), str)
        or not item.get("problem_family")
        for item in holdout
    ):
        raise TaskManifestError("holdout problem families must be non-empty strings")
    families = {item["problem_family"] for item in holdout}
    holdout_requirements = protocol_spec.get("holdout") if isinstance(protocol_spec, Mapping) else None
    family_min = 8
    absent_family_min = 5
    external_pack_min = 5
    size_shift_min = 6
    distribution_shift_min = 6
    if isinstance(holdout_requirements, Mapping):
        for field, default in (
            ("distinct_problem_families_min", family_min),
            ("families_absent_from_development_min", absent_family_min),
            ("external_repository_packs_min", external_pack_min),
            ("size_shift_problems_min", size_shift_min),
            ("distribution_shift_problems_min", distribution_shift_min),
        ):
            value = holdout_requirements.get(field)
            if isinstance(value, int) and not isinstance(value, bool):
                if field == "distinct_problem_families_min":
                    family_min = value
                elif field == "families_absent_from_development_min":
                    absent_family_min = value
                elif field == "external_repository_packs_min":
                    external_pack_min = value
                elif field == "size_shift_problems_min":
                    size_shift_min = value
                elif field == "distribution_shift_problems_min":
                    distribution_shift_min = value
    if len(families) < family_min:
        raise TaskManifestError("holdout requires at least 8 distinct problem families")
    dev_families = {item.get("problem_family") for item in development_metadata}
    if len(families - dev_families) < absent_family_min:
        raise TaskManifestError("at least 5 holdout families must be absent from development")
    if sum(bool(item.get("external_repository_pack")) for item in holdout) < external_pack_min:
        raise TaskManifestError("at least 5 holdout packs must be external repository packs")
    for item in holdout:
        distributions = item.get("distributions")
        if not isinstance(distributions, list) or not all(
            isinstance(value, str) for value in distributions
        ):
            raise TaskManifestError("each holdout distributions field must be a string list")
        if not distributions or not set(distributions) <= _ALLOWED_DISTRIBUTIONS:
            raise TaskManifestError("holdout distributions contain an unknown or empty value")
        if item.get("external_repository_pack") not in {True, False}:
            raise TaskManifestError("external_repository_pack must be boolean")
        for count_key in (
            "search_instance_clusters", "test_instance_clusters", "hidden_test_instances"
        ):
            count = item.get(count_key)
            if isinstance(count, bool) or not isinstance(count, int):
                raise TaskManifestError(f"{count_key} must be an integer")
        if item.get("search_instance_clusters", 0) < 50:
            raise TaskManifestError("each holdout needs at least 50 search instance clusters")
        if item.get("test_instance_clusters", 0) < 100:
            raise TaskManifestError("each holdout needs at least 100 test instance clusters")
        if item.get("hidden_test_instances", 0) < 500:
            raise TaskManifestError("each holdout needs at least 500 hidden test instances")
    size_shift = sum("size_shift" in item.get("distributions", []) for item in holdout)
    distribution_shift = sum("distribution_shift" in item.get("distributions", []) for item in holdout)
    if size_shift < size_shift_min or distribution_shift < distribution_shift_min:
        raise TaskManifestError("size_shift and distribution_shift each require 6 problems")
    if require_sealed:
        if manifest.get("sealed") is not True:
            raise TaskManifestError("task manifest is not sealed")
        if manifest.get("hidden_content_in_search_bundle") is not False:
            raise TaskManifestError("hidden content must be absent from search bundle")


def task_manifest_sha256(path: str | Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def search_visible_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return public metadata only; reject hidden values instead of filtering guesses."""
    validate_task_manifest(manifest, require_sealed=False)
    # A search-facing view must never be produced from an asset whose hidden
    # content state is omitted or affirmative.  Silently filtering fields
    # would make a malformed bundle look safe and would weaken the external
    # holdout barrier.
    if manifest.get("hidden_content_in_search_bundle") is not False:
        raise TaskManifestError(
            "search-visible manifest requires hidden_content_in_search_bundle=false"
        )
    public = {
        "manifest_id": manifest.get("manifest_id"),
        "development_problems": manifest["development_problems"],
        "holdout_problems": [],
    }
    for item in manifest["holdout_problems"]:
        public["holdout_problems"].append({
            "problem_id": item["problem_id"],
            "problem_family": item["problem_family"],
            "distributions": item["distributions"],
            "search_instance_clusters": item["search_instance_clusters"],
        })
    return public
