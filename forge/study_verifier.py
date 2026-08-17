"""Read-only verifier for a frozen Forge Research V3 study bundle.

The repository cannot act as the external authority described by the V3
criterion.  This module therefore verifies a bundle supplied by that
authority, without writing to it and without consulting a candidate evaluator
or an LLM.  Missing assets, hash mismatches, unresolved baselines, malformed
ledgers, or incomplete evidence are integrity failures rather than zeros.

Expected bundle layout::

    protocol.json
    study_manifest.json
    model_manifest.json
    task_manifest.json
    baseline_registry.json
    evaluator_manifest.json
    container_manifest.json
    prompt_and_decoding_manifest.json
    run_matrix.json
    external_verifier_receipt.json
    events.jsonl
    result.json
    evidence.json
"""
from __future__ import annotations

import json
import math
import re
import statistics
import subprocess
from pathlib import Path
from typing import Any, Mapping

from .baselines import load_registry, primary_baselines
from .ledger import LedgerError
from .manifest import validate_frozen_asset_manifest, validate_frozen_study_manifest
from .models import validate_model_manifest
from .protocol import (
    PROTOCOL_PATH,
    ProtocolError,
    RESEARCH_TERMINAL_STATES,
    V3_NATIVE_ATTEMPT_CAP,
    V3_SAME_MODEL_ATTEMPT_CAP,
    canonical_json,
    load_protocol,
    protocol_hash,
    strict_json_loads,
)
from .replay import replay_decision_records, replay_result_records, replay_summary
from .result_schema import validate_result_schema
from .study_matrix import _artifact_relpath, validate_run_matrix
from .research_metrics import (
    MetricError,
    hierarchical_bootstrap,
    ood_delta_statistic,
    oracle_delta_statistic,
    percentile_interval,
)
from .tasks import TaskManifestError, search_visible_manifest, validate_task_manifest
from .verdict import (
    INTEGRITY_BOOLEAN_FLAGS,
    INTEGRITY_ZERO_FIELDS,
    STRONG_BOOLEAN_GATES,
    derive_q_statuses,
    final_verdict,
    integrity_ready,
    validate_metric_gate_claims,
)


ASSET_FILES = {
    "protocol_sha256": "protocol.json",
    "model_manifests_sha256": "model_manifest.json",
    "task_manifests_sha256": "task_manifest.json",
    "baseline_registry_sha256": "baseline_registry.json",
    "evaluator_manifests_sha256": "evaluator_manifest.json",
    "container_image_digests_sha256": "container_manifest.json",
    "prompt_and_decoding_profiles_sha256": "prompt_and_decoding_manifest.json",
    "metrics_summary_sha256": "metrics_summary.json",
    "run_matrix_sha256": "run_matrix.json",
}
RESULT_ASSET_FILES = {
    "events_sha256": "events.jsonl",
    "result_sha256": "result.json",
    "evidence_sha256": "evidence.json",
}
EXTERNAL_RECEIPT_FILE = "external_verifier_receipt.json"
REQUIRED_FILES = (*ASSET_FILES.values(), "study_manifest.json", "events.jsonl",
                  "result.json", "evidence.json")

_DENIED_EVENT_TYPES = frozenset({
    "hidden_test_access",
    "hidden_test_feedback",
    "hidden_test_score",
    "hidden_test_path",
    "search_hidden_access",
})
_DENIED_PAYLOAD_KEYS = frozenset({
    "hidden_test_content",
    "hidden_test_path",
    "hidden_test_score",
    "hidden_score",
    "secret",
    "side_channel",
})

_LINEAGE_COVERAGE_FIELDS = (
    "candidate_ast_hash_coverage",
    "accepted_candidate_diff_coverage",
    "trace_parent_child_links_complete",
    "parent_child_link_coverage",
    "deterministic_cycle_detection_coverage",
    "lineage_cycle_count",
    "evaluator_hack_audit_coverage",
)

_BOOTSTRAP_REPLICATES = 20_000
_BOOTSTRAP_SEED = 2_026_080_901
_BOOTSTRAP_HIERARCHY = (
    "problem_family", "problem", "seed", "hidden_test_instance_cluster"
)
_BOOTSTRAP_FIELDS = {
    "overall_delta_oracle": "overall_delta_oracle_95ci_high",
    "overall_delta_gpu_oracle": "overall_delta_gpu_oracle_95ci_high",
    "delta_fixed": "delta_fixed_95ci_high",
    "delta_transfer": "delta_transfer_95ci_high",
    "delta_cost": "delta_cost_95ci_high",
    "overall_delta_ood": "overall_delta_ood_95ci_high",
}


def _named_delta_statistic(field: str):
    """Build a finite Forge-minus-comparator statistic for raw bootstrap rows."""
    def statistic(rows: list[Mapping[str, Any]]) -> float:
        if not rows:
            raise MetricError(f"empty bootstrap rows for {field}")
        deltas = []
        for row in rows:
            if field not in row or "forge" not in row:
                raise MetricError(f"bootstrap row needs forge and {field}")
            forge = row["forge"]
            comparator = row[field]
            if (
                isinstance(forge, bool)
                or not isinstance(forge, (int, float))
                or not math.isfinite(float(forge))
                or isinstance(comparator, bool)
                or not isinstance(comparator, (int, float))
                or not math.isfinite(float(comparator))
            ):
                raise MetricError(f"bootstrap row has non-finite forge/{field}")
            deltas.append(float(forge) - float(comparator))
        return statistics.fmean(deltas)
    return statistic


_BOOTSTRAP_STATISTICS = {
    "overall_delta_oracle": oracle_delta_statistic,
    "overall_delta_gpu_oracle": oracle_delta_statistic,
    "delta_fixed": _named_delta_statistic("fixed_champion"),
    "delta_transfer": _named_delta_statistic("no_transfer"),
    "delta_cost": _named_delta_statistic("cost_unaware"),
    "overall_delta_ood": ood_delta_statistic,
}
_CONTAINER_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CONTROLLER_MECHANISMS = frozenset({
    "TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V1",
    "TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V2",
    "FIXED_DEV_BEST",
    "NO_TRANSFER_PRIOR",
    "COST_UNAWARE_CONTROLLER",
})
_CONTROLLER_ACTION_FIELDS = frozenset({
    "generator_model", "parent_selection_policy", "mutation_operator",
    "number_of_offspring", "reflection_depth", "archive_sampling_policy",
})
_CONTROLLER_STATE_FIELDS = frozenset({
    "remaining_budget", "improvement_slope", "time_since_last_improvement",
    "archive_behavioral_entropy", "archive_score_dispersion",
    "candidate_invalid_rate", "duplicate_rate", "parent_lineage_depth",
    "recent_operator_success", "recent_model_success", "estimated_generation_cost",
})
_UNRESOLVED_CONTROLLER_MODELS = frozenset({
    "", "latest", "default", "main", "master", "floating", "unresolved",
    "draft", "unpinned", "small", "medium", "strong",
})
_TRACK_ATTEMPT_CAPS = {
    "SAME_MODEL": V3_SAME_MODEL_ATTEMPT_CAP,
    "NATIVE_COMPUTE": V3_NATIVE_ATTEMPT_CAP,
}


def _track_attempt_cap(tracks: set[str]) -> int | None:
    """Return a cap only for one valid, unambiguous run track."""
    if len(tracks) != 1:
        return None
    return _TRACK_ATTEMPT_CAPS.get(next(iter(tracks)))


def _read_json(path: Path) -> Any:
    def reject_nonfinite_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON constant is not permitted: {value}")

    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=reject_nonfinite_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProtocolError(f"cannot read JSON asset: {path.name}") from exc


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_bundle_file(root: Path, name: str) -> Path:
    """Resolve a top-level bundle file without following an escape symlink."""
    if root.is_symlink():
        raise ProtocolError("bundle root must not be a symlink")
    relative = Path(name)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ProtocolError(f"bundle asset path is not a safe relative name: {name}")
    lexical = root / relative
    if lexical.is_symlink():
        raise ProtocolError(f"bundle asset must not be a symlink: {name}")
    resolved_root = root.resolve()
    resolved = lexical.resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ProtocolError(f"bundle asset escapes bundle root: {name}") from exc
    return resolved


def _study_binding_hash(study: Mapping[str, Any]) -> str:
    """Hash study identity without the receipt hash/self-hash recursion."""
    payload = {
        key: value for key, value in study.items()
        if key not in {"manifest_sha256", "external_verifier_receipt_sha256"}
    }
    import hashlib

    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _git_head(root: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = proc.stdout.strip()
    return value if proc.returncode == 0 and value else None


def _git_clean(root: Path) -> bool | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.returncode == 0 and not proc.stdout.strip()


def _nested_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            keys.add(str(key))
            keys.update(_nested_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_nested_keys(child))
    return keys


def _check_hidden_events(path: Path) -> list[str]:
    """Return explicit hidden-test feedback/access violations in a ledger."""
    violations: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        return [f"cannot scan event ledger: {type(exc).__name__}"]
    for line_no, line in enumerate(lines, 1):
        try:
            event = strict_json_loads(line)
        except (json.JSONDecodeError, ValueError) as exc:
            violations.append(
                f"event {line_no}: invalid strict JSON ({type(exc).__name__})"
            )
            continue  # replay remains authoritative for full ledger validity
        event_type = str(event.get("event_type", ""))
        if event_type in _DENIED_EVENT_TYPES:
            violations.append(f"event {line_no}: denied event type {event_type}")
        denied = sorted(_DENIED_PAYLOAD_KEYS & _nested_keys(event.get("payload", {})))
        violations.extend(f"event {line_no}: denied payload key {key}" for key in denied)
    return violations


def _validate_external_receipt(
    root: Path,
    study: Mapping[str, Any] | None,
    result: Mapping[str, Any] | None,
) -> list[str]:
    """Require an out-of-repository verifier receipt before scientific finish.

    The repository verifier can validate the receipt's binding and shape, but
    cannot authenticate an external authority's signature.  That final trust
    boundary is intentionally explicit rather than silently treating a local
    mock bundle as a registered study.
    """
    errors: list[str] = []
    try:
        path = _safe_bundle_file(root, EXTERNAL_RECEIPT_FILE)
    except ProtocolError as exc:
        return [f"external verifier receipt: {exc}"]
    if not path.is_file():
        return ["external read-only verifier receipt is missing"]
    try:
        receipt = _read_json(path)
    except ProtocolError as exc:
        return [f"external verifier receipt: {exc}"]
    if not isinstance(receipt, Mapping):
        return ["external verifier receipt is not an object"]
    if receipt.get("receipt_type") != "external_read_only_verifier":
        errors.append("external verifier receipt type is invalid")
    if not isinstance(study, Mapping):
        errors.append("external verifier receipt cannot bind missing study manifest")
    else:
        if receipt.get("authority_id") != study.get("external_authority_id"):
            errors.append("external verifier receipt authority does not match study")
        if receipt.get("study_manifest_sha256") != _study_binding_hash(study):
            errors.append("external verifier receipt is not bound to study manifest")
        expected_hash = study.get("external_verifier_receipt_sha256")
        actual_hash = _sha256(path)
        if not isinstance(expected_hash, str) or expected_hash != actual_hash:
            errors.append("external verifier receipt hash is missing or mismatched")
    if not isinstance(result, Mapping) or receipt.get("terminal_state") != result.get("terminal_state"):
        errors.append("external verifier receipt terminal state does not match result")
    for field in ("verifier_id", "verifier_version", "signature"):
        value = receipt.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"external verifier receipt missing {field}")
    if receipt.get("read_only") is not True:
        errors.append("external verifier receipt is not marked read_only")
    return errors


def _required_evidence_fields() -> set[str]:
    return {
        *INTEGRITY_BOOLEAN_FLAGS,
        *INTEGRITY_ZERO_FIELDS,
        *STRONG_BOOLEAN_GATES,
        "primary_and_required_extension_complete",
        "post_unblinding_changes",
        "q1_status",
        "q2_status",
        "q3_status",
        "q4_status",
        "primary_seed_ids",
        "extension_seed_ids",
        "primary_seed_count",
        "extension_seed_count",
        "extension_authorized",
    }


def _validate_seed_completeness(
    evidence: Mapping[str, Any], protocol_spec: Mapping[str, Any] | None
) -> list[str]:
    """Require the preregistered primary/extension seed sets exactly.

    A count alone is insufficient: a duplicated favorable seed could otherwise
    masquerade as a complete phase.  The verifier therefore checks exact IDs,
    role counts, and the extension authorization bit independently of any
    reported effect size.
    """
    errors: list[str] = []
    if not isinstance(protocol_spec, Mapping):
        return ["seed completeness cannot be checked without protocol"]
    seeds = protocol_spec.get("seeds")
    if not isinstance(seeds, Mapping):
        return ["protocol seeds are missing"]
    primary = seeds.get("primary")
    extension = seeds.get("extension")
    if (
        not isinstance(primary, list) or any(isinstance(value, bool) or not isinstance(value, int) for value in primary)
        or not isinstance(extension, list)
        or any(isinstance(value, bool) or not isinstance(value, int) for value in extension)
    ):
        return ["protocol seed sets are malformed"]
    observed_primary = evidence.get("primary_seed_ids")
    observed_extension = evidence.get("extension_seed_ids")
    for name, observed, expected in (
        ("primary", observed_primary, primary),
        ("extension", observed_extension, extension),
    ):
        if not isinstance(observed, list) or any(
            isinstance(value, bool) or not isinstance(value, int) for value in observed
        ):
            errors.append(f"{name} seed IDs are missing or malformed")
            continue
        if observed != sorted(set(observed)):
            errors.append(f"{name} seed IDs are not sorted and unique")
        if name == "primary" and observed != primary:
            errors.append("primary seed IDs do not equal the preregistered set")
        if name == "extension" and observed not in ([], extension):
            errors.append("extension seed IDs must be empty or equal the preregistered set")
        count_field = f"{name}_seed_count"
        if evidence.get(count_field) != len(observed):
            errors.append(f"{count_field} differs from the seed ID list")
    extension_ids = observed_extension if isinstance(observed_extension, list) else []
    extension_authorized = evidence.get("extension_authorized")
    if not isinstance(extension_authorized, bool):
        errors.append("extension_authorized must be boolean")
    elif extension_ids and not extension_authorized:
        errors.append("extension seeds are present without external authorization")
    elif not extension_ids and extension_authorized:
        errors.append("extension_authorized is true but no extension seeds are present")
    maximum_total = seeds.get("maximum_total")
    if isinstance(maximum_total, int) and len(primary) + len(extension) > maximum_total:
        errors.append("protocol seed sets exceed maximum_total")
    return errors


def _validate_bootstrap_samples(metrics: Mapping[str, Any]) -> list[str]:
    """Recompute bootstrap vectors and CI highs from frozen raw result rows."""
    errors: list[str] = []
    if metrics.get("bootstrap_replicates") != _BOOTSTRAP_REPLICATES:
        errors.append("metrics summary bootstrap_replicates is not 20000")
    if metrics.get("bootstrap_seed") != _BOOTSTRAP_SEED:
        errors.append("metrics summary bootstrap_seed is not preregistered")
    if tuple(metrics.get("bootstrap_hierarchy", ())) != _BOOTSTRAP_HIERARCHY:
        errors.append("metrics summary bootstrap hierarchy is not preregistered")
    if metrics.get("oracle_reselected_inside_replicate") is not True:
        errors.append("metrics summary does not attest oracle reselection")
    samples = metrics.get("bootstrap_samples")
    if not isinstance(samples, Mapping):
        return [*errors, "metrics summary bootstrap_samples is missing"]
    raw_inputs = metrics.get("bootstrap_raw_inputs")
    if not isinstance(raw_inputs, Mapping):
        return [*errors, "metrics summary bootstrap_raw_inputs is missing"]
    for sample_name, ci_field in _BOOTSTRAP_FIELDS.items():
        values = samples.get(sample_name)
        if not isinstance(values, list) or len(values) != _BOOTSTRAP_REPLICATES:
            errors.append(f"bootstrap sample vector invalid: {sample_name}")
            continue
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in values
        ):
            errors.append(f"bootstrap sample vector contains non-finite value: {sample_name}")
            continue
        raw_rows = raw_inputs.get(sample_name)
        if not isinstance(raw_rows, list) or not raw_rows:
            errors.append(f"bootstrap raw input rows invalid: {sample_name}")
            continue
        malformed_hierarchy = [
            index for index, row in enumerate(raw_rows)
            if not isinstance(row, Mapping)
            or any(key not in row for key in _BOOTSTRAP_HIERARCHY)
        ]
        if malformed_hierarchy:
            errors.append(
                f"bootstrap raw input hierarchy invalid: {sample_name}"
            )
            continue
        statistic = _BOOTSTRAP_STATISTICS[sample_name]
        try:
            recomputed = hierarchical_bootstrap(
                raw_rows,
                statistic,
                replicates=_BOOTSTRAP_REPLICATES,
                seed=_BOOTSTRAP_SEED,
            )
            computed_high = percentile_interval(recomputed)[1]
        except Exception as exc:
            errors.append(f"bootstrap percentile failed: {sample_name}: {type(exc).__name__}")
            continue
        if recomputed != values:
            errors.append(f"bootstrap raw recomputation mismatch: {sample_name}")
        reported_high = metrics.get(ci_field)
        if (
            isinstance(reported_high, bool)
            or not isinstance(reported_high, (int, float))
            or not math.isfinite(float(reported_high))
            or not math.isclose(float(reported_high), computed_high, rel_tol=0.0, abs_tol=1e-12)
        ):
            errors.append(f"bootstrap CI high mismatch: {ci_field}")
    return errors


def _validate_baseline_execution_identity(
    result: Mapping[str, Any],
    registry: Mapping[str, Any],
    container_manifest: Mapping[str, Any],
) -> list[str]:
    """Ensure executed baseline source/container identities are registry-bound."""
    errors: list[str] = []
    execution = result.get("baseline_execution")
    containers = container_manifest.get("baseline_containers")
    if not isinstance(execution, Mapping):
        return ["result baseline_execution is missing"]
    if not isinstance(containers, Mapping):
        return ["container manifest baseline_containers is missing"]
    try:
        primary = primary_baselines(registry)
    except ProtocolError:
        return ["baseline execution cannot be checked against an eligible registry"]
    expected_names = {str(entry["name"]) for entry in primary}
    observed_names = set(execution)
    if observed_names != expected_names:
        errors.append("baseline execution identities do not exactly match eligible registry")
    if set(containers) != expected_names:
        errors.append("baseline container identities do not exactly match eligible registry")
    for entry in primary:
        name = entry["name"]
        observed = execution.get(name)
        if not isinstance(observed, Mapping):
            errors.append(f"baseline execution identity missing: {name}")
            continue
        if observed.get("source_commit") != entry.get("source_commit"):
            errors.append(f"baseline source commit mismatch: {name}")
        expected_container = containers.get(name)
        observed_container = observed.get("container_digest")
        if (
            not isinstance(expected_container, str)
            or not expected_container
            or observed_container != expected_container
        ):
            errors.append(f"baseline container identity mismatch: {name}")
        elif _CONTAINER_DIGEST_RE.fullmatch(expected_container) is None:
            errors.append(f"baseline container digest is not pinned: {name}")
    return errors


def _validate_selected_incumbent_curve(
    result: Mapping[str, Any], events_path: Path, *, require_full_cap: bool = False
) -> list[str]:
    """Recompute attempt AUC from the unblinded curve and replay checkpoints.

    The ledger owns the selected-incumbent identity and attempt position.  The
    result bundle owns the post-run hidden-test normalized quality.  Requiring
    both streams and joining them by attempt/candidate digest prevents a
    fabricated best-score curve from replacing the actual search trajectory.
    """
    errors: list[str] = []
    try:
        checkpoints = [
            record["payload"]
            for record in replay_result_records(events_path)
            if record["event_type"] == "incumbent_selected"
        ]
    except (LedgerError, OSError, ProtocolError) as exc:
        return [f"cannot load incumbent checkpoints: {type(exc).__name__}"]
    curve = result.get("selected_incumbent_curve")
    if not isinstance(curve, list) or not curve:
        return ["result selected_incumbent_curve is missing"]
    if require_full_cap:
        attempt_cap = result.get("attempt_cap")
        if (
            isinstance(attempt_cap, bool)
            or not isinstance(attempt_cap, int)
            or attempt_cap <= 0
            or len(checkpoints) != attempt_cap
            or len(curve) != attempt_cap
        ):
            errors.append("registered result does not contain one incumbent checkpoint per capped attempt")
    if len(curve) != len(checkpoints):
        errors.append("selected incumbent curve length differs from replay")
    for index, checkpoint in enumerate(checkpoints):
        if index >= len(curve) or not isinstance(curve[index], Mapping):
            errors.append(f"selected incumbent curve row missing: {index}")
            continue
        row = curve[index]
        if row.get("after_attempt") != checkpoint.get("after_attempt"):
            errors.append(f"selected incumbent attempt mismatch: {index}")
        if row.get("candidate_sha256") != checkpoint.get("candidate_sha256"):
            errors.append(f"selected incumbent candidate mismatch: {index}")
        quality = row.get("hidden_test_normalized_quality")
        if (
            isinstance(quality, bool)
            or not isinstance(quality, (int, float))
            or not math.isfinite(float(quality))
        ):
            errors.append(f"selected incumbent quality is non-finite: {index}")
    if errors:
        return errors
    qualities = [float(row["hidden_test_normalized_quality"]) for row in curve]
    computed_auc = statistics.fmean(qualities)
    reported_auc = result.get("auc_attempt")
    if (
        isinstance(reported_auc, bool)
        or not isinstance(reported_auc, (int, float))
        or not math.isfinite(float(reported_auc))
        or not math.isclose(float(reported_auc), computed_auc, rel_tol=0.0, abs_tol=1e-12)
    ):
        errors.append("result auc_attempt differs from selected incumbent curve")
    return errors


def _validate_native_resource_consistency(
    result: Mapping[str, Any], replay: Mapping[str, Any]
) -> list[str]:
    """Bind native result telemetry to the append-only resource ledger."""
    if result.get("track") != "NATIVE_COMPUTE":
        return []
    errors: list[str] = []
    resource_summary = replay.get("resource_summary")
    phases = resource_summary.get("phases") if isinstance(resource_summary, Mapping) else None
    generation = phases.get("generation") if isinstance(phases, Mapping) else None
    totals = generation.get("totals") if isinstance(generation, Mapping) else None
    if not isinstance(totals, Mapping):
        return ["native result cannot be bound to generation resource totals"]
    observed_gpu = result.get("native_gpu_seconds_observed")
    observed_forward = result.get("native_model_forward_time_ms_observed")
    ledger_gpu = totals.get("gpu_seconds")
    ledger_forward = totals.get("model_forward_time_ms")
    for name, value in (
        ("native_gpu_seconds_observed", observed_gpu),
        ("native_model_forward_time_ms_observed", observed_forward),
        ("ledger generation gpu_seconds", ledger_gpu),
        ("ledger generation model_forward_time_ms", ledger_forward),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) < 0
        ):
            errors.append(f"native resource telemetry is missing or invalid: {name}")
    if errors:
        return errors
    if not math.isclose(float(observed_gpu), float(ledger_gpu), rel_tol=0.0, abs_tol=1e-9):
        errors.append("native result GPU seconds differ from resource ledger")
    if not math.isclose(float(observed_forward), float(ledger_forward), rel_tol=0.0, abs_tol=1e-9):
        errors.append("native result model-forward time differs from resource ledger")
    curve = result.get("gpu_anytime_curve")
    if isinstance(curve, list) and curve:
        endpoint = curve[-1].get("observed_gpu_seconds") if isinstance(curve[-1], Mapping) else None
        if not isinstance(endpoint, (int, float)) or not math.isclose(
            float(endpoint), float(observed_gpu), rel_tol=0.0, abs_tol=1e-9
        ):
            errors.append("native result GPU curve endpoint differs from resource ledger")
    return errors


def _validate_controller_provenance(
    result: Mapping[str, Any], task_manifest: Mapping[str, Any] | None = None
) -> list[str]:
    """Require an explicit, frozen controller identity for a Forge result."""
    errors: list[str] = []
    mechanism = result.get("controller_mechanism_id")
    if not isinstance(mechanism, str) or mechanism not in _CONTROLLER_MECHANISMS:
        errors.append("result controller mechanism identity is missing or unknown")
    policy = result.get("controller_policy_sha256")
    if not isinstance(policy, str) or re.fullmatch(r"[0-9a-f]{64}", policy) is None:
        errors.append("result controller policy hash is not pinned")
    training_ids = result.get("controller_training_problem_ids")
    if (
        not isinstance(training_ids, list)
        or not training_ids
        or any(not isinstance(item, str) or not item.strip() for item in training_ids)
    ):
        errors.append("result controller training problem IDs are missing")
    elif isinstance(task_manifest, Mapping):
        development = task_manifest.get("development_problems")
        if (
            not isinstance(development, list)
            or any(item not in development for item in training_ids)
        ):
            errors.append("controller training problem IDs include non-development tasks")
    holdout_updates = result.get("controller_holdout_update_attempts")
    if (
        isinstance(holdout_updates, bool)
        or not isinstance(holdout_updates, int)
        or holdout_updates != 0
    ):
        errors.append("result controller holdout update attempts are non-zero")
    actions = result.get("controller_actions")
    if not isinstance(actions, list) or not actions:
        errors.append("result controller actions are missing")
    else:
        for index, record in enumerate(actions):
            if not isinstance(record, Mapping):
                errors.append(f"result controller action is not an object: {index}")
                continue
            action = record.get("action")
            state = record.get("state")
            if not isinstance(action, Mapping):
                errors.append(f"result controller action payload is missing: {index}")
            else:
                missing = sorted(_CONTROLLER_ACTION_FIELDS - set(action))
                if missing:
                    errors.append(
                        f"result controller action fields are missing at {index}: {','.join(missing)}"
                    )
                model = action.get("generator_model")
                if (
                    not isinstance(model, str)
                    or not model.strip()
                    or model.strip().lower() in _UNRESOLVED_CONTROLLER_MODELS
                ):
                    errors.append(f"result controller model identity is unresolved: {index}")
                for field in ("number_of_offspring", "reflection_depth"):
                    value = action.get(field)
                    if isinstance(value, bool) or not isinstance(value, int):
                        errors.append(f"result controller action {field} is not an integer: {index}")
            if not isinstance(state, Mapping):
                errors.append(f"result controller search state is missing: {index}")
            else:
                missing = sorted(_CONTROLLER_STATE_FIELDS - set(state))
                if missing:
                    errors.append(
                        f"result controller search state fields are missing at {index}: {','.join(missing)}"
                    )
                for field in _CONTROLLER_STATE_FIELDS:
                    value = state.get(field)
                    if field in {"remaining_budget", "time_since_last_improvement"}:
                        valid = isinstance(value, int) and not isinstance(value, bool) and value >= 0
                    else:
                        valid = (
                            isinstance(value, (int, float))
                            and not isinstance(value, bool)
                            and math.isfinite(float(value))
                            and (field == "improvement_slope" or float(value) >= 0)
                        )
                    if not valid:
                        errors.append(f"result controller search state value is invalid: {index}.{field}")
    return errors


def _validate_controller_action_trace(
    result: Mapping[str, Any], events_path: Path
) -> list[str]:
    """Cross-check recorded controller actions against ledger start events."""
    errors: list[str] = []
    try:
        # Controller actions are committed on ``attempt_started`` events,
        # which belong to the decision replay stream (the result stream only
        # contains finished attempts and incumbent checkpoints).  Reading the
        # latter would make every non-empty trace appear to have no ledger
        # counterpart and would mask independent integrity checks such as
        # cross-run budget consistency.
        records = replay_decision_records(events_path)
    except (LedgerError, OSError, ProtocolError) as exc:
        return [f"cannot load controller action trace: {type(exc).__name__}"]
    expected: dict[int, Mapping[str, Any]] = {}
    for record in records:
        if record.get("event_type") != "attempt_started":
            continue
        payload = record.get("payload")
        if not isinstance(payload, Mapping):
            errors.append("controller action start payload is not an object")
            continue
        generation = payload.get("generation")
        metadata = payload.get("metadata")
        action = metadata.get("controller_action") if isinstance(metadata, Mapping) else None
        if isinstance(generation, bool) or not isinstance(generation, int):
            errors.append("controller action generation is invalid")
            continue
        if not isinstance(action, Mapping):
            errors.append(f"ledger controller action is missing for generation {generation}")
            continue
        previous = expected.get(generation)
        if previous is not None and canonical_json(previous) != canonical_json(action):
            errors.append(f"ledger controller action differs within generation {generation}")
        expected[generation] = action
    observed_records = result.get("controller_actions")
    observed: dict[int, Mapping[str, Any]] = {}
    if isinstance(observed_records, list):
        for index, record in enumerate(observed_records):
            if not isinstance(record, Mapping):
                continue
            generation = record.get("generation")
            action = record.get("action")
            if isinstance(generation, bool) or not isinstance(generation, int) or not isinstance(action, Mapping):
                continue
            observed[generation] = action
    if set(observed) != set(expected):
        errors.append("result controller action generations differ from ledger")
    else:
        for generation, action in expected.items():
            if canonical_json(observed[generation]) != canonical_json(action):
                errors.append(f"result controller action differs from ledger: generation {generation}")
    return errors


def _bundle_artifact(root: Path, relative: Any, field: str) -> Path:
    """Resolve a matrix artifact and prove it remains inside the bundle."""
    relative = _artifact_relpath(relative, field)
    bundle_root = root.resolve()
    lexical_candidate = bundle_root / relative
    if lexical_candidate.is_symlink():
        raise ProtocolError(f"{field} must not be a symlink")
    candidate = lexical_candidate.resolve()
    try:
        candidate.relative_to(bundle_root)
    except ValueError as exc:
        raise ProtocolError(f"{field} escapes bundle root") from exc
    if not candidate.is_file():
        raise ProtocolError(f"missing registered run artifact: {relative}")
    return candidate


def _validate_registered_run_artifacts(
    root: Path,
    matrix: Mapping[str, Any],
    *,
    protocol_spec: Mapping[str, Any],
    task_manifest: Mapping[str, Any],
    require_full_cap: bool = False,
) -> list[str]:
    """Validate every matrix row against its real event/result artifacts.

    A run matrix containing only hashes is insufficient: a bundle could repeat
    one favorable result hash for every registered seed.  Each row is opened,
    its result identity is checked, and its event stream is independently
    replayed.  Hidden-test access is scanned for every row, not only the
    current run exposed through ``result.json``.
    """
    errors: list[str] = []
    evaluator_budget_limits: list[tuple[str, float]] = []
    rows = matrix.get("runs")
    if not isinstance(rows, list):
        return ["registered run artifacts cannot be checked without matrix rows"]
    identity_fields = (
        "run_id", "study_id", "study_version", "method_id", "problem_id",
        "problem_family", "distribution", "model_tier", "seed", "seed_role", "track",
    )
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            errors.append(f"registered run row is not an object: {index}")
            continue
        try:
            events_path = _bundle_artifact(root, row.get("events_path"), f"runs[{index}].events_path")
            result_path = _bundle_artifact(root, row.get("result_path"), f"runs[{index}].result_path")
            row_result = _read_json(result_path)
            if not isinstance(row_result, Mapping):
                raise ProtocolError(f"registered result is not an object: {result_path.name}")
            validate_result_schema(
                row_result,
                task_manifest=task_manifest,
                protocol_spec=protocol_spec,
            )
            controller_trace_errors = _validate_controller_action_trace(row_result, events_path)
            if controller_trace_errors:
                raise ProtocolError("; ".join(controller_trace_errors))
            for field in identity_fields:
                if row_result.get(field) != row.get(field):
                    raise ProtocolError(
                        f"registered result {field} differs from matrix row: {row.get('run_id')}"
                    )
            replay = replay_summary(events_path)
            if replay.get("run_id") != row.get("run_id"):
                raise ProtocolError(f"registered event run_id differs: {row.get('run_id')}")
            if replay.get("tracks") != [row.get("track")]:
                raise ProtocolError(f"registered event track differs: {row.get('run_id')}")
            for result_field, replay_field in (
                ("attempt_count", "attempt_count"),
                ("event_ledger_head_hash", "head_hash"),
                ("decision_hash", "decision_hash"),
                ("result_recomputation_hash", "result_recomputation_hash"),
                ("resource_ledger_hash", "resource_ledger_hash"),
            ):
                if row_result.get(result_field) != replay.get(replay_field):
                    raise ProtocolError(
                        f"registered result {result_field} differs from replay: {row.get('run_id')}"
                    )
            if row_result.get("resource_summary") != replay.get("resource_summary"):
                raise ProtocolError(
                    f"registered result resource summary differs: {row.get('run_id')}"
                )
            resource_summary = row_result.get("resource_summary")
            evaluator_budget = (
                resource_summary.get("budgets", {}).get("evaluator", {}).get("calls")
                if isinstance(resource_summary, Mapping)
                else None
            )
            evaluator_limit = evaluator_budget.get("limit") if isinstance(evaluator_budget, Mapping) else None
            if (
                isinstance(evaluator_limit, bool)
                or not isinstance(evaluator_limit, (int, float))
                or not math.isfinite(float(evaluator_limit))
                or float(evaluator_limit) < 0
            ):
                raise ProtocolError(
                    f"registered evaluator budget limit is missing or invalid: {row.get('run_id')}"
                )
            evaluator_budget_limits.append((str(row.get("run_id")), float(evaluator_limit)))
            resource_errors = _validate_native_resource_consistency(row_result, replay)
            if resource_errors:
                raise ProtocolError("; ".join(resource_errors))
            for field in _LINEAGE_COVERAGE_FIELDS:
                if row_result.get(field) != replay.get(field):
                    raise ProtocolError(
                        f"registered result {field} differs from replay: {row.get('run_id')}"
                    )
            for field in (
                "candidate_ast_hash_coverage",
                "accepted_candidate_diff_coverage",
                "parent_child_link_coverage",
                "deterministic_cycle_detection_coverage",
                "evaluator_hack_audit_coverage",
            ):
                if replay.get(field) != 1.0:
                    raise ProtocolError(
                        f"registered lineage/audit coverage is incomplete: {row.get('run_id')}"
                    )
            if replay.get("trace_parent_child_links_complete") is not True:
                raise ProtocolError(
                    f"registered parent-child links are incomplete: {row.get('run_id')}"
                )
            if replay.get("lineage_cycle_count") != 0:
                raise ProtocolError(
                    f"registered candidate lineage contains a cycle: {row.get('run_id')}"
                )
            cap = _TRACK_ATTEMPT_CAPS.get(str(row.get("track")))
            if cap is None or row_result.get("attempt_cap") != cap:
                raise ProtocolError(f"registered result attempt cap differs: {row.get('run_id')}")
            if require_full_cap and (
                row_result.get("attempt_count") != cap
                or replay.get("attempt_count") != cap
            ):
                raise ProtocolError(
                    f"registered result does not contain a full capped run: {row.get('run_id')}"
                )
            hidden_violations = _check_hidden_events(events_path)
            if hidden_violations:
                raise ProtocolError(
                    f"registered event stream exposes hidden feedback: {row.get('run_id')}"
                )
        except (LedgerError, ProtocolError, OSError, ValueError) as exc:
            errors.append(str(exc))
    distinct_evaluator_limits = {limit for _, limit in evaluator_budget_limits}
    if len(distinct_evaluator_limits) > 1:
        errors.append("registered runs do not share the same evaluator budget")
    return errors


def verify_bundle(
    bundle: str | Path,
    *,
    repository_protocol: str | Path = PROTOCOL_PATH,
    require_clean_checkout: bool = True,
) -> dict[str, Any]:
    """Verify a frozen study bundle and return a deterministic JSON report.

    The function only reads paths below ``bundle`` (plus the repository
    protocol used to detect post-freeze protocol drift).  It never creates,
    modifies, or deletes files.  A report is returned even for malformed or
    incomplete bundles so callers can audit the reason for a blocked result.
    """
    root = Path(bundle)
    errors: list[str] = []
    checks: dict[str, Any] = {
        "bundle_exists": root.is_dir() and not root.is_symlink(),
        "read_only": True,
    }
    if not root.is_dir() or root.is_symlink():
        return {
            "bundle": str(root),
            "checks": checks,
            "errors": [
                f"invalid bundle directory: {root}"
                if root.is_symlink() else f"missing bundle directory: {root}"
            ],
            "terminal_state": "BLOCKED_INTEGRITY_FAILURE",
            "research_finished": False,
        }

    bundle_paths: dict[str, Path] = {}
    for name in (*REQUIRED_FILES, EXTERNAL_RECEIPT_FILE):
        try:
            bundle_paths[name] = _safe_bundle_file(root, name)
        except ProtocolError as exc:
            errors.append(str(exc))

    missing = [
        name for name in REQUIRED_FILES
        if name not in bundle_paths or not bundle_paths[name].is_file()
    ]
    checks["required_files_present"] = not missing
    if missing:
        errors.append("missing bundle files: " + ", ".join(sorted(set(missing))))

    parsed: dict[str, Any] = {}
    for name in REQUIRED_FILES:
        path = bundle_paths.get(name)
        if path is None or not path.is_file() or name == "events.jsonl":
            continue
        try:
            parsed[name] = _read_json(path)
        except ProtocolError as exc:
            errors.append(str(exc))

    study = parsed.get("study_manifest.json")
    if isinstance(study, Mapping):
        try:
            validate_frozen_study_manifest(study)
            checks["study_manifest_frozen"] = True
        except ProtocolError as exc:
            checks["study_manifest_frozen"] = False
            errors.append(f"study manifest: {exc}")
    else:
        checks["study_manifest_frozen"] = False
        errors.append("study_manifest.json is not an object")

    if isinstance(study, Mapping):
        protocol_file = Path(repository_protocol).resolve()
        repository_root = protocol_file.parent.parent
        current_commit = _git_head(repository_root)
        clean_checkout = _git_clean(repository_root)
        checks["source_commit_matches_repository"] = (
            current_commit is not None and study.get("source_commit") == current_commit
        )
        checks["source_checkout_clean"] = clean_checkout is True
        if not checks["source_commit_matches_repository"]:
            errors.append("frozen source_commit does not match verifier checkout HEAD")
        if require_clean_checkout and clean_checkout is not True:
            errors.append("verifier checkout is not clean")

    # Every manifest reference is checked against the exact bytes in the
    # bundle.  The study manifest is never trusted as a mere list of claims.
    if isinstance(study, Mapping):
        for field, filename in ASSET_FILES.items():
            path = bundle_paths.get(filename)
            expected = study.get(field)
            actual = _sha256(path) if path is not None and path.is_file() else None
            ok = isinstance(expected, str) and actual == expected
            checks[f"hash_{field}"] = ok
            if not ok:
                errors.append(f"hash mismatch or missing reference: {field}")
        for field, filename in RESULT_ASSET_FILES.items():
            path = bundle_paths.get(filename)
            expected = study.get(field)
            actual = _sha256(path) if path is not None and path.is_file() else None
            ok = isinstance(expected, str) and actual == expected
            checks[f"hash_{field}"] = ok
            if not ok:
                errors.append(f"hash mismatch or missing reference: {field}")

    protocol_spec = parsed.get("protocol.json")
    if isinstance(protocol_spec, Mapping):
        try:
            protocol_path = bundle_paths.get("protocol.json")
            if protocol_path is None:
                raise ProtocolError("protocol.json has no safe bundle path")
            load_protocol(protocol_path)
            checks["protocol_valid"] = True
        except ProtocolError as exc:
            checks["protocol_valid"] = False
            errors.append(f"bundle protocol: {exc}")
        protocol_path = bundle_paths.get("protocol.json")
        bundle_protocol_hash = _sha256(protocol_path) if protocol_path is not None else None
        current_protocol_hash = protocol_hash(repository_protocol)
        checks["repository_protocol_matches_bundle"] = (
            bundle_protocol_hash == current_protocol_hash
        )
        if not checks["repository_protocol_matches_bundle"]:
            errors.append("bundle protocol differs from repository protocol")
    else:
        checks["protocol_valid"] = False

    model = parsed.get("model_manifest.json")
    try:
        validate_model_manifest(model if isinstance(model, Mapping) else {})
        checks["model_manifest_valid"] = True
    except ProtocolError as exc:
        checks["model_manifest_valid"] = False
        errors.append(f"model manifest: {exc}")

    task = parsed.get("task_manifest.json")
    try:
        validate_task_manifest(
            task if isinstance(task, Mapping) else {},
            require_sealed=True,
            protocol_spec=protocol_spec if isinstance(protocol_spec, Mapping) else None,
        )
        search_visible_manifest(task)  # reject accidental hidden values in public view
        checks["task_manifest_valid_and_sealed"] = True
    except (ProtocolError, TaskManifestError) as exc:
        checks["task_manifest_valid_and_sealed"] = False
        errors.append(f"task manifest: {exc}")

    registry = parsed.get("baseline_registry.json")
    try:
        if not isinstance(registry, Mapping):
            raise ProtocolError("baseline registry is not an object")
        # Use the same fail-closed eligibility conjunction as the run-time
        # registry.  An unresolved baseline is not a zero score.
        registry_path = bundle_paths.get("baseline_registry.json")
        if registry_path is None:
            raise ProtocolError("baseline_registry.json has no safe bundle path")
        loaded = load_registry(registry_path)
        primary = primary_baselines(loaded)
        checks["baseline_registry_conformant"] = True
        checks["eligible_baseline_count"] = len(primary)
    except (ProtocolError, OSError) as exc:
        checks["baseline_registry_conformant"] = False
        errors.append(f"baseline registry: {exc}")

    for filename in (
        "evaluator_manifest.json",
        "container_manifest.json",
    "prompt_and_decoding_manifest.json",
    "metrics_summary.json",
    ):
        value = parsed.get(filename)
        try:
            validate_frozen_asset_manifest(value, name=filename)
        except ProtocolError as exc:
            checks[f"{filename[:-5]}_valid"] = False
            errors.append(str(exc))
        else:
            checks[f"{filename[:-5]}_valid"] = True

    events_path = bundle_paths.get("events.jsonl")
    attempt_cap: int | None = None
    if events_path is not None and events_path.is_file():
        try:
            replay = replay_summary(events_path)
            checks["ledger_replay_valid"] = True
            checks["replay"] = replay
            checks["resource_ledger_valid"] = bool(replay.get("resource_ledger_valid"))
            checks["resource_telemetry_complete"] = bool(
                replay.get("resource_summary", {}).get("telemetry_complete")
            )
            if not checks["resource_ledger_valid"]:
                errors.append("resource ledger is invalid or exceeds a declared budget")
            tracks = {str(track) for track in replay.get("tracks", [])}
            attempt_cap = _track_attempt_cap(tracks)
            checks["track_set_valid"] = attempt_cap is not None
            if attempt_cap is None:
                errors.append("event ledger must contain exactly one valid run track")
            elif replay["attempt_count"] > attempt_cap:
                errors.append("event ledger exceeds the frozen track attempt cap")
        except (LedgerError, ProtocolError, OSError) as exc:
            checks["ledger_replay_valid"] = False
            errors.append(f"event ledger replay: {exc}")
        hidden_violations = _check_hidden_events(events_path)
        checks["hidden_event_scan_clean"] = not hidden_violations
        errors.extend(hidden_violations)
    else:
        checks["ledger_replay_valid"] = False
        checks["hidden_event_scan_clean"] = False
        checks["track_set_valid"] = False
        attempt_cap = None

    result = parsed.get("result.json")
    if isinstance(result, Mapping):
        try:
            validate_result_schema(
                result,
                task_manifest=task if isinstance(task, Mapping) else None,
                protocol_spec=protocol_spec if isinstance(protocol_spec, Mapping) else None,
            )
            checks["result_schema_valid"] = True
        except ProtocolError as exc:
            checks["result_schema_valid"] = False
            errors.append(f"result schema: {exc}")
    else:
        checks["result_schema_valid"] = False
        errors.append("result.json is not an object")
    matrix = parsed.get("run_matrix.json")
    if (
        isinstance(matrix, Mapping)
        and isinstance(protocol_spec, Mapping)
        and isinstance(task, Mapping)
    ):
        try:
            matrix_summary = validate_run_matrix(
                matrix,
                protocol_spec=protocol_spec,
                task_manifest=task,
                current_result=result if isinstance(result, Mapping) else None,
                current_events_sha256=_sha256(events_path)
                if events_path is not None and events_path.is_file() else None,
                current_result_sha256=_sha256(bundle_paths["result.json"])
                if bundle_paths.get("result.json") is not None
                and bundle_paths["result.json"].is_file() else None,
                artifact_root=root,
            )
            checks["run_matrix_valid"] = True
            checks["run_matrix"] = matrix_summary
            artifact_errors = _validate_registered_run_artifacts(
                root,
                matrix,
                protocol_spec=protocol_spec,
                task_manifest=task,
                require_full_cap=(
                    bundle_paths.get(EXTERNAL_RECEIPT_FILE) is not None
                    and bundle_paths[EXTERNAL_RECEIPT_FILE].is_file()
                ),
            )
            checks["registered_run_artifacts_valid"] = not artifact_errors
            errors.extend(artifact_errors)
        except ProtocolError as exc:
            checks["run_matrix_valid"] = False
            checks["registered_run_artifacts_valid"] = False
            errors.append(f"run matrix: {exc}")
    else:
        checks["run_matrix_valid"] = False
        checks["registered_run_artifacts_valid"] = False
        errors.append("run_matrix.json cannot be checked")
    if isinstance(result, Mapping) and isinstance(checks.get("replay"), Mapping):
        replay = checks["replay"]
        checks["result_attempt_count_matches_replay"] = (
            result.get("attempt_count") == replay.get("attempt_count")
        )
        checks["result_ledger_head_matches_replay"] = (
            result.get("event_ledger_head_hash") == replay.get("head_hash")
        )
        checks["result_decision_hash_matches_replay"] = (
            result.get("decision_hash") == replay.get("decision_hash")
        )
        checks["result_recomputation_hash_matches_replay"] = (
            result.get("result_recomputation_hash")
            == replay.get("result_recomputation_hash")
        )
        result_resources = result.get("resource_summary")
        checks["result_resource_summary_matches_replay"] = (
            isinstance(result_resources, Mapping)
            and canonical_json(result_resources)
            == canonical_json(replay.get("resource_summary"))
        )
        checks["result_resource_ledger_hash_matches_replay"] = (
            result.get("resource_ledger_hash") == replay.get("resource_ledger_hash")
        )
        for field in _LINEAGE_COVERAGE_FIELDS:
            checks[f"result_{field}_matches_replay"] = result.get(field) == replay.get(field)
            if not checks[f"result_{field}_matches_replay"]:
                errors.append(f"result {field} differs from replay")
        replay_tracks = replay.get("tracks", [])
        result_track = result.get("track")
        checks["result_track_matches_replay"] = (
            isinstance(result_track, str)
            and replay_tracks == [result_track]
        )
        checks["result_attempt_cap_matches_replay"] = (
            attempt_cap is not None and result.get("attempt_cap") == attempt_cap
        )
        if not checks["result_attempt_count_matches_replay"]:
            errors.append("result attempt_count differs from replay")
        if not checks["result_ledger_head_matches_replay"]:
            errors.append("result ledger head differs from replay")
        if not checks["result_decision_hash_matches_replay"]:
            errors.append("result decision hash differs from replay")
        if not checks["result_recomputation_hash_matches_replay"]:
            errors.append("result recomputation hash differs from replay")
        if not checks["result_resource_summary_matches_replay"]:
            errors.append("result resource summary differs from replay")
        if not checks["result_resource_ledger_hash_matches_replay"]:
            errors.append("result resource ledger hash differs from replay")
        if not checks["result_track_matches_replay"]:
            errors.append("result track differs from replay")
        if not checks["result_attempt_cap_matches_replay"]:
            errors.append("result attempt_cap differs from frozen track cap")
        curve_errors = _validate_selected_incumbent_curve(
            result,
            events_path,
            require_full_cap=(
                bundle_paths.get(EXTERNAL_RECEIPT_FILE) is not None
                and bundle_paths[EXTERNAL_RECEIPT_FILE].is_file()
            ),
        )
        checks["selected_incumbent_auc_valid"] = not curve_errors
        errors.extend(curve_errors)
        native_resource_errors = _validate_native_resource_consistency(result, replay)
        checks["native_resource_consistency_valid"] = not native_resource_errors
        errors.extend(native_resource_errors)
        controller_errors = _validate_controller_provenance(result, task)
        if events_path is not None and events_path.is_file():
            controller_errors.extend(_validate_controller_action_trace(result, events_path))
        checks["controller_provenance_valid"] = not controller_errors
        errors.extend(controller_errors)
    else:
        checks["result_attempt_count_matches_replay"] = False
        checks["result_ledger_head_matches_replay"] = False
        checks["result_decision_hash_matches_replay"] = False
        checks["result_recomputation_hash_matches_replay"] = False
        checks["result_resource_summary_matches_replay"] = False
        checks["result_resource_ledger_hash_matches_replay"] = False
        checks["result_track_matches_replay"] = False
        checks["result_attempt_cap_matches_replay"] = False
        checks["selected_incumbent_auc_valid"] = False
        checks["native_resource_consistency_valid"] = False
        checks["controller_provenance_valid"] = False
        errors.append("result.json cannot be checked against replay")

    registry_for_execution = parsed.get("baseline_registry.json")
    container_for_execution = parsed.get("container_manifest.json")
    if isinstance(result, Mapping) and isinstance(registry_for_execution, Mapping) and isinstance(
        container_for_execution, Mapping
    ):
        execution_errors = _validate_baseline_execution_identity(
            result, registry_for_execution, container_for_execution
        )
        checks["baseline_execution_identity_valid"] = not execution_errors
        errors.extend(execution_errors)
    else:
        checks["baseline_execution_identity_valid"] = False
        errors.append("baseline execution identity cannot be checked")

    evidence = parsed.get("evidence.json")
    missing_evidence = sorted(_required_evidence_fields() - set(evidence or {})) \
        if isinstance(evidence, Mapping) else sorted(_required_evidence_fields())
    checks["evidence_complete"] = not missing_evidence
    if missing_evidence:
        errors.append("evidence missing fields: " + ", ".join(missing_evidence))
    if isinstance(evidence, Mapping):
        seed_errors = _validate_seed_completeness(evidence, protocol_spec)
        checks["seed_completeness_valid"] = not seed_errors
        errors.extend(seed_errors)
        matrix_summary = checks.get("run_matrix")
        matrix_value = parsed.get("run_matrix.json")
        if isinstance(matrix_summary, Mapping) and isinstance(matrix_value, Mapping):
            for field in (
                "primary_seed_ids", "extension_seed_ids", "extension_authorized",
            ):
                if evidence.get(field) != matrix_value.get(field):
                    errors.append(f"evidence {field} differs from frozen run matrix")
    else:
        checks["seed_completeness_valid"] = False
        errors.append("seed completeness cannot be checked without evidence")
    if (
        isinstance(evidence, Mapping)
        and evidence.get("resource_budget_telemetry_complete") is True
        and checks.get("resource_telemetry_complete") is not True
    ):
        errors.append("evidence claims complete resource telemetry but ledger is incomplete")

    metrics_summary = parsed.get("metrics_summary.json")
    if isinstance(metrics_summary, Mapping) and isinstance(evidence, Mapping):
        bootstrap_errors = _validate_bootstrap_samples(metrics_summary)
        checks["bootstrap_samples_valid"] = not bootstrap_errors
        errors.extend(bootstrap_errors)
        try:
            validate_metric_gate_claims(metrics_summary)
            checks["metric_gate_claims_valid"] = True
            derived = derive_q_statuses(metrics_summary)
            checks["metrics_summary_valid"] = True
            checks["derived_q_statuses_match_evidence"] = all(
                evidence.get(key) == value for key, value in derived.items()
            )
            checks["derived_gate_values_match_evidence"] = all(
                evidence.get(key) == metrics_summary.get(key)
                for key in STRONG_BOOLEAN_GATES
            )
            if not checks["derived_q_statuses_match_evidence"]:
                errors.append("derived Q statuses differ from evidence")
            if not checks["derived_gate_values_match_evidence"]:
                errors.append("derived readiness gates differ from evidence")
        except ProtocolError as exc:
            checks["metric_gate_claims_valid"] = False
            checks["metrics_summary_valid"] = False
            checks["derived_q_statuses_match_evidence"] = False
            checks["derived_gate_values_match_evidence"] = False
            errors.append(f"metrics summary: {exc}")
    else:
        checks["bootstrap_samples_valid"] = False
        checks["metric_gate_claims_valid"] = False
        checks["metrics_summary_valid"] = False
        checks["derived_q_statuses_match_evidence"] = False
        checks["derived_gate_values_match_evidence"] = False
        errors.append("metrics_summary.json cannot be checked")

    receipt_errors = _validate_external_receipt(root, study, result if isinstance(result, Mapping) else None)
    checks["external_verifier_receipt_valid"] = not receipt_errors
    errors.extend(receipt_errors)

    computed = "BLOCKED_INTEGRITY_FAILURE"
    if isinstance(evidence, Mapping) and not errors:
        computed = final_verdict(evidence)
    reported_terminal = result.get("terminal_state") if isinstance(result, Mapping) else None
    checks["result_terminal_state_matches_evidence"] = reported_terminal == computed
    if reported_terminal != computed:
        errors.append("result terminal_state differs from independently recomputed verdict")
        computed = "BLOCKED_INTEGRITY_FAILURE"
    checks["verdict_evidence_integrity_ready"] = (
        isinstance(evidence, Mapping) and integrity_ready(evidence)
    )
    return {
        "bundle": str(root),
        "checks": checks,
        "errors": sorted(set(errors)),
        "terminal_state": computed,
        "research_finished": computed in RESEARCH_TERMINAL_STATES and not errors,
    }
