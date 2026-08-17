import json

import pytest

from forge.baselines import (
    OPEN_CANDIDATES,
    PEER_REQUIRED,
    baseline_eligible,
    adapter_conformance_report,
    baseline_registry_report,
    baseline_readiness_report,
    load_registry,
    primary_baselines,
)
from forge.protocol import ProtocolError


def test_registry_has_modern_mandatory_and_open_frontier_names():
    registry = load_registry()
    names = {entry["name"] for entry in registry["entries"]}
    assert set(PEER_REQUIRED) <= names
    assert set(OPEN_CANDIDATES) <= names
    assert not any(baseline_eligible(entry) for entry in registry["entries"])


def test_registry_rejects_short_or_nonhex_source_commits():
    registry = load_registry()
    registry["entries"][0]["source_commit"] = "not-a-commit"
    with pytest.raises(ProtocolError):
        from forge.baselines import validate_registry
        validate_registry(registry)


def test_registry_rejects_cutoff_or_post_freeze_mutation():
    registry = load_registry()
    registry["baseline_cutoff_utc"] = "2026-08-02T00:00:00Z"
    from forge.baselines import validate_registry
    with pytest.raises(ProtocolError):
        validate_registry(registry)
    registry = load_registry()
    registry["post_unblinding_baseline_additions"] = 1
    with pytest.raises(ProtocolError):
        validate_registry(registry)


def test_registry_rejects_post_cutoff_source_observation():
    registry = load_registry()
    entry = registry["entries"][0]
    entry["source_commit"] = "a" * 40
    entry["source_commit_observed_at"] = "2026-08-02T00:00:00Z"
    with pytest.raises(ProtocolError):
        from forge.baselines import validate_registry
        validate_registry(registry)


def test_unresolved_registry_fails_closed_in_primary_selection():
    with pytest.raises(ProtocolError):
        primary_baselines(load_registry())


def test_registry_rejects_missing_peer(tmp_path):
    registry = load_registry()
    registry["entries"] = [e for e in registry["entries"] if e["name"] != "EoH"]
    with pytest.raises(ProtocolError):
        from forge.baselines import validate_registry
        validate_registry(registry)


def test_adapter_conformance_is_fail_closed_and_requires_deterministic_smoke():
    entry = {
        "name": "synthetic",
        "status": "eligible",
        "source_commit": "a" * 40,
        "source_commit_observed_at": "2026-08-01T00:00:00Z",
        "public_before_baseline_cutoff": True,
        "source_url": "https://example.invalid/synthetic",
        "license_id": "MIT",
        "adapter_id": "synthetic-v3",
        "native_smoke_evidence_sha256": "b" * 64,
        "adapter_conformance_evidence_sha256": "c" * 64,
        "algorithm_change_audit_sha256": "d" * 64,
        "license_allows_evaluation": True,
        "native_smoke_tests_pass": True,
        "forge_adapter_conformance_pass": True,
        "no_material_algorithm_change_required": True,
    }
    assert adapter_conformance_report(entry, lambda: {"score": 1})["adapter_conformance_pass"] is True
    counter = {"n": 0}
    def nondeterministic():
        counter["n"] += 1
        return counter["n"]
    report = adapter_conformance_report(entry, nondeterministic)
    assert report["adapter_conformance_pass"] is False


def test_eligible_baseline_without_evidence_cannot_be_selected():
    entry = {
        "name": "synthetic",
        "status": "eligible",
        "source_commit": "a" * 40,
        "source_commit_observed_at": "2026-08-01T00:00:00Z",
        "public_before_baseline_cutoff": True,
        "license_allows_evaluation": True,
        "native_smoke_tests_pass": True,
        "forge_adapter_conformance_pass": True,
        "no_material_algorithm_change_required": True,
    }
    assert baseline_eligible(entry) is False


def test_registry_report_explains_unresolved_entries_without_calling_them_zero():
    registry = load_registry()
    report = baseline_registry_report(registry)
    shinka = next(item for item in report if item["name"] == "ShinkaEvolve")
    assert shinka["eligible"] is False
    assert "status_is_eligible" in shinka["failed_predicates"]
    assert "evidence:adapter_id" in shinka["failed_predicates"]
    synthetic = {
        "name": "synthetic", "status": "unresolved", "source_commit": None,
        "source_commit_observed_at": None, "public_before_baseline_cutoff": False,
        "license_allows_evaluation": False, "native_smoke_tests_pass": False,
        "forge_adapter_conformance_pass": False,
        "no_material_algorithm_change_required": False,
    }
    assert baseline_readiness_report(synthetic)["eligible"] is False
