import json
import shutil
import subprocess
from pathlib import Path

import pytest

from forge.baselines import PEER_REQUIRED
from forge.ledger import EventLedger, candidate_sha256
from forge.lineage import lineage_metadata
from forge.manifest import freeze_manifest, manifest_sha256
from forge.protocol import PROTOCOL_PATH, ProtocolError, sha256_file
from forge.replay import replay_decision_hash, replay_result_hash
from forge.result_schema import result_identity_sha256
from forge.study_matrix import matrix_sha256
from forge.study_verifier import _study_binding_hash
from forge.resources import evaluator_usage, generation_usage
from forge.study_verifier import (
    _check_hidden_events,
    _track_attempt_cap,
    _read_json,
    _validate_native_resource_consistency,
    verify_bundle,
)
from forge.verdict import INTEGRITY_BOOLEAN_FLAGS, INTEGRITY_ZERO_FIELDS


def _write_valid_clean_bundle(bundle):
    shutil.copy2(PROTOCOL_PATH, bundle / "protocol.json")
    registry = json.loads((PROTOCOL_PATH.parent / "baseline_registry_v3.json").read_text())
    for entry in registry["entries"]:
        if entry["name"] in PEER_REQUIRED:
            entry.update(
                status="eligible",
                source_commit="a" * 40,
                source_commit_observed_at="2026-08-01T00:00:00Z",
                public_before_baseline_cutoff=True,
                source_url=f"https://example.invalid/{entry['name']}",
                license_id="MIT",
                adapter_id=f"{entry['name']}-v3",
                native_smoke_evidence_sha256="b" * 64,
                adapter_conformance_evidence_sha256="c" * 64,
                algorithm_change_audit_sha256="d" * 64,
                license_allows_evaluation=True,
                native_smoke_tests_pass=True,
                forge_adapter_conformance_pass=True,
                no_material_algorithm_change_required=True,
            )
    (bundle / "baseline_registry.json").write_text(json.dumps(registry, sort_keys=True) + "\n")
    baseline_container_digest = "sha256:" + "d" * 64

    model = {"manifest_id": "FORGE_MODELS_V3_1", "tiers": ["SMALL", "MEDIUM", "STRONG"], "models": {}}
    for tier in model["tiers"]:
        model["models"][tier] = {
            "weight_revision": tier + "-weights-20260801",
            "tokenizer_revision": tier + "-tokenizer-20260801",
            "chat_template_sha256": "b" * 64,
            "quantization_profile": "fp16",
            "inference_runtime_digest": "sha256:" + "c" * 64,
            "sampling_profile": "temperature-0.8-top-p-0.95",
        }
    (bundle / "model_manifest.json").write_text(json.dumps(model, sort_keys=True) + "\n")

    holdout = []
    for i in range(10):
        holdout.append({
            "problem_id": f"h{i:02d}",
            "problem_family": f"family{i % 8}",
            "external_repository_pack": i < 5,
            "search_instance_clusters": 50,
            "test_instance_clusters": 100,
            "hidden_test_instances": 500,
            "distributions": ["iid_heldout"] + (["size_shift"] if i < 6 else []) +
                             (["distribution_shift"] if i < 6 else []),
        })
    task = {
        "manifest_id": "FORGE_TASKS_V3_1",
        "sealed": True,
        "hidden_content_in_search_bundle": False,
        "development_problems": ["obp_dev_v1", "tsp_dev_v1", "jssp_dev_v1", "capset_dev_v1"],
        "development_metadata": [
            {"problem_id": problem_id, "problem_family": f"devfam{i}"}
            for i, problem_id in enumerate(["obp_dev_v1", "tsp_dev_v1", "jssp_dev_v1", "capset_dev_v1"])
        ],
        "holdout_problems": holdout,
    }
    (bundle / "task_manifest.json").write_text(json.dumps(task, sort_keys=True) + "\n")
    bootstrap_fields = (
        "overall_delta_oracle", "overall_delta_gpu_oracle", "delta_fixed",
        "delta_transfer", "delta_cost", "overall_delta_ood",
    )
    metrics = {
        "bootstrap_replicates": 20_000,
        "bootstrap_seed": 2_026_080_901,
        "bootstrap_hierarchy": [
            "problem_family", "problem", "seed", "hidden_test_instance_cluster"
        ],
        "oracle_reselected_inside_replicate": True,
        "bootstrap_samples": {name: [0.0] * 20_000 for name in bootstrap_fields},
        "bootstrap_raw_inputs": {
            "overall_delta_oracle": [{
                "problem_family": "family0", "problem": "h00", "seed": 1,
                "hidden_test_instance_cluster": 1, "forge": 0.0,
                "baselines": {"b": 0.0},
            }],
            "overall_delta_gpu_oracle": [{
                "problem_family": "family0", "problem": "h00", "seed": 1,
                "hidden_test_instance_cluster": 1, "forge": 0.0,
                "baselines": {"b": 0.0},
            }],
            "delta_fixed": [{
                "problem_family": "family0", "problem": "h00", "seed": 1,
                "hidden_test_instance_cluster": 1, "forge": 0.0,
                "fixed_champion": 0.0,
            }],
            "delta_transfer": [{
                "problem_family": "family0", "problem": "h00", "seed": 1,
                "hidden_test_instance_cluster": 1, "forge": 0.0,
                "no_transfer": 0.0,
            }],
            "delta_cost": [{
                "problem_family": "family0", "problem": "h00", "seed": 1,
                "hidden_test_instance_cluster": 1, "forge": 0.0,
                "cost_unaware": 0.0,
            }],
            "overall_delta_ood": [{
                "problem_family": "family0", "problem": "h00", "seed": 1,
                "hidden_test_instance_cluster": 1, "distribution": "distribution_shift",
                "forge": 0.0, "baselines": {"b": 0.0},
            }],
        },
    }
    metrics.update({
        "same_model_superiority_ready": False,
        "overall_delta_oracle_95ci_high": 0.0,
        "compute_efficiency_ready": False,
        "overall_delta_gpu_oracle_95ci_high": 0.0,
        "primary_mechanism_validated": False,
        "delta_fixed_95ci_high": 0.0,
        "delta_transfer_95ci_high": 0.0,
        "delta_cost_95ci_high": 0.0,
        "ood_generalization_ready": False,
        "overall_delta_ood_95ci_high": 0.0,
        "final_quality_ready": False,
        "replication_ready": False,
    })
    for name, value in (
        ("evaluator_manifest.json", {"evaluator": "frozen-1"}),
        ("container_manifest.json", {
            "digest": baseline_container_digest,
            "baseline_containers": {
                entry["name"]: baseline_container_digest
                for entry in registry["entries"] if entry["name"] in PEER_REQUIRED
            },
        }),
        ("prompt_and_decoding_manifest.json", {"profile": "frozen-1"}),
        ("metrics_summary.json", metrics),
    ):
        (bundle / name).write_text(json.dumps(value, sort_keys=True) + "\n")

    source_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    refs = {
        "manifest_id": "FORGE_STUDY_V3_1",
        "external_authority_id": "authority.example/forge-v3/20260809",
        "sealed_holdout_locator": "sealed://authority.example/forge-v3/h01-h10",
        "source_commit": source_commit,
        "protocol_sha256": sha256_file(bundle / "protocol.json"),
        "baseline_registry_sha256": sha256_file(bundle / "baseline_registry.json"),
        "model_manifests_sha256": sha256_file(bundle / "model_manifest.json"),
        "task_manifests_sha256": sha256_file(bundle / "task_manifest.json"),
        "evaluator_manifests_sha256": sha256_file(bundle / "evaluator_manifest.json"),
        "container_image_digests_sha256": sha256_file(bundle / "container_manifest.json"),
        "prompt_and_decoding_profiles_sha256": sha256_file(bundle / "prompt_and_decoding_manifest.json"),
        "metrics_summary_sha256": sha256_file(bundle / "metrics_summary.json"),
    }
    primary_run_id = "forge-v3-fixture/forge/h00/SMALL/101"
    fixture_budgets = {
        "generation": {"records": 1},
        "evaluator": {"calls": 512},
    }
    controller_action = {
        "generator_model": "fixture-model",
        "parent_selection_policy": "elite",
        "mutation_operator": "local",
        "number_of_offspring": 1,
        "reflection_depth": 0,
        "archive_sampling_policy": "uniform",
    }
    controller_state = {
        "remaining_budget": 512,
        "improvement_slope": 0.0,
        "time_since_last_improvement": 0,
        "archive_behavioral_entropy": 0.0,
        "archive_score_dispersion": 0.0,
        "candidate_invalid_rate": 0.0,
        "duplicate_rate": 0.0,
        "parent_lineage_depth": 0.0,
        "recent_operator_success": 0.0,
        "recent_model_success": 0.0,
        "estimated_generation_cost": 1.0,
    }
    ledger = EventLedger(
        bundle / "events.jsonl",
        run_id=primary_run_id,
        max_attempts=1,
        resource_budgets=fixture_budgets,
    )
    attempt_id = ledger.start_attempt(
        generation=1, slot=0, model="fixture-model",
        metadata={"controller_action": controller_action},
    )
    candidate = "candidate"
    ledger.finish_attempt(
        attempt_id,
        status="valid_candidate",
        candidate_hash=candidate_sha256(candidate),
        score=1.0,
        resource_usage=generation_usage(
            input_tokens=1,
            output_tokens=1,
            model_identity="fixture-model",
            sampling_profile={"temperature": 0.0},
            wall_time_ms=1.0,
        ),
        metadata={
            **lineage_metadata(candidate, []),
            "evaluator_hack_audit": {
                "parseable": True,
                "suspected_hack": False,
                "findings": [],
            },
        },
    )
    ledger.record_evaluation(
        attempt_id,
        resource_usage=evaluator_usage(
            wall_time_ms=1.0,
            evaluator_cost=0.001,
            evaluator_id="fixture-evaluator",
        ),
    )
    ledger.record_event("incumbent_selected", {
        "attempt_id": attempt_id,
        "after_attempt": 1,
        "candidate_sha256": candidate_sha256(candidate),
        "score": 1.0,
    })
    (bundle / "result.json").write_text(json.dumps({
        "attempt_count": 1,
        "attempt_cap": 512,
        "track": "SAME_MODEL",
        "study_id": "forge-v3-fixture",
        "study_version": "f" * 64,
        "run_id": primary_run_id,
        "method_id": "FORGE",
        "problem_id": "h00",
        "problem_family": "family0",
        "distribution": "iid_heldout",
        "model_tier": "SMALL",
        "seed": 101,
        "seed_role": "primary",
        "run_identity_sha256": "PLACEHOLDER",
        "event_ledger_head_hash": ledger.summary()["head_hash"],
        "decision_hash": replay_decision_hash(bundle / "events.jsonl"),
        "result_recomputation_hash": replay_result_hash(bundle / "events.jsonl"),
        "resource_summary": ledger.summary()["resource_summary"],
        "resource_ledger_hash": ledger.summary()["resource_ledger_hash"],
        "candidate_ast_hash_coverage": ledger.summary()["candidate_ast_hash_coverage"],
        "accepted_candidate_diff_coverage": ledger.summary()["accepted_candidate_diff_coverage"],
        "trace_parent_child_links_complete": ledger.summary()["trace_parent_child_links_complete"],
        "parent_child_link_coverage": ledger.summary()["parent_child_link_coverage"],
        "deterministic_cycle_detection_coverage": ledger.summary()["deterministic_cycle_detection_coverage"],
        "lineage_cycle_count": ledger.summary()["lineage_cycle_count"],
        "evaluator_hack_audit_coverage": ledger.summary()["evaluator_hack_audit_coverage"],
        "selected_incumbent_curve": [{
            "after_attempt": 1,
            "candidate_sha256": candidate_sha256(candidate),
            "hidden_test_normalized_quality": 0.0,
        }],
        "auc_attempt": 0.0,
        "native_a100_gpu_seconds_cap": 3600,
        "gpu_anytime_curve": [],
        "auc_gpu": None,
        "gpu_auc_status": "not_applicable",
        "terminal_state": "CLEAN_FALSIFICATION",
        "controller_mechanism_id": "TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V1",
        "controller_policy_sha256": "e" * 64,
        "controller_training_problem_ids": ["obp_dev_v1"],
        "controller_holdout_update_attempts": 0,
        "controller_actions": [{
            "generation": 1,
            "action": controller_action,
            "state": controller_state,
        }],
        "baseline_execution": {
            entry["name"]: {
                "source_commit": entry["source_commit"],
                "container_digest": baseline_container_digest,
            }
            for entry in registry["entries"] if entry["name"] in PEER_REQUIRED
        },
    }) + "\n")
    result_value = json.loads((bundle / "result.json").read_text())
    result_value["run_identity_sha256"] = result_identity_sha256(result_value)
    (bundle / "result.json").write_text(json.dumps(result_value, sort_keys=True) + "\n")
    evidence = {
        **{field: True for field in INTEGRITY_BOOLEAN_FLAGS},
        **{field: 0 for field in INTEGRITY_ZERO_FIELDS},
        **{field: False for field in (
            "same_model_superiority_ready", "final_quality_ready",
            "ood_generalization_ready", "compute_efficiency_ready",
            "primary_mechanism_validated", "replication_ready",
        )},
        "primary_and_required_extension_complete": True,
        "resource_budget_telemetry_complete": True,
        "post_unblinding_changes": 0,
        "q1_status": "clean_negative",
        "q2_status": "clean_negative",
        "q3_status": "clean_negative",
        "q4_status": "clean_negative",
        "primary_seed_ids": list(range(101, 113)),
        "extension_seed_ids": [],
        "primary_seed_count": 12,
        "extension_seed_count": 0,
        "extension_authorized": False,
    }
    (bundle / "evidence.json").write_text(json.dumps(evidence, sort_keys=True) + "\n")

    result_value = json.loads((bundle / "result.json").read_text())
    run_rows = []
    # Materialize one real event/result pair for every registered primary seed.
    # The verifier must not accept twelve matrix rows that all point at one
    # favorable artifact.
    for seed in range(102, 113):
        run_id = f"forge-v3-fixture/forge/h00/SMALL/{seed}"
        run_dir = bundle / "runs" / str(seed)
        run_dir.mkdir(parents=True, exist_ok=True)
        seed_ledger = EventLedger(
            run_dir / "events.jsonl",
            run_id=run_id,
            max_attempts=1,
            resource_budgets=fixture_budgets,
        )
        seed_attempt = seed_ledger.start_attempt(
            generation=1, slot=0, model="fixture-model", track="SAME_MODEL",
            metadata={"controller_action": controller_action},
        )
        seed_ledger.finish_attempt(
            seed_attempt,
            status="valid_candidate",
            candidate_hash=candidate_sha256(candidate),
            score=1.0,
            resource_usage=generation_usage(
                input_tokens=1,
                output_tokens=1,
                model_identity="fixture-model",
                sampling_profile={"temperature": 0.0},
                wall_time_ms=1.0,
            ),
            metadata={
                **lineage_metadata(candidate, []),
                "evaluator_hack_audit": {
                    "parseable": True,
                    "suspected_hack": False,
                    "findings": [],
                },
            },
        )
        seed_ledger.record_evaluation(
            seed_attempt,
            resource_usage=evaluator_usage(
                wall_time_ms=1.0,
                evaluator_cost=0.001,
                evaluator_id="fixture-evaluator",
            ),
        )
        seed_ledger.record_event("incumbent_selected", {
            "attempt_id": seed_attempt,
            "after_attempt": 1,
            "candidate_sha256": candidate_sha256(candidate),
            "score": 1.0,
        })
        seed_summary = seed_ledger.summary()
        seed_result = dict(result_value)
        seed_result.update({
            "run_id": run_id,
            "seed": seed,
            "run_identity_sha256": result_identity_sha256({**seed_result, "run_id": run_id, "seed": seed}),
            "event_ledger_head_hash": seed_summary["head_hash"],
            "decision_hash": replay_decision_hash(run_dir / "events.jsonl"),
            "result_recomputation_hash": replay_result_hash(run_dir / "events.jsonl"),
            "resource_summary": seed_summary["resource_summary"],
            "resource_ledger_hash": seed_summary["resource_ledger_hash"],
        })
        (run_dir / "result.json").write_text(
            json.dumps(seed_result, sort_keys=True) + "\n"
        )
    for seed in range(101, 113):
        events_path = bundle / ("events.jsonl" if seed == 101 else f"runs/{seed}/events.jsonl")
        result_path = bundle / ("result.json" if seed == 101 else f"runs/{seed}/result.json")
        run_rows.append({
            "run_id": f"forge-v3-fixture/forge/h00/SMALL/{seed}",
            "study_id": "forge-v3-fixture",
            "study_version": "f" * 64,
            "method_id": "FORGE",
            "problem_id": "h00",
            "problem_family": "family0",
            "distribution": "iid_heldout",
            "model_tier": "SMALL",
            "seed": seed,
            "seed_role": "primary",
            "track": "SAME_MODEL",
            "events_sha256": sha256_file(events_path),
            "result_sha256": sha256_file(result_path),
            "events_path": "events.jsonl" if seed == 101 else f"runs/{seed}/events.jsonl",
            "result_path": "result.json" if seed == 101 else f"runs/{seed}/result.json",
        })
    matrix = {
        "manifest_id": "FORGE_RUN_MATRIX_V3_1",
        "schema_version": 1,
        "status": "frozen",
        "frozen": True,
        "study_id": "forge-v3-fixture",
        "study_version": "f" * 64,
        "authority_attestation_sha256": "a" * 64,
        "primary_verifier_status": "negative",
        "primary_seed_ids": list(range(101, 113)),
        "extension_seed_ids": [],
        "extension_authorized": False,
        "primary_seed_count": 12,
        "extension_seed_count": 0,
        "runs": run_rows,
    }
    matrix["matrix_sha256"] = matrix_sha256(matrix)
    (bundle / "run_matrix.json").write_text(json.dumps(matrix, sort_keys=True) + "\n")

    refs.update({
        "events_sha256": sha256_file(bundle / "events.jsonl"),
        "result_sha256": sha256_file(bundle / "result.json"),
        "evidence_sha256": sha256_file(bundle / "evidence.json"),
        "run_matrix_sha256": sha256_file(bundle / "run_matrix.json"),
    })
    (bundle / "study_manifest.json").write_text(
        json.dumps(freeze_manifest(refs), sort_keys=True) + "\n"
    )


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_read_json_rejects_nonfinite_constants(tmp_path, constant):
    path = tmp_path / "asset.json"
    path.write_text('{"value": ' + constant + '}\n', encoding="utf-8")
    with pytest.raises(ProtocolError, match="cannot read JSON asset"):
        _read_json(path)


def test_hidden_event_scan_rejects_nonfinite_json(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"payload": {"score": NaN}}\n', encoding="utf-8")
    violations = _check_hidden_events(path)
    assert violations == ["event 1: invalid strict JSON (ValueError)"]


def test_missing_frozen_bundle_is_blocked_and_read_only(tmp_path):
    target = tmp_path / "does-not-exist"
    report = verify_bundle(target)
    assert report["terminal_state"] == "BLOCKED_INTEGRITY_FAILURE"
    assert report["research_finished"] is False
    assert report["checks"]["read_only"] is True
    assert not target.exists()


def test_verifier_does_not_follow_top_level_bundle_symlink(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}\n")
    (bundle / "protocol.json").symlink_to(outside)

    report = verify_bundle(bundle)

    assert report["terminal_state"] == "BLOCKED_INTEGRITY_FAILURE"
    assert report["research_finished"] is False
    assert any("must not be a symlink" in error for error in report["errors"])


def test_run_matrix_rejects_symlinked_registered_artifact(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _write_valid_clean_bundle(bundle)

    matrix_path = bundle / "run_matrix.json"
    matrix = json.loads(matrix_path.read_text())
    row = next(item for item in matrix["runs"] if item["seed"] == 102)
    alias = bundle / "runs" / "102" / "events-alias.jsonl"
    alias.symlink_to(Path("../../events.jsonl"))
    row["events_path"] = "runs/102/events-alias.jsonl"
    row["events_sha256"] = sha256_file(bundle / "events.jsonl")
    matrix["matrix_sha256"] = matrix_sha256(matrix)
    matrix_path.write_text(json.dumps(matrix, sort_keys=True) + "\n")

    study_path = bundle / "study_manifest.json"
    study = json.loads(study_path.read_text())
    study["run_matrix_sha256"] = sha256_file(matrix_path)
    study["manifest_sha256"] = manifest_sha256(study)
    study_path.write_text(json.dumps(study, sort_keys=True) + "\n")

    report = verify_bundle(bundle, require_clean_checkout=False)

    assert report["terminal_state"] == "BLOCKED_INTEGRITY_FAILURE"
    assert report["research_finished"] is False
    assert any("must not be a symlink" in error for error in report["errors"])


def test_run_matrix_rejects_hardlinked_registered_artifact(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _write_valid_clean_bundle(bundle)

    matrix_path = bundle / "run_matrix.json"
    matrix = json.loads(matrix_path.read_text())
    row = next(item for item in matrix["runs"] if item["seed"] == 102)
    hardlink = bundle / "runs" / "102" / "events-hardlink.jsonl"
    hardlink.hardlink_to(bundle / "events.jsonl")
    row["events_path"] = "runs/102/events-hardlink.jsonl"
    row["events_sha256"] = sha256_file(bundle / "events.jsonl")
    matrix["matrix_sha256"] = matrix_sha256(matrix)
    matrix_path.write_text(json.dumps(matrix, sort_keys=True) + "\n")

    study_path = bundle / "study_manifest.json"
    study = json.loads(study_path.read_text())
    study["run_matrix_sha256"] = sha256_file(matrix_path)
    study["manifest_sha256"] = manifest_sha256(study)
    study_path.write_text(json.dumps(study, sort_keys=True) + "\n")

    report = verify_bundle(bundle, require_clean_checkout=False)

    assert report["terminal_state"] == "BLOCKED_INTEGRITY_FAILURE"
    assert report["research_finished"] is False
    assert any("inode is aliased" in error for error in report["errors"])


def test_public_verifier_cli_is_read_only_and_nonzero_for_missing_bundle(tmp_path):
    target = tmp_path / "missing"
    proc = subprocess.run(
        ["python3", "tools/verify_v3_research.py", str(target)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 2
    assert '"research_finished": false' in proc.stdout
    assert not target.exists()


def test_incomplete_bundle_does_not_turn_into_inconclusive_or_positive(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "evidence.json").write_text(json.dumps({}), encoding="utf-8")
    report = verify_bundle(bundle, require_clean_checkout=False)
    assert report["terminal_state"] == "BLOCKED_INTEGRITY_FAILURE"
    assert report["research_finished"] is False
    assert report["errors"]


def test_verifier_does_not_modify_bundle_files(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    marker = bundle / "marker.txt"
    marker.write_text("immutable", encoding="utf-8")
    before = marker.read_bytes()
    verify_bundle(bundle)
    assert marker.read_bytes() == before


def test_repository_only_bundle_cannot_terminate_without_external_receipt(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _write_valid_clean_bundle(bundle)
    report = verify_bundle(bundle, require_clean_checkout=False)
    assert report["terminal_state"] == "BLOCKED_INTEGRITY_FAILURE", report
    assert report["research_finished"] is False
    assert report["checks"]["external_verifier_receipt_valid"] is False
    assert report["checks"]["controller_provenance_valid"] is True
    assert any("external read-only verifier receipt" in error for error in report["errors"])


def test_registered_result_requires_controller_action_and_state_provenance(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _write_valid_clean_bundle(bundle)
    result_path = bundle / "result.json"
    result = json.loads(result_path.read_text())
    result.pop("controller_actions")
    result_path.write_text(json.dumps(result, sort_keys=True) + "\n")
    matrix_path = bundle / "run_matrix.json"
    matrix = json.loads(matrix_path.read_text())
    matrix["runs"][0]["result_sha256"] = sha256_file(result_path)
    matrix["matrix_sha256"] = matrix_sha256(matrix)
    matrix_path.write_text(json.dumps(matrix, sort_keys=True) + "\n")
    study_path = bundle / "study_manifest.json"
    study = json.loads(study_path.read_text())
    study["result_sha256"] = sha256_file(result_path)
    study["run_matrix_sha256"] = sha256_file(matrix_path)
    study_path.write_text(json.dumps(freeze_manifest(study), sort_keys=True) + "\n")
    report = verify_bundle(bundle, require_clean_checkout=False)
    assert report["terminal_state"] == "BLOCKED_INTEGRITY_FAILURE"
    assert any("controller actions are missing" in error for error in report["errors"])


def test_registered_runs_with_unequal_evaluator_budgets_are_blocked(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _write_valid_clean_bundle(bundle)

    # Rebuild one registered artifact with a different frozen evaluator-call
    # limit, keeping its replay/result/hash bindings internally consistent.
    seed = 102
    run_id = f"forge-v3-fixture/forge/h00/SMALL/{seed}"
    run_dir = bundle / "runs" / str(seed)
    events_path = run_dir / "events.jsonl"
    events_path.unlink()
    budgets = {
        "generation": {"records": 1},
        "evaluator": {"calls": 511},
    }
    ledger = EventLedger(
        events_path,
        run_id=run_id,
        max_attempts=1,
        resource_budgets=budgets,
    )
    candidate = "candidate"
    attempt_id = ledger.start_attempt(
        generation=1,
        slot=0,
        model="fixture-model",
        track="SAME_MODEL",
        metadata={
            "controller_action": {
                "generator_model": "fixture-model",
                "parent_selection_policy": "elite",
                "mutation_operator": "local",
                "number_of_offspring": 1,
                "reflection_depth": 0,
                "archive_sampling_policy": "uniform",
            }
        },
    )
    ledger.finish_attempt(
        attempt_id,
        status="valid_candidate",
        candidate_hash=candidate_sha256(candidate),
        score=1.0,
        resource_usage=generation_usage(
            input_tokens=1,
            output_tokens=1,
            model_identity="fixture-model",
            sampling_profile={"temperature": 0.0},
            wall_time_ms=1.0,
        ),
        metadata={
            **lineage_metadata(candidate, []),
            "evaluator_hack_audit": {
                "parseable": True,
                "suspected_hack": False,
                "findings": [],
            },
        },
    )
    ledger.record_evaluation(
        attempt_id,
        resource_usage=evaluator_usage(
            wall_time_ms=1.0,
            evaluator_cost=0.001,
            evaluator_id="fixture-evaluator",
        ),
    )
    ledger.record_event("incumbent_selected", {
        "attempt_id": attempt_id,
        "after_attempt": 1,
        "candidate_sha256": candidate_sha256(candidate),
        "score": 1.0,
    })
    summary = ledger.summary()
    result = json.loads((bundle / "result.json").read_text())
    result.update({
        "run_id": run_id,
        "seed": seed,
        "run_identity_sha256": result_identity_sha256({**result, "run_id": run_id, "seed": seed}),
        "event_ledger_head_hash": summary["head_hash"],
        "decision_hash": replay_decision_hash(events_path),
        "result_recomputation_hash": replay_result_hash(events_path),
        "resource_summary": summary["resource_summary"],
        "resource_ledger_hash": summary["resource_ledger_hash"],
    })
    (run_dir / "result.json").write_text(json.dumps(result, sort_keys=True) + "\n")

    matrix = json.loads((bundle / "run_matrix.json").read_text())
    row = next(row for row in matrix["runs"] if row["seed"] == seed)
    row["events_sha256"] = sha256_file(events_path)
    row["result_sha256"] = sha256_file(run_dir / "result.json")
    matrix["matrix_sha256"] = matrix_sha256(matrix)
    (bundle / "run_matrix.json").write_text(json.dumps(matrix, sort_keys=True) + "\n")
    study = json.loads((bundle / "study_manifest.json").read_text())
    study["run_matrix_sha256"] = sha256_file(bundle / "run_matrix.json")
    (bundle / "study_manifest.json").write_text(
        json.dumps(freeze_manifest(study), sort_keys=True) + "\n"
    )

    report = verify_bundle(bundle, require_clean_checkout=False)
    assert report["terminal_state"] == "BLOCKED_INTEGRITY_FAILURE", report
    assert report["research_finished"] is False
    assert any("same evaluator budget" in error for error in report["errors"])


def test_external_receipt_does_not_mask_incomplete_attempt_curve(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _write_valid_clean_bundle(bundle)
    study = json.loads((bundle / "study_manifest.json").read_text())
    result = json.loads((bundle / "result.json").read_text())
    receipt = {
        "receipt_type": "external_read_only_verifier",
        "authority_id": study["external_authority_id"],
        "study_manifest_sha256": _study_binding_hash(study),
        "terminal_state": result["terminal_state"],
        "verifier_id": "external-verifier-fixture",
        "verifier_version": "v3-fixture",
        "read_only": True,
        "signature": "fixture-signature",
    }
    (bundle / "external_verifier_receipt.json").write_text(
        json.dumps(receipt, sort_keys=True) + "\n"
    )
    study["external_verifier_receipt_sha256"] = sha256_file(
        bundle / "external_verifier_receipt.json"
    )
    (bundle / "study_manifest.json").write_text(
        json.dumps(freeze_manifest(study), sort_keys=True) + "\n"
    )
    report = verify_bundle(bundle, require_clean_checkout=False)
    assert report["terminal_state"] == "BLOCKED_INTEGRITY_FAILURE", report
    assert report["research_finished"] is False
    assert any("one incumbent checkpoint" in error for error in report["errors"])


def test_run_matrix_seed_coverage_is_fail_closed(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _write_valid_clean_bundle(bundle)
    matrix = json.loads((bundle / "run_matrix.json").read_text())
    matrix["primary_seed_ids"] = list(range(101, 112))
    matrix["matrix_sha256"] = matrix_sha256(matrix)
    (bundle / "run_matrix.json").write_text(json.dumps(matrix, sort_keys=True) + "\n")
    study = json.loads((bundle / "study_manifest.json").read_text())
    study["run_matrix_sha256"] = sha256_file(bundle / "run_matrix.json")
    (bundle / "study_manifest.json").write_text(
        json.dumps(freeze_manifest(study), sort_keys=True) + "\n"
    )
    report = verify_bundle(bundle, require_clean_checkout=False)
    assert report["terminal_state"] == "BLOCKED_INTEGRITY_FAILURE"
    assert any("run matrix" in error for error in report["errors"])


def test_run_matrix_extension_requires_external_extend_status(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _write_valid_clean_bundle(bundle)
    matrix = json.loads((bundle / "run_matrix.json").read_text())
    matrix["primary_verifier_status"] = "extend"
    matrix["matrix_sha256"] = matrix_sha256(matrix)
    (bundle / "run_matrix.json").write_text(json.dumps(matrix, sort_keys=True) + "\n")
    study = json.loads((bundle / "study_manifest.json").read_text())
    study["run_matrix_sha256"] = sha256_file(bundle / "run_matrix.json")
    (bundle / "study_manifest.json").write_text(
        json.dumps(freeze_manifest(study), sort_keys=True) + "\n"
    )
    report = verify_bundle(bundle, require_clean_checkout=False)
    assert report["terminal_state"] == "BLOCKED_INTEGRITY_FAILURE"
    assert any("extension authorization" in error for error in report["errors"])


def test_run_matrix_rejects_unregistered_extension_rows(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _write_valid_clean_bundle(bundle)
    matrix = json.loads((bundle / "run_matrix.json").read_text())
    row = dict(matrix["runs"][0])
    row["run_id"] = row["run_id"] + "/extension"
    row["seed"] = 113
    row["seed_role"] = "extension"
    row["events_path"] = "runs/extension/events.jsonl"
    row["result_path"] = "runs/extension/result.json"
    extension_dir = bundle / "runs" / "extension"
    extension_dir.mkdir(parents=True)
    shutil.copy2(bundle / "events.jsonl", extension_dir / "events.jsonl")
    shutil.copy2(bundle / "result.json", extension_dir / "result.json")
    matrix["runs"].append(row)
    matrix["matrix_sha256"] = matrix_sha256(matrix)
    (bundle / "run_matrix.json").write_text(json.dumps(matrix, sort_keys=True) + "\n")
    study = json.loads((bundle / "study_manifest.json").read_text())
    study["run_matrix_sha256"] = sha256_file(bundle / "run_matrix.json")
    (bundle / "study_manifest.json").write_text(
        json.dumps(freeze_manifest(study), sort_keys=True) + "\n"
    )
    report = verify_bundle(bundle, require_clean_checkout=False)
    assert report["terminal_state"] == "BLOCKED_INTEGRITY_FAILURE"
    assert any("extension rows without authorization" in error for error in report["errors"])


def test_evidence_seed_claims_must_match_frozen_run_matrix(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _write_valid_clean_bundle(bundle)
    evidence = json.loads((bundle / "evidence.json").read_text())
    evidence["primary_seed_ids"] = list(range(102, 114))
    (bundle / "evidence.json").write_text(json.dumps(evidence, sort_keys=True) + "\n")
    study = json.loads((bundle / "study_manifest.json").read_text())
    study["evidence_sha256"] = sha256_file(bundle / "evidence.json")
    (bundle / "study_manifest.json").write_text(
        json.dumps(freeze_manifest(study), sort_keys=True) + "\n"
    )
    report = verify_bundle(bundle, require_clean_checkout=False)
    assert report["terminal_state"] == "BLOCKED_INTEGRITY_FAILURE"
    assert any("primary_seed_ids differs" in error for error in report["errors"])


def test_registered_run_artifact_missing_or_tampered_is_fail_closed(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _write_valid_clean_bundle(bundle)
    missing = bundle / "runs" / "102" / "result.json"
    missing.unlink()
    report = verify_bundle(bundle, require_clean_checkout=False)
    assert report["terminal_state"] == "BLOCKED_INTEGRITY_FAILURE"
    assert report["checks"]["run_matrix_valid"] is False
    assert any("artifact is missing" in error for error in report["errors"])

    bundle = tmp_path / "tampered"
    bundle.mkdir()
    _write_valid_clean_bundle(bundle)
    target = bundle / "runs" / "103" / "result.json"
    target.write_text(target.read_text() + "\n", encoding="utf-8")
    report = verify_bundle(bundle, require_clean_checkout=False)
    assert report["terminal_state"] == "BLOCKED_INTEGRITY_FAILURE"
    assert any("artifact hash mismatch" in error for error in report["errors"])


def test_native_result_telemetry_must_match_resource_ledger():
    result = {
        "track": "NATIVE_COMPUTE",
        "native_gpu_seconds_observed": 12.0,
        "native_model_forward_time_ms_observed": 34.0,
        "gpu_anytime_curve": [{"observed_gpu_seconds": 12.0}],
    }
    replay = {
        "resource_summary": {
            "phases": {
                "generation": {
                    "totals": {
                        "gpu_seconds": 12.0,
                        "model_forward_time_ms": 34.0,
                    }
                }
            }
        }
    }
    assert _validate_native_resource_consistency(result, replay) == []
    result["native_gpu_seconds_observed"] = 11.0
    assert _validate_native_resource_consistency(result, replay)


def test_mutating_a_frozen_asset_blocks_the_same_bundle(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _write_valid_clean_bundle(bundle)
    (bundle / "task_manifest.json").write_text(
        (bundle / "task_manifest.json").read_text() + "\n",
        encoding="utf-8",
    )
    report = verify_bundle(bundle, require_clean_checkout=False)
    assert report["terminal_state"] == "BLOCKED_INTEGRITY_FAILURE"
    assert report["research_finished"] is False
    assert any("task_manifests_sha256" in error for error in report["errors"])


def test_unresolved_hashed_runtime_asset_blocks_bundle(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _write_valid_clean_bundle(bundle)
    (bundle / "evaluator_manifest.json").write_text(
        json.dumps({"runtime_digest": None}) + "\n", encoding="utf-8"
    )
    report = verify_bundle(bundle, require_clean_checkout=False)
    assert report["terminal_state"] == "BLOCKED_INTEGRITY_FAILURE"
    assert any("evaluator_manifest.json" in error for error in report["errors"])


def test_missing_external_authority_or_holdout_locator_blocks_bundle(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _write_valid_clean_bundle(bundle)
    study = json.loads((bundle / "study_manifest.json").read_text())
    study.pop("external_authority_id")
    # Recompute the self-hash so this test reaches the study-semantic gate,
    # rather than stopping at a generic hash mismatch.
    study = freeze_manifest(study)
    (bundle / "study_manifest.json").write_text(json.dumps(study) + "\n")
    report = verify_bundle(bundle, require_clean_checkout=False)
    assert report["terminal_state"] == "BLOCKED_INTEGRITY_FAILURE"
    assert any("external_authority_id" in error for error in report["errors"])


def test_missing_result_decision_hash_blocks_bundle(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _write_valid_clean_bundle(bundle)
    result = json.loads((bundle / "result.json").read_text())
    result.pop("decision_hash")
    (bundle / "result.json").write_text(json.dumps(result) + "\n")
    report = verify_bundle(bundle, require_clean_checkout=False)
    assert report["terminal_state"] == "BLOCKED_INTEGRITY_FAILURE"
    assert any("decision hash" in error for error in report["errors"])


def test_bootstrap_ci_mismatch_blocks_even_when_asset_hash_is_updated(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _write_valid_clean_bundle(bundle)
    metrics = json.loads((bundle / "metrics_summary.json").read_text())
    metrics["overall_delta_oracle_95ci_high"] = 0.5
    (bundle / "metrics_summary.json").write_text(json.dumps(metrics, sort_keys=True) + "\n")
    study = json.loads((bundle / "study_manifest.json").read_text())
    study["metrics_summary_sha256"] = sha256_file(bundle / "metrics_summary.json")
    (bundle / "study_manifest.json").write_text(
        json.dumps(freeze_manifest(study), sort_keys=True) + "\n"
    )
    report = verify_bundle(bundle, require_clean_checkout=False)
    assert report["terminal_state"] == "BLOCKED_INTEGRITY_FAILURE"
    assert any("bootstrap CI high mismatch" in error for error in report["errors"])


def test_positive_gate_boolean_without_numeric_attestations_blocks_bundle(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _write_valid_clean_bundle(bundle)
    metrics = json.loads((bundle / "metrics_summary.json").read_text())
    metrics["same_model_superiority_ready"] = True
    (bundle / "metrics_summary.json").write_text(json.dumps(metrics, sort_keys=True) + "\n")
    study = json.loads((bundle / "study_manifest.json").read_text())
    study["metrics_summary_sha256"] = sha256_file(bundle / "metrics_summary.json")
    (bundle / "study_manifest.json").write_text(
        json.dumps(freeze_manifest(study), sort_keys=True) + "\n"
    )
    report = verify_bundle(bundle, require_clean_checkout=False)
    assert report["terminal_state"] == "BLOCKED_INTEGRITY_FAILURE"
    assert any("positive metric gate requires finite value" in error for error in report["errors"])


def test_baseline_execution_identity_mismatch_blocks_bundle(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _write_valid_clean_bundle(bundle)
    result = json.loads((bundle / "result.json").read_text())
    result["baseline_execution"][PEER_REQUIRED[0]]["source_commit"] = "e" * 40
    (bundle / "result.json").write_text(json.dumps(result, sort_keys=True) + "\n")
    report = verify_bundle(bundle, require_clean_checkout=False)
    assert report["terminal_state"] == "BLOCKED_INTEGRITY_FAILURE"
    assert any("baseline source commit mismatch" in error for error in report["errors"])


def test_raw_bootstrap_recomputation_mismatch_blocks_bundle(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _write_valid_clean_bundle(bundle)
    metrics = json.loads((bundle / "metrics_summary.json").read_text())
    metrics["bootstrap_samples"]["overall_delta_oracle"][0] = 1.0
    (bundle / "metrics_summary.json").write_text(json.dumps(metrics, sort_keys=True) + "\n")
    study = json.loads((bundle / "study_manifest.json").read_text())
    study["metrics_summary_sha256"] = sha256_file(bundle / "metrics_summary.json")
    (bundle / "study_manifest.json").write_text(
        json.dumps(freeze_manifest(study), sort_keys=True) + "\n"
    )
    report = verify_bundle(bundle, require_clean_checkout=False)
    assert report["terminal_state"] == "BLOCKED_INTEGRITY_FAILURE"
    assert any("bootstrap raw recomputation mismatch" in error for error in report["errors"])


def test_selected_incumbent_curve_mismatch_blocks_bundle(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _write_valid_clean_bundle(bundle)
    result = json.loads((bundle / "result.json").read_text())
    result["selected_incumbent_curve"][0]["hidden_test_normalized_quality"] = 1.0
    (bundle / "result.json").write_text(json.dumps(result, sort_keys=True) + "\n")
    study = json.loads((bundle / "study_manifest.json").read_text())
    study["result_sha256"] = sha256_file(bundle / "result.json")
    (bundle / "study_manifest.json").write_text(
        json.dumps(freeze_manifest(study), sort_keys=True) + "\n"
    )
    report = verify_bundle(bundle, require_clean_checkout=False)
    assert report["terminal_state"] == "BLOCKED_INTEGRITY_FAILURE"
    assert any("auc_attempt" in error for error in report["errors"])


def test_saved_terminal_state_mismatch_blocks_bundle(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _write_valid_clean_bundle(bundle)
    result = json.loads((bundle / "result.json").read_text())
    result["terminal_state"] = "STRONG_POSITIVE"
    (bundle / "result.json").write_text(json.dumps(result, sort_keys=True) + "\n")
    study = json.loads((bundle / "study_manifest.json").read_text())
    study["result_sha256"] = sha256_file(bundle / "result.json")
    (bundle / "study_manifest.json").write_text(
        json.dumps(freeze_manifest(study), sort_keys=True) + "\n"
    )
    report = verify_bundle(bundle, require_clean_checkout=False)
    assert report["terminal_state"] == "BLOCKED_INTEGRITY_FAILURE"
    assert any("terminal_state differs" in error for error in report["errors"])


def test_extra_baseline_execution_identity_blocks_bundle(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _write_valid_clean_bundle(bundle)
    result = json.loads((bundle / "result.json").read_text())
    result["baseline_execution"]["unexpected"] = {
        "source_commit": "a" * 40,
        "container_digest": "sha256:" + "d" * 64,
    }
    (bundle / "result.json").write_text(json.dumps(result, sort_keys=True) + "\n")
    study = json.loads((bundle / "study_manifest.json").read_text())
    study["result_sha256"] = sha256_file(bundle / "result.json")
    (bundle / "study_manifest.json").write_text(
        json.dumps(freeze_manifest(study), sort_keys=True) + "\n"
    )
    report = verify_bundle(bundle, require_clean_checkout=False)
    assert report["terminal_state"] == "BLOCKED_INTEGRITY_FAILURE"
    assert any("do not exactly match" in error for error in report["errors"])


def test_controller_provenance_is_required_and_pinned(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _write_valid_clean_bundle(bundle)
    result = json.loads((bundle / "result.json").read_text())
    result["controller_policy_sha256"] = "not-pinned"
    (bundle / "result.json").write_text(json.dumps(result, sort_keys=True) + "\n")
    study = json.loads((bundle / "study_manifest.json").read_text())
    study["result_sha256"] = sha256_file(bundle / "result.json")
    (bundle / "study_manifest.json").write_text(
        json.dumps(freeze_manifest(study), sort_keys=True) + "\n"
    )
    report = verify_bundle(bundle, require_clean_checkout=False)
    assert report["terminal_state"] == "BLOCKED_INTEGRITY_FAILURE"
    assert any("controller policy hash" in error for error in report["errors"])


def test_controller_training_ids_must_be_development_tasks(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _write_valid_clean_bundle(bundle)
    result = json.loads((bundle / "result.json").read_text())
    result["controller_training_problem_ids"] = ["h00"]
    (bundle / "result.json").write_text(json.dumps(result, sort_keys=True) + "\n")
    study = json.loads((bundle / "study_manifest.json").read_text())
    study["result_sha256"] = sha256_file(bundle / "result.json")
    (bundle / "study_manifest.json").write_text(
        json.dumps(freeze_manifest(study), sort_keys=True) + "\n"
    )
    report = verify_bundle(bundle, require_clean_checkout=False)
    assert report["terminal_state"] == "BLOCKED_INTEGRITY_FAILURE"
    assert any("non-development tasks" in error for error in report["errors"])


def test_mixed_or_unknown_tracks_have_no_attempt_cap():
    assert _track_attempt_cap({"SAME_MODEL"}) == 512
    assert _track_attempt_cap({"NATIVE_COMPUTE"}) == 2048
    assert _track_attempt_cap({"SAME_MODEL", "NATIVE_COMPUTE"}) is None
    assert _track_attempt_cap({"UNKNOWN"}) is None
