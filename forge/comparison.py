"""Matched-model comparison harness for the LLM4AD-compatible packs.

This module is intentionally a *same-model adapter track*.  It does not claim
to be a bit-for-bit reproduction of every native implementation in LLM4AD or
``llm-eps``.  Instead, each method is represented by a frozen search policy
that is executed by the same Forge V3 loop, the same MockLLM, the same problem
pack, and the same four-attempt/evaluator budgets.

The native paper-reproduction track remains a separate concern.  Keeping the
tracks separate prevents a paper's original LLM/prompt/budget from being
mistaken for a matched-model comparison.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable, Mapping, Sequence

from .controller import ComputeAwareController, SearchAction, load_controller_manifest
from .development import DevelopmentProblem, _load_problem
from .loop import run
from .protocol import ProtocolError, canonical_json, load_protocol
from .replay import replay_summary


COMPARISON_SCHEMA_VERSION = 1
COMPARISON_STUDY_ID = "FORGE_LLM4AD_COMMON_MODEL_COMPARISON_V2"
ATTEMPT_CAP = 4
EVALUATOR_BUDGET = 4
MODEL_ID = "FORGE_MOCK_AST_V2"
MODEL_SEED_RULE = "per_run_seed"
SAME_MODEL_TRACK = "SAME_MODEL"

PRIMARY_METHOD = "TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V2"
FIXED_METHOD = "FIXED_DEV_BEST"
PAPER_METHODS = ("FunSearch", "EoH", "ReEvo", "HillClimb", "RandomSampling")
METHOD_ORDER = (PRIMARY_METHOD, FIXED_METHOD, *PAPER_METHODS)


@dataclass(frozen=True)
class ComparisonMethod:
    """One method identity and its matched-model adapter contract."""

    method_id: str
    source_url: str
    source_commit: str
    paper_url: str | None
    license_id: str
    adapter_mode: str
    action: SearchAction | None = None
    parent_count: int = 1
    implementation_paths: tuple[str, ...] = ()

    def manifest(self) -> dict[str, Any]:
        payload = {
            "method_id": self.method_id,
            "source_url": self.source_url,
            "source_commit": self.source_commit,
            "paper_url": self.paper_url,
            "license_id": self.license_id,
            "adapter_mode": self.adapter_mode,
            "parent_count": self.parent_count,
            "implementation_paths": list(self.implementation_paths),
        }
        if self.action is not None:
            payload["action"] = asdict(self.action)
        return payload


def _mock_action(
    *,
    parent_selection_policy: str,
    mutation_operator: str,
    reflection_depth: int,
    archive_sampling_policy: str,
) -> SearchAction:
    return SearchAction(
        generator_model="MOCK_MODEL_V1",
        parent_selection_policy=parent_selection_policy,
        mutation_operator=mutation_operator,
        # One model call is one generation slot in the matched track.  This
        # keeps the four-point AUC coordinate identical for all methods.
        number_of_offspring=1,
        reflection_depth=reflection_depth,
        archive_sampling_policy=archive_sampling_policy,
    )


def comparison_methods() -> tuple[ComparisonMethod, ...]:
    """Return the frozen method set used by the matched-model track.

    The source commits identify the public implementations that motivated the
    adapters.  ``adapter_mode`` is explicit because these are common-harness
    adapters, not native paper reproductions.
    """
    llm4ad_url = "https://github.com/Optima-CityU/LLM4AD"
    llm4ad_commit = "ffb6acf64497be93932c98d25369352efd3865cf"
    eps_url = "https://github.com/zhichao-lu/llm-eps"
    eps_commit = "ab09bfabe6b5ed2037cf72ce0074ca89ee8d9185"
    return (
        ComparisonMethod(
            PRIMARY_METHOD,
            "local:forge/controller.py",
            "workspace",
            None,
            "workspace",
            "frozen_development_policy",
            implementation_paths=("forge/controller.py", "protocol/controller_development_actions.json"),
        ),
        ComparisonMethod(
            FIXED_METHOD,
            "local:forge/controller.py",
            "workspace",
            None,
            "workspace",
            "frozen_development_policy",
            implementation_paths=("forge/controller.py", "protocol/controller_development_actions.json"),
        ),
        ComparisonMethod(
            "FunSearch",
            llm4ad_url,
            llm4ad_commit,
            "https://www.nature.com/articles/s41586-023-06924-6",
            "BSD-2-Clause",
            "matched_archive_policy",
            _mock_action(
                parent_selection_policy="uniform",
                mutation_operator="structural",
                reflection_depth=0,
                archive_sampling_policy="uniform",
            ),
            parent_count=1,
            implementation_paths=("llm4ad/method/funsearch", "example/methods/funsearch"),
        ),
        ComparisonMethod(
            "EoH",
            llm4ad_url,
            llm4ad_commit,
            "https://arxiv.org/abs/2311.15249",
            "BSD-2-Clause",
            "matched_population_policy",
            _mock_action(
                parent_selection_policy="diverse",
                mutation_operator="structural",
                reflection_depth=1,
                archive_sampling_policy="best",
            ),
            parent_count=2,
            implementation_paths=("llm4ad/method/eoh", "example/methods/eoh"),
        ),
        ComparisonMethod(
            "ReEvo",
            llm4ad_url,
            llm4ad_commit,
            "https://arxiv.org/abs/2402.01145",
            "BSD-2-Clause",
            "matched_reflective_recombination",
            _mock_action(
                parent_selection_policy="diverse",
                mutation_operator="recombine",
                reflection_depth=2,
                archive_sampling_policy="score_spread",
            ),
            parent_count=2,
            implementation_paths=("llm4ad/method/reevo",),
        ),
        ComparisonMethod(
            "HillClimb",
            eps_url,
            eps_commit,
            None,
            "unresolved_external",
            "matched_incumbent_only",
            _mock_action(
                parent_selection_policy="elite",
                mutation_operator="local",
                reflection_depth=0,
                archive_sampling_policy="best",
            ),
            parent_count=1,
            implementation_paths=("hill_climb/bin_packing_or",),
        ),
        ComparisonMethod(
            "RandomSampling",
            llm4ad_url,
            llm4ad_commit,
            None,
            "BSD-2-Clause",
            "matched_uniform_sampling",
            _mock_action(
                parent_selection_policy="uniform",
                mutation_operator="global",
                reflection_depth=0,
                archive_sampling_policy="random",
            ),
            parent_count=1,
            implementation_paths=("llm4ad/method/funsearch",),
        ),
    )


class StaticComparisonController(ComputeAwareController):
    """Frozen one-action controller used for a matched paper-method adapter."""

    def __init__(self, method: ComparisonMethod):
        if method.action is None:
            raise ProtocolError(f"static adapter has no action: {method.method_id}")
        super().__init__([method.action])
        self.mechanism_id = method.method_id
        self._frozen = True
        self._training_problem_ids = ("matched_model_adapter",)
        self._gain_normalization_scales = {"matched_model_adapter": 1.0}
        self._support[method.action] = 1
        self._policy_sha256 = self._digest()
        self._method_id = method.method_id

    def choose(self, state):  # noqa: D401 - signature is inherited by contract.
        del state
        return self.actions[0]

    def restricted_parents(self, state, action, items):
        """Apply the method's defining parent constraint where applicable."""
        del state, action
        if not items:
            return None
        if self._method_id == "HillClimb":
            return [max(items, key=lambda item: item["score"])]
        # The other adapters intentionally leave parent sampling to the common
        # Forge operator, so their parent policy remains visible in the ledger.
        return None


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ProtocolError(f"cannot hash artifact: {path}") from exc


def _write_json(path: Path, value: Mapping[str, Any]) -> str:
    payload = canonical_json(dict(value))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> str:
    payload = b"".join(canonical_json(dict(row)) for row in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ProtocolError(f"missing JSONL artifact: {path}")
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_bytes().splitlines(), 1):
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProtocolError(f"invalid JSONL at {path}:{line_number}") from exc
        if not isinstance(value, dict):
            raise ProtocolError(f"JSONL row is not an object: {path}:{line_number}")
        rows.append(value)
    return rows


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError(f"{label} must be numeric")
    value = float(value)
    if not math.isfinite(value):
        raise ProtocolError(f"{label} must be finite")
    return value


def matched_attempt_metrics(events: Sequence[Mapping[str, Any]], *, attempt_cap: int,
                            evaluator_budget: int) -> dict[str, Any]:
    """Recompute fixed-coordinate metrics from the append-only ledger."""
    finished = [event for event in events if event.get("event_type") == "attempt_finished"]
    checkpoints = [
        event for event in events if event.get("event_type") == "incumbent_selected"
    ]
    if len(finished) != attempt_cap or len(checkpoints) != attempt_cap:
        raise ProtocolError(
            f"matched run must have {attempt_cap} finished attempts/checkpoints; "
            f"got {len(finished)}/{len(checkpoints)}"
        )
    checkpoint_rows = []
    for event in checkpoints:
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            raise ProtocolError("incumbent checkpoint payload is not an object")
        after_attempt = payload.get("after_attempt")
        if isinstance(after_attempt, bool) or not isinstance(after_attempt, int):
            raise ProtocolError("incumbent checkpoint index is invalid")
        checkpoint_rows.append((after_attempt, _finite(payload.get("score"), "incumbent score")))
    checkpoint_rows.sort(key=lambda row: row[0])
    if [index for index, _ in checkpoint_rows] != list(range(1, attempt_cap + 1)):
        raise ProtocolError("incumbent checkpoints do not cover every attempt exactly once")
    curve = [score for _, score in checkpoint_rows]

    status_counts: dict[str, int] = {}
    evaluator_calls = 0
    for event in finished:
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            raise ProtocolError("attempt finish payload is not an object")
        status = payload.get("status")
        if not isinstance(status, str) or not status:
            raise ProtocolError("attempt finish status is invalid")
        status_counts[status] = status_counts.get(status, 0) + 1
        usage = payload.get("evaluator_resource_usage")
        if usage is not None:
            if not isinstance(usage, Mapping):
                raise ProtocolError("evaluator resource usage is not an object")
            calls = usage.get("evaluator_calls")
            if isinstance(calls, bool) or not isinstance(calls, (int, float)):
                raise ProtocolError("evaluator call count is invalid")
            if calls < 0 or not math.isfinite(float(calls)):
                raise ProtocolError("evaluator call count is invalid")
            evaluator_calls += int(calls)
    if evaluator_calls > evaluator_budget:
        raise ProtocolError("matched run exceeded evaluator budget")

    failures = attempt_cap - status_counts.get("valid_candidate", 0)
    return {
        "attempt_count": attempt_cap,
        "attempt_cap": attempt_cap,
        "auc_by_generation": fmean(curve),
        "auc_definition": "mean best-so-far after each of four attempt slots",
        "best_score": curve[-1],
        "incumbent_curve": curve,
        "failure_count": failures,
        "failure_rate": failures / attempt_cap,
        "status_counts": dict(sorted(status_counts.items())),
        "evaluator_calls": evaluator_calls,
        "evaluator_budget": evaluator_budget,
    }


def _method_by_id(method_id: str) -> ComparisonMethod:
    for method in comparison_methods():
        if method.method_id == method_id:
            return method
    raise ProtocolError(f"unknown comparison method: {method_id}")


def _policy_path(policy_dir: Path, method_id: str) -> Path:
    candidates = (
        policy_dir / "policies" / f"{method_id}.json",
        policy_dir / f"{method_id}.json",
    )
    for path in candidates:
        if path.is_file():
            return path
    raise ProtocolError(f"missing frozen policy for {method_id} under {policy_dir}")


@contextmanager
def _comparison_environment(scale: str):
    if scale not in {"mock", "full"}:
        raise ProtocolError("comparison scale must be 'mock' or 'full'")
    old_mock = os.environ.get("FORGE_MOCK")
    old_scale = os.environ.get("FORGE_BENCH_SCALE")
    os.environ["FORGE_MOCK"] = "1"
    os.environ["FORGE_BENCH_SCALE"] = scale
    try:
        yield
    finally:
        if old_mock is None:
            os.environ.pop("FORGE_MOCK", None)
        else:
            os.environ["FORGE_MOCK"] = old_mock
        if old_scale is None:
            os.environ.pop("FORGE_BENCH_SCALE", None)
        else:
            os.environ["FORGE_BENCH_SCALE"] = old_scale


def _run_config(source: Mapping[str, Any], *, method_id: str, problem_id: str,
                seed: int, run_id: str, scale: str, parent_count: int) -> dict[str, Any]:
    protocol = load_protocol()
    cfg = dict(source)
    cfg.update({
        "protocol_v3": True,
        "mock": True,
        "track": SAME_MODEL_TRACK,
        "study_id": COMPARISON_STUDY_ID,
        "study_version": "2",
        "run_id": run_id,
        "method_id": method_id,
        "problem_id": problem_id,
        "problem_family": problem_id,
        "distribution": f"LLM4AD:{problem_id}:{scale}",
        "model_tier": MODEL_ID,
        "seed": seed,
        "seed_role": "matched_comparison",
        "generations": ATTEMPT_CAP,
        "batch_size": 1,
        "max_attempts": ATTEMPT_CAP,
        "max_cheap_calls": ATTEMPT_CAP,
        "max_evaluator_calls": EVALUATOR_BUDGET,
        "max_smart_calls": 0,
        "parents": parent_count,
        "workers": 1,
        "cheap_workers": 1,
        "islands": 1,
    })
    cfg["resource_budgets"] = {
        "generation": {
            "records": ATTEMPT_CAP,
            "input_tokens": protocol["budgets"]["max_input_tokens"],
            "output_tokens": protocol["budgets"]["max_output_tokens"],
        },
        "evaluator": {"calls": EVALUATOR_BUDGET},
    }
    return cfg


def _artifact_row(root: Path, run_dir: Path, *, method: ComparisonMethod,
                  problem_id: str, seed: int, cfg: Mapping[str, Any],
                  result: Mapping[str, Any], metrics: Mapping[str, Any]) -> dict[str, Any]:
    events_path = run_dir / "events.jsonl"
    result_path = run_dir / "result.json"
    config_path = run_dir / "run_config.json"
    config_hash = _write_json(config_path, dict(cfg))
    replay = replay_summary(events_path)
    prompt_trace: list[dict[str, Any]] = []
    for event in _read_jsonl(events_path):
        if event.get("event_type") != "attempt_finished":
            continue
        payload = event.get("payload")
        metadata = payload.get("metadata") if isinstance(payload, Mapping) else None
        if isinstance(metadata, Mapping):
            prompt_trace.append({
                "prompt_sha256": metadata.get("prompt_sha256"),
                "prompt_profile": metadata.get("prompt_profile"),
                "temperature": metadata.get("temperature"),
            })
    row = {
        "method_id": method.method_id,
        "problem_id": problem_id,
        "seed": seed,
        "model_id": MODEL_ID,
        "model_seed": seed,
        "scale": cfg["distribution"].rsplit(":", 1)[-1],
        "attempt_cap": ATTEMPT_CAP,
        "evaluator_budget": EVALUATOR_BUDGET,
        "run_dir": str(run_dir.relative_to(root)),
        "run_config_sha256": config_hash,
        "events_sha256": _sha256(events_path),
        "result_sha256": _sha256(result_path),
        "decision_hash": replay["decision_hash"],
        "result_recomputation_hash": replay["result_recomputation_hash"],
        "ledger_resource_valid": replay["resource_ledger_valid"],
        "attempt_count": metrics["attempt_count"],
        "auc_by_generation": metrics["auc_by_generation"],
        "best_score": metrics["best_score"],
        "failure_count": metrics["failure_count"],
        "failure_rate": metrics["failure_rate"],
        "evaluator_calls": metrics["evaluator_calls"],
        "status_counts": metrics["status_counts"],
        "incumbent_curve": metrics["incumbent_curve"],
        "prompt_trace": prompt_trace,
        "controller_policy_sha256": result.get("controller_policy_sha256"),
        "controller_mechanism_id": result.get("controller_mechanism_id"),
    }
    return row


def _aggregate(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    cells: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        cells.setdefault((str(row["method_id"]), str(row["problem_id"])), []).append(row)
    output = []
    for (method_id, problem_id), cell in sorted(cells.items()):
        output.append({
            "method_id": method_id,
            "problem_id": problem_id,
            "seed_count": len(cell),
            "seeds": sorted(int(row["seed"]) for row in cell),
            "mean_auc_by_generation": fmean(float(row["auc_by_generation"]) for row in cell),
            "mean_best_score": fmean(float(row["best_score"]) for row in cell),
            "mean_failure_count": fmean(float(row["failure_count"]) for row in cell),
            "mean_evaluator_calls": fmean(float(row["evaluator_calls"]) for row in cell),
            "per_seed": [
                {
                    "seed": row["seed"],
                    "auc_by_generation": row["auc_by_generation"],
                    "best_score": row["best_score"],
                    "failure_count": row["failure_count"],
                }
                for row in sorted(cell, key=lambda item: int(item["seed"]))
            ],
        })
    return output


def validate_comparison_bundle(root: str | Path) -> dict[str, Any]:
    """Fail closed if a comparison bundle violates the matched contract."""
    target = Path(root)
    manifest_path = target / "comparison_manifest.json"
    results_path = target / "comparison_results.jsonl"
    summary_path = target / "comparison_summary.json"
    if not manifest_path.is_file() or not results_path.is_file() or not summary_path.is_file():
        raise ProtocolError("comparison bundle is incomplete")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != COMPARISON_SCHEMA_VERSION:
        raise ProtocolError("unsupported comparison manifest")
    if manifest.get("attempt_cap") != ATTEMPT_CAP:
        raise ProtocolError("comparison attempt cap drifted")
    if manifest.get("evaluator_budget", {}).get("max_calls") != EVALUATOR_BUDGET:
        raise ProtocolError("comparison evaluator budget drifted")
    model = manifest.get("model")
    if model != {"model_id": MODEL_ID, "seed_rule": MODEL_SEED_RULE}:
        raise ProtocolError("comparison model contract drifted")
    methods = manifest.get("methods")
    expected_methods = list(METHOD_ORDER)
    if not isinstance(methods, list) or [item.get("method_id") for item in methods] != expected_methods:
        raise ProtocolError("comparison method set/order drifted")
    rows = _read_jsonl(results_path)
    expected_problems = [item["problem_id"] for item in manifest.get("problems", [])]
    expected_seeds = [int(seed) for seed in manifest.get("seeds", [])]
    expected_keys = {
        (method_id, problem_id, seed)
        for method_id in expected_methods
        for problem_id in expected_problems
        for seed in expected_seeds
    }
    actual_keys = {(row.get("method_id"), row.get("problem_id"), row.get("seed")) for row in rows}
    if actual_keys != expected_keys or len(rows) != len(expected_keys):
        raise ProtocolError("comparison result cells are incomplete or duplicated")
    for row in rows:
        for field in ("model_id", "method_id", "problem_id"):
            if not isinstance(row.get(field), str) or not row[field]:
                raise ProtocolError(f"comparison row has invalid {field}")
        if row.get("model_id") != MODEL_ID or row.get("model_seed") != row.get("seed"):
            raise ProtocolError("comparison model identity is not paired to the run seed")
        if row.get("attempt_cap") != ATTEMPT_CAP or row.get("attempt_count") != ATTEMPT_CAP:
            raise ProtocolError("comparison row does not use exactly four attempts")
        if row.get("evaluator_budget") != EVALUATOR_BUDGET:
            raise ProtocolError("comparison row evaluator budget differs")
        curve = row.get("incumbent_curve")
        if not isinstance(curve, list) or len(curve) != ATTEMPT_CAP:
            raise ProtocolError("comparison row has no fixed four-point curve")
        if row.get("ledger_resource_valid") is not True:
            raise ProtocolError("comparison row has an invalid resource ledger")
        run_dir = target / str(row["run_dir"])
        if not run_dir.is_dir():
            raise ProtocolError(f"comparison run directory is missing: {run_dir}")
        if _sha256(run_dir / "events.jsonl") != row.get("events_sha256"):
            raise ProtocolError("comparison events hash mismatch")
        if _sha256(run_dir / "result.json") != row.get("result_sha256"):
            raise ProtocolError("comparison result hash mismatch")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if not isinstance(summary, dict) or summary.get("rows") != _aggregate(rows):
        raise ProtocolError("comparison summary is not reproducible from result rows")
    return {
        "fairness_pass": True,
        "method_count": len(expected_methods),
        "problem_count": len(expected_problems),
        "seed_count": len(expected_seeds),
        "cell_count": len(rows),
        "attempt_cap": ATTEMPT_CAP,
        "evaluator_budget": EVALUATOR_BUDGET,
    }


def run_method_comparison(
    problems: Sequence[DevelopmentProblem],
    output_dir: str | Path,
    *,
    seeds: Sequence[int],
    policy_dir: str | Path,
    scale: str = "full",
    methods: Sequence[str] = METHOD_ORDER,
) -> dict[str, Any]:
    """Run the complete matched-model matrix and emit an auditable bundle."""
    if not problems:
        raise ProtocolError("comparison requires at least one problem")
    if not seeds or any(isinstance(seed, bool) or not isinstance(seed, int) for seed in seeds):
        raise ProtocolError("comparison seeds must be non-empty integers")
    if len(set(seeds)) != len(seeds):
        raise ProtocolError("comparison seeds must be unique")
    if tuple(methods) != METHOD_ORDER:
        raise ProtocolError("comparison method set must use the frozen method order")
    policy_root = Path(policy_dir).resolve()
    target = Path(output_dir).resolve()
    if target.exists() and any(target.iterdir()):
        raise ProtocolError(f"comparison output directory is not empty: {target}")
    target.mkdir(parents=True, exist_ok=True)
    method_specs = {method.method_id: method for method in comparison_methods()}
    policy_objects: dict[str, ComputeAwareController] = {}
    for method_id in (PRIMARY_METHOD, FIXED_METHOD):
        policy_objects[method_id] = load_controller_manifest(
            _policy_path(policy_root, method_id)
        )
    loaded: dict[str, tuple[Any, dict[str, Any]]] = {}
    problem_rows = []
    for spec in problems:
        factory, source_config = _load_problem(spec)
        loaded[spec.problem_id] = (factory, source_config)
        source_path = spec.problem_dir.resolve()
        problem_rows.append({
            "problem_id": spec.problem_id,
            "problem_dir": str(source_path),
            "problem_sha256": _sha256(source_path / "problem.py"),
            "config_sha256": _sha256(source_path / "config.json"),
            "data_seed": 2024,
            "scale": scale,
        })

    manifest = {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "study_id": COMPARISON_STUDY_ID,
        "track": SAME_MODEL_TRACK,
        "adapter_scope": "matched_model_same_evaluator",
        "native_paper_reproduction": False,
        "model": {"model_id": MODEL_ID, "seed_rule": MODEL_SEED_RULE},
        "attempt_cap": ATTEMPT_CAP,
        "evaluator_budget": {"max_calls": EVALUATOR_BUDGET, "separate_from_generation": True},
        "scale": scale,
        "seeds": list(seeds),
        "methods": [method_specs[method_id].manifest() for method_id in methods],
        "problems": problem_rows,
        "policy_hashes": {
            method_id: policy_objects[method_id].policy_sha256
            for method_id in (PRIMARY_METHOD, FIXED_METHOD)
        },
        "prompt_contract": {
            "prompt_builder": "forge.operators.build_prompt",
            "prompt_builder_sha256": _sha256(Path(__file__).resolve().with_name("operators.py")),
            "prompt_profile": "FORGE_BUILD_PROMPT",
            "prompt_sha256_recorded_per_attempt": True,
            "temperature_rule": "jitter_temperature(problem_config.temperature, alarm, seeded_rng)",
            "model_route_id": MODEL_ID,
            "generator_model_id": "MOCK_MODEL_V1",
        },
    }
    _write_json(target / "comparison_manifest.pre_run.json", manifest)

    rows: list[dict[str, Any]] = []
    with _comparison_environment(scale):
        for method_id in methods:
            method = method_specs[method_id]
            for spec in problems:
                factory, source_config = loaded[spec.problem_id]
                for seed in seeds:
                    run_id = f"comparison-{method_id}-{spec.problem_id}-seed-{seed}"
                    run_dir = target / "runs" / method_id / spec.problem_id / f"seed-{seed}"
                    run_dir.mkdir(parents=True, exist_ok=True)
                    cfg = _run_config(
                        source_config,
                        method_id=method_id,
                        problem_id=spec.problem_id,
                        seed=seed,
                        run_id=run_id,
                        scale=scale,
                        parent_count=method.parent_count,
                    )
                    if method_id in policy_objects:
                        controller = policy_objects[method_id]
                    else:
                        controller = StaticComparisonController(method)
                    run(factory(), cfg, str(run_dir), controller=controller)
                    events = _read_jsonl(run_dir / "events.jsonl")
                    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
                    metrics = matched_attempt_metrics(
                        events,
                        attempt_cap=ATTEMPT_CAP,
                        evaluator_budget=EVALUATOR_BUDGET,
                    )
                    row = _artifact_row(
                        target,
                        run_dir,
                        method=method,
                        problem_id=spec.problem_id,
                        seed=seed,
                        cfg=cfg,
                        result=result,
                        metrics=metrics,
                    )
                    rows.append(row)
                    _write_json(run_dir / "comparison_row.json", row)

    results_hash = _write_jsonl(target / "comparison_results.jsonl", rows)
    summary = {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "study_id": COMPARISON_STUDY_ID,
        "track": SAME_MODEL_TRACK,
        "rows": _aggregate(rows),
    }
    summary_hash = _write_json(target / "comparison_summary.json", summary)
    manifest["results_sha256"] = results_hash
    manifest["summary_sha256"] = summary_hash
    manifest["run_count"] = len(rows)
    manifest_hash = _write_json(target / "comparison_manifest.json", manifest)
    (target / "comparison_manifest.pre_run.json").unlink()
    receipt = {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "study_id": COMPARISON_STUDY_ID,
        "fairness_pass": True,
        "manifest_sha256": manifest_hash,
        "results_sha256": results_hash,
        "summary_sha256": summary_hash,
        "checks": validate_comparison_bundle(target),
    }
    _write_json(target / "fairness_receipt.json", receipt)
    return {
        "output_dir": str(target),
        "manifest_sha256": manifest_hash,
        "results_sha256": results_hash,
        "summary_sha256": summary_hash,
        "fairness_pass": True,
        "rows": rows,
        "summary": summary,
    }
