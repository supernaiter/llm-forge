from __future__ import annotations

import json
from pathlib import Path

from forge.paper_claim import audit_paper_claim


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "artifacts" / "method-comparison-v2-prompt-audit-full"


def test_current_bundle_is_explicitly_matched_adapter_only():
    report = audit_paper_claim(BUNDLE)
    assert report["gates"]["matched_adapter_descriptive_claim_ready"] is True
    assert report["gates"]["native_surpass_claim_ready"] is False
    assert report["claim_scope"] == "matched_model_adapter_only"
    assert report["checks"]["native_paper_reproduction"]["status"] == "fail"
    assert report["checks"]["statistical_contract"]["status"] == "fail"


def test_native_gate_fails_closed_if_manifest_flag_is_changed(tmp_path):
    copied = tmp_path / "bundle"
    copied.mkdir()
    for name in ("comparison_manifest.json", "comparison_summary.json", "fairness_receipt.json"):
        (copied / name).write_bytes((BUNDLE / name).read_bytes())
    manifest_path = copied / "comparison_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["native_paper_reproduction"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = audit_paper_claim(copied)
    assert report["gates"]["native_surpass_claim_ready"] is False
    assert report["checks"]["mandatory_baseline_registry_eligibility"]["status"] == "fail"
    assert report["checks"]["positive_gate_metrics"]["status"] == "missing"
