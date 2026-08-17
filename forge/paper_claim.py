"""Fail-closed audit for claims made from a Forge comparison bundle.

The matched-model comparison and the native-paper-reproduction study are
different scientific objects.  This module makes that distinction explicit
and refuses to promote a common-harness result into a paper-level
"surpasses prior work" claim when the registered evidence is missing.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .baselines import PEER_REQUIRED, load_registry
from .protocol import ProtocolError, load_protocol, strict_json_loads


PAPER_CLAIM_AUDIT_SCHEMA_VERSION = 1
DEFAULT_PROTOCOL_PATH = Path(__file__).resolve().parents[1] / "protocol" / "forge_research_v3.json"
DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "protocol" / "baseline_registry_v3.json"
DEFAULT_NATIVE_CONTRACT_PATH = Path(__file__).resolve().parents[1] / "protocol" / "native_reproduction_contract_v1.json"


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProtocolError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ProtocolError(f"{label} must be a JSON object: {path}")
    return value


def _check(
    name: str,
    *,
    observed: Any,
    required: Any,
    passed: bool,
    reason: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "pass" if passed else "fail",
        "observed": observed,
        "required": required,
    }
    if reason:
        result["reason"] = reason
    return {name: result}


def _missing(name: str, *, required: Any, reason: str) -> dict[str, Any]:
    return {
        name: {
            "status": "missing",
            "observed": None,
            "required": required,
            "reason": reason,
        }
    }


def _optional_object(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return _read_object(path, "optional research metrics")


def _read_jsonl_objects(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line_number, raw in enumerate(path.read_bytes().splitlines(), 1):
            value = strict_json_loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError(f"line {line_number} is not an object")
            rows.append(value)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError(f"cannot read comparison results: {path}") from exc
    return rows


def _method_manifests(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    methods = manifest.get("methods")
    if not isinstance(methods, list):
        return []
    return [item for item in methods if isinstance(item, Mapping)]


def audit_paper_claim(
    bundle: str | Path,
    *,
    protocol_path: str | Path = DEFAULT_PROTOCOL_PATH,
    registry_path: str | Path = DEFAULT_REGISTRY_PATH,
) -> dict[str, Any]:
    """Audit a comparison bundle against the registered paper claim gate.

    ``matched_adapter_descriptive_claim_ready`` is intentionally weaker than
    ``native_surpass_claim_ready``.  A passing matched-model check permits a
    narrowly scoped statement about the common harness; it never permits a
    claim about the original paper methods.
    """
    target = Path(bundle)
    manifest = _read_object(target / "comparison_manifest.json", "comparison manifest")
    summary = _read_object(target / "comparison_summary.json", "comparison summary")
    fairness = _read_object(target / "fairness_receipt.json", "fairness receipt")
    result_rows = _read_jsonl_objects(target / "comparison_results.jsonl")
    protocol = load_protocol(protocol_path)
    registry = load_registry(registry_path)
    native_contract = _read_object(DEFAULT_NATIVE_CONTRACT_PATH, "native reproduction contract")

    methods = _method_manifests(manifest)
    method_ids = [item.get("method_id") for item in methods]
    rows = summary.get("rows")
    if not isinstance(rows, list):
        rows = []
    registry_entries = registry["entries"]
    peer_entries = [entry for entry in registry_entries if entry.get("name") in PEER_REQUIRED]
    eligible_peers = [entry["name"] for entry in peer_entries if _eligible_without_import(entry)]
    required_baselines = list(protocol["baselines"]["peer_reviewed_required"])
    missing_baselines = [name for name in required_baselines if name not in method_ids]
    native_contract_rows = native_contract.get("methods")
    if not isinstance(native_contract_rows, list):
        native_contract_rows = []
    native_contract_by_id = {
        row.get("method_id"): row for row in native_contract_rows if isinstance(row, Mapping)
    }
    native_contract_model_resolved = sum(
        isinstance(native_contract_by_id.get(name, {}).get("model"), Mapping)
        and native_contract_by_id[name]["model"].get("resolved") is True
        for name in required_baselines
    )
    native_contract_prompt_resolved = sum(
        isinstance(native_contract_by_id.get(name, {}).get("prompt"), Mapping)
        and native_contract_by_id[name]["prompt"].get("resolved") is True
        for name in required_baselines
    )
    native_contract_executed = sum(
        native_contract_by_id.get(name, {}).get("status") == "executed_native"
        for name in required_baselines
    )

    holdout = protocol["holdout"]
    seeds = protocol["seeds"]["primary"]
    budgets = protocol["budgets"]
    target_metrics = protocol["metrics"]
    stats = protocol["statistics"]

    checks: dict[str, dict[str, Any]] = {}
    checks.update(_check(
        "fairness_receipt",
        observed=fairness.get("fairness_pass"),
        required=True,
        passed=fairness.get("fairness_pass") is True,
        reason="同一条件の比較であることの最低限の確認",
    ))
    checks.update(_check(
        "adapter_scope_declared",
        observed={
            "track": manifest.get("track"),
            "adapter_scope": manifest.get("adapter_scope"),
            "native_paper_reproduction": manifest.get("native_paper_reproduction"),
        },
        required={
            "track": "SAME_MODEL",
            "adapter_scope": "matched_model_same_evaluator",
            "native_paper_reproduction": False,
        },
        passed=(
            manifest.get("track") == "SAME_MODEL"
            and manifest.get("adapter_scope") == "matched_model_same_evaluator"
            and manifest.get("native_paper_reproduction") is False
        ),
        reason="共通ハーネス比較であることを明示しているか",
    ))
    checks.update(_check(
        "method_manifest_completeness",
        observed={
            "manifest_method_count": len(methods),
            "methods_with_adapter_mode": sum("adapter_mode" in item for item in methods),
            "row_method_count": len({row.get("method_id") for row in result_rows if isinstance(row, Mapping)}),
        },
        required="全手法にmethod_idとadapter_modeがあり、行にも対応すること",
        passed=(
            bool(methods)
            and len(methods) == len(method_ids)
            and all(isinstance(item.get("method_id"), str) and item.get("adapter_mode") for item in methods)
            and {item.get("method_id") for item in methods}
            == {row.get("method_id") for row in result_rows if isinstance(row, Mapping)}
        ),
        reason="アダプターの正体を隠さないためのトレーサビリティ",
    ))
    prompt_contract = manifest.get("prompt_contract")
    prompt_contract_pass = (
        isinstance(prompt_contract, Mapping)
        and prompt_contract.get("prompt_builder") == "forge.operators.build_prompt"
        and isinstance(prompt_contract.get("prompt_builder_sha256"), str)
        and len(prompt_contract["prompt_builder_sha256"]) == 64
        and prompt_contract.get("prompt_sha256_recorded_per_attempt") is True
        and isinstance(prompt_contract.get("temperature_rule"), str)
    )
    checks.update(_check(
        "prompt_contract",
        observed=dict(prompt_contract) if isinstance(prompt_contract, Mapping) else None,
        required="prompt builder hash・温度規則・試行ごとのprompt hash",
        passed=prompt_contract_pass,
        reason="使用プロンプトを再現可能にする契約",
    ))
    prompt_trace_rows = [row.get("prompt_trace") for row in result_rows if isinstance(row, Mapping)]
    prompt_trace_pass = (
        len(prompt_trace_rows) == len(result_rows)
        and all(
            isinstance(trace, list)
            and len(trace) == manifest.get("attempt_cap")
            and all(
                isinstance(item, Mapping)
                and isinstance(item.get("prompt_sha256"), str)
                and len(item["prompt_sha256"]) == 64
                and isinstance(item.get("prompt_profile"), str)
                and isinstance(item.get("temperature"), (int, float))
                for item in trace
            )
            for trace in prompt_trace_rows
        )
    )
    checks.update(_check(
        "prompt_trace",
        observed={
            "rows_with_trace": sum(isinstance(trace, list) for trace in prompt_trace_rows),
            "row_count": len(result_rows),
        },
        required="全runの全attemptにprompt_sha256・profile・temperature",
        passed=prompt_trace_pass,
        reason="マニフェストだけでなく実行時プロンプトも記録する",
    ))
    checks.update(_check(
        "native_paper_reproduction",
        observed=manifest.get("native_paper_reproduction"),
        required=True,
        passed=manifest.get("native_paper_reproduction") is True,
        reason="論文手法そのものを比較する主張にはnative再現が必要",
    ))
    checks.update(_check(
        "mandatory_baseline_registry_eligibility",
        observed={"eligible": eligible_peers, "eligible_count": len(eligible_peers)},
        required={"all_required": required_baselines, "eligible_count": len(required_baselines)},
        passed=(len(eligible_peers) == len(required_baselines) and set(eligible_peers) == set(required_baselines)),
        reason="source commit、ライセンス、native smoke、conformance、変更監査の全条件",
    ))
    checks.update(_check(
        "mandatory_baseline_coverage",
        observed={"evaluated": sorted(set(method_ids) & set(required_baselines)), "missing": missing_baselines},
        required=required_baselines,
        passed=not missing_baselines,
        reason="必須peer-reviewed手法を全て比較しているか",
    ))
    checks.update(_check(
        "native_reproduction_contract",
        observed={
            "contract_method_count": len(native_contract_by_id),
            "required_method_count": len(required_baselines),
            "model_resolved_count": native_contract_model_resolved,
            "prompt_resolved_count": native_contract_prompt_resolved,
            "executed_native_count": native_contract_executed,
        },
        required={
            "method_count": len(required_baselines),
            "model_resolved_count": len(required_baselines),
            "prompt_resolved_count": len(required_baselines),
            "executed_native_count": len(required_baselines),
        },
        passed=(
            len(native_contract_by_id) >= len(required_baselines)
            and native_contract_model_resolved == len(required_baselines)
            and native_contract_prompt_resolved == len(required_baselines)
            and native_contract_executed == len(required_baselines)
        ),
        reason="各native手法のモデル・プロンプト・予算を固定し、実行済みであること",
    ))
    checks.update(_check(
        "holdout_problem_count",
        observed=len(manifest.get("problems", [])) if isinstance(manifest.get("problems"), list) else None,
        required=holdout["problem_count"],
        passed=(isinstance(manifest.get("problems"), list) and len(manifest["problems"]) == holdout["problem_count"]),
        reason="未見問題の数",
    ))
    holdout_required = {
        key: holdout[key] for key in (
            "distinct_problem_families_min",
            "families_absent_from_development_min",
            "external_repository_packs_min",
            "search_instance_clusters_min_per_problem",
            "test_instance_clusters_min_per_problem",
            "hidden_test_instances_min_per_problem",
            "size_shift_problems_min",
            "distribution_shift_problems_min",
        )
    }
    holdout_observed = manifest.get("holdout")
    if isinstance(holdout_observed, Mapping):
        holdout_structure_pass = all(
            isinstance(holdout_observed.get(key), int)
            and holdout_observed[key] >= threshold
            for key, threshold in holdout_required.items()
        )
        checks.update(_check(
            "holdout_structure",
            observed=dict(holdout_observed),
            required=holdout_required,
            passed=holdout_structure_pass,
            reason="未見問題のfamily・hidden cluster・shift分布の要件",
        ))
    else:
        checks.update(_missing(
            "holdout_structure",
            required=holdout_required,
            reason="比較バンドルにproblem family・hidden cluster・shift分布の実測値がない",
        ))
    model_profiles = manifest.get("model_profiles")
    model_profile_count = len(model_profiles) if isinstance(model_profiles, list) else (
        1 if isinstance(manifest.get("model"), Mapping) else 0
    )
    checks.update(_check(
        "model_profiles",
        observed={"profile_count": model_profile_count, "profiles": model_profiles},
        required={"profile_count": len(protocol["models"]), "profiles": list(protocol["models"])},
        passed=(
            isinstance(model_profiles, list)
            and len(model_profiles) == len(protocol["models"])
        ),
        reason="現在のバンドルは単一Mockモデルで、3モデルプロファイルを含まない",
    ))
    current_seed_count = len(manifest.get("seeds", [])) if isinstance(manifest.get("seeds"), list) else None
    checks.update(_check(
        "primary_seed_count",
        observed=current_seed_count,
        required=len(seeds),
        passed=current_seed_count == len(seeds),
        reason="V3 primary seed setとの一致",
    ))
    checks.update(_check(
        "same_model_attempt_budget",
        observed=manifest.get("attempt_cap"),
        required=budgets["same_model_attempts"],
        passed=manifest.get("attempt_cap") == budgets["same_model_attempts"],
        reason="4 attemptsは内部比較としては公平だが、登録済み本番ゲートは512 attempts",
    ))
    row_fields = sorted({key for row in result_rows if isinstance(row, Mapping) for key in row})
    checks.update(_check(
        "primary_metric",
        observed={"declared_metric": manifest.get("primary_metric"), "row_fields": row_fields},
        required=target_metrics["primary"],
        passed=manifest.get("primary_metric") == target_metrics["primary"],
        reason="raw auc_by_generationではなくhidden-test正規化AUCが必要",
    ))
    if isinstance(manifest.get("statistics"), Mapping):
        observed_statistics: Any = manifest["statistics"]
        stats_pass = (
            manifest["statistics"].get("bootstrap") == stats["bootstrap"]
            and manifest["statistics"].get("replicates") == stats["replicates"]
            and manifest["statistics"].get("confidence_interval") == stats["confidence_interval"]
        )
    else:
        observed_statistics = None
        stats_pass = False
    checks.update(_check(
        "statistical_contract",
        observed=observed_statistics,
        required={
            "bootstrap": stats["bootstrap"],
            "replicates": stats["replicates"],
            "confidence_interval": stats["confidence_interval"],
        },
        passed=stats_pass,
        reason="平均値だけでは論文の優越主張にならない",
    ))
    positive_evidence = summary.get("positive_gate_metrics")
    if not isinstance(positive_evidence, Mapping):
        optional_metrics = _optional_object(target / "research_metrics.json")
        positive_evidence = optional_metrics.get("positive_gate_metrics") if optional_metrics else None
    positive_required = tuple(target_metrics["positive_gate_thresholds"])
    if isinstance(positive_evidence, Mapping):
        positive_observed = {key: positive_evidence.get(key) for key in positive_required}
        positive_pass = all(positive_observed[key] is True for key in positive_required)
        checks.update(_check(
            "positive_gate_metrics",
            observed=positive_observed,
            required={key: True for key in positive_required},
            passed=positive_pass,
            reason="登録済みの全positive gateがtrueであること",
        ))
    else:
        checks.update(_missing(
            "positive_gate_metrics",
            required=list(positive_required),
            reason="holdout正規化AUC、95% CI、win rate、regression rateが未収録",
        ))
    adapter_conformance = manifest.get("adapter_conformance")
    if isinstance(adapter_conformance, Mapping):
        adapter_conformance_pass = all(
            adapter_conformance.get(method_id) is True for method_id in method_ids
        )
        checks.update(_check(
            "adapter_conformance",
            observed=dict(adapter_conformance),
            required="各adapterのnative smoke・出力・予算契約がpass",
            passed=adapter_conformance_pass,
            reason="共通条件比較でもアダプターの忠実性を確認する必要がある",
        ))
    else:
        checks.update(_missing(
            "adapter_conformance",
            required="各adapterのnative smoke・出力・予算契約がpass",
            reason="comparison manifestにadapter conformance結果がない",
        ))
    checks.update(_check(
        "registered_thesis_alignment",
        observed={
            "registered": protocol.get("primary_thesis"),
            "bundle_methods": sorted(str(value) for value in method_ids if value is not None),
        },
        required="比較対象の主手法版が登録プロトコルと一致すること",
        passed=any(protocol.get("primary_thesis") == value for value in method_ids),
        reason="登録プロトコルはV1、現行比較バンドルの主手法はV2",
    ))
    checks.update(_check(
        "ablation_coverage",
        observed=sorted(set(method_ids)),
        required=list(protocol["ablations"]),
        passed=set(protocol["ablations"]).issubset(set(method_ids)),
        reason="固定・転移なし・コスト非考慮の因果比較が必要",
    ))

    matched_requirements = (
        "fairness_receipt",
        "adapter_scope_declared",
        "method_manifest_completeness",
        "prompt_contract",
        "prompt_trace",
    )
    native_requirements = (
        "fairness_receipt",
        "native_paper_reproduction",
        "mandatory_baseline_registry_eligibility",
        "mandatory_baseline_coverage",
        "native_reproduction_contract",
        "holdout_problem_count",
        "holdout_structure",
        "model_profiles",
        "primary_seed_count",
        "same_model_attempt_budget",
        "primary_metric",
        "statistical_contract",
        "positive_gate_metrics",
        "registered_thesis_alignment",
        "ablation_coverage",
    )
    matched_statistical_requirements = (
        "fairness_receipt",
        "adapter_scope_declared",
        "method_manifest_completeness",
        "prompt_contract",
        "prompt_trace",
        "adapter_conformance",
        "adapter_conformance",
        "holdout_problem_count",
        "holdout_structure",
        "primary_seed_count",
        "primary_metric",
        "statistical_contract",
        "positive_gate_metrics",
    )

    def gate_ready(names: tuple[str, ...]) -> bool:
        return all(checks[name]["status"] == "pass" for name in names)

    matched_ready = gate_ready(matched_requirements)
    matched_statistical_ready = gate_ready(matched_statistical_requirements)
    native_ready = gate_ready(native_requirements)
    if native_ready:
        claim_scope = "native_prior_method_surpass"
        allowed_claim = "事前登録した同一ベンチマーク・同一資源条件で、必須既存手法群を統計的に上回った。"
    elif matched_statistical_ready:
        claim_scope = "matched_model_adapter_statistical"
        allowed_claim = "論文手法の共通条件アダプターを用いた事前登録比較で、統計的に上回った。"
    elif matched_ready:
        claim_scope = "matched_model_adapter_only"
        allowed_claim = "共通MockLLMハーネスに移植したアダプター比較で、指定ベースラインを上回った。"
    else:
        claim_scope = "no_claim"
        allowed_claim = None

    return {
        "schema_version": PAPER_CLAIM_AUDIT_SCHEMA_VERSION,
        "bundle": str(target.resolve()),
        "protocol_id": protocol.get("protocol_id"),
        "registry_id": registry.get("registry_id"),
        "checks": checks,
        "gates": {
            "matched_adapter_descriptive_claim_ready": matched_ready,
            "matched_adapter_statistical_claim_ready": matched_statistical_ready,
            "native_surpass_claim_ready": native_ready,
        },
        "claim_scope": claim_scope,
        "allowed_claim": allowed_claim,
        "forbidden_claims_when_native_gate_fails": [
            "FunSearch、EoH、ReEvoなどの論文手法そのものをsurpassした",
            "state of the artを達成した",
            "未見問題にも一般化した",
        ],
    }


def _eligible_without_import(entry: Mapping[str, Any]) -> bool:
    """Use the registry's public predicate without duplicating its validator."""
    from .baselines import baseline_eligible

    return baseline_eligible(entry)
