"""決定論的探索ループ。ここが主であり、LLMは内部の関数呼び出しにすぎない。
制御構造にLLMの「判断」は一切介在しない。全ての分岐はif文と数値比較。"""
from __future__ import annotations
import concurrent.futures as cf
import hashlib
import json, math, os, random, time
from collections.abc import Mapping
from dataclasses import asdict
from .archive import Archive
from .codecheck import audit_candidate
from .controller import ComputeAwareController, SearchState
from .dedup import DedupIndex, ExactDedup, simhash, hamming
from .ledger import EventLedger, LedgerError, candidate_sha256
from .lineage import lineage_metadata
from .llm import Budget, make_caller
from .metrics import run_metrics
from .operators import build_prompt, jitter_temperature, mutate_params, sample_parents
from .protocol import (
    V3_MAX_INPUT_TOKENS,
    V3_MAX_OUTPUT_TOKENS,
    V3_MAX_SEARCH_EVALUATIONS,
    V3_NATIVE_A100_GPU_SECONDS,
    V3_NATIVE_ATTEMPT_CAP,
    V3_SAME_MODEL_ATTEMPT_CAP,
)
from .resources import (
    empty_usage,
    evaluator_usage,
    generation_usage,
    merge_usage,
    normalize_usage,
)
from .replay import replay_decision_hash, replay_result_hash
from .result_schema import NATIVE_GPU_SECONDS, result_identity_sha256
from .verify import extract_block, v0, v0_diagnostic, v1, v2_reflect


def run(problem, cfg: dict, run_dir: str, controller: ComputeAwareController | None = None):
    """Run a search while scoping the V3 sandbox policy to this invocation.

    Problem packs consult ``FORGE_PROTOCOL_V3`` when they execute candidate
    code.  Keeping that switch at the loop boundary makes direct Python API
    callers obey the same policy as the CLI, while restoring the caller's
    environment prevents a later legacy run in the same process from being
    silently upgraded.
    """
    previous_policy = os.environ.get("FORGE_PROTOCOL_V3")
    if cfg.get("protocol_v3", False):
        os.environ["FORGE_PROTOCOL_V3"] = "1"
    try:
        return _run(problem, cfg, run_dir, controller=controller)
    finally:
        if previous_policy is None:
            os.environ.pop("FORGE_PROTOCOL_V3", None)
        else:
            os.environ["FORGE_PROTOCOL_V3"] = previous_policy


def _select_archive(archives, slot: int, policy: str | None, rng: random.Random):
    """Select a search-side island archive for a controller action.

    The selector never sees hidden-test values.  ``uniform`` is implemented as
    deterministic round-robin so a replayed event stream has no extra random
    draw; ``best`` and ``diverse`` use only visible archive scores.
    """
    if not archives:
        raise LedgerError("at least one archive is required")
    if policy is None:
        return archives[slot % len(archives)]
    normalized = str(policy).strip().lower()
    if normalized in {"uniform", "round_robin"}:
        return archives[slot % len(archives)]
    live = [archive for archive in archives if archive.items]
    if not live:
        return archives[slot % len(archives)]
    if normalized in {"best", "elite"}:
        return max(live, key=lambda archive: archive.best["score"])
    if normalized in {"diverse", "score_spread"}:
        return max(
            live,
            key=lambda archive: (
                len({item["score"] for item in archive.items}),
                -archives.index(archive),
            ),
        )
    if normalized == "random":
        return rng.choice(live)
    raise LedgerError(f"unsupported archive sampling policy: {policy}")


def _make_seeded_caller(tier: str, seed: int):
    """Create a seeded caller while preserving legacy test/integration shims.

    Older embedders commonly replace ``make_caller`` with a one-argument
    factory.  The production factory now accepts a seed for deterministic mock
    development, but a compatibility fallback keeps those adapters working
    without hiding unrelated factory errors.
    """
    try:
        return make_caller(tier, seed=seed)
    except TypeError as exc:
        if "unexpected keyword argument 'seed'" not in str(exc):
            raise
        return make_caller(tier)


def _run(problem, cfg: dict, run_dir: str, controller: ComputeAwareController | None = None):
    t0 = time.time()
    rng = random.Random(cfg.get("seed", 0))
    protocol_v3 = bool(cfg.get("protocol_v3", False))
    configured_evaluator_limit = (
        cfg.get("max_evaluator_calls",
                cfg.get("max_search_evaluations", cfg.get("max_cheap_calls", 200)))
        if protocol_v3 else None
    )
    evaluator_limit = (
        min(int(configured_evaluator_limit), V3_MAX_SEARCH_EVALUATIONS)
        if protocol_v3 else None
    )
    budget = Budget(
        cfg.get("max_cheap_calls", 200),
        cfg.get("max_smart_calls", 3),
        max_evaluator_calls=evaluator_limit,
    )
    requested_attempt_cap = (
        int(cfg.get("max_attempts", cfg.get("max_cheap_calls", 200)))
        if protocol_v3 else None
    )
    study_track = str(cfg.get("track", "SAME_MODEL"))
    if protocol_v3:
        if study_track not in {"SAME_MODEL", "NATIVE_COMPUTE"}:
            raise LedgerError(f"unknown V3 track: {study_track}")
        if requested_attempt_cap is None or requested_attempt_cap <= 0:
            raise LedgerError("V3 max_attempts must be positive")
        # A study config may reserve fewer attempts for a mock/dev run, but it
        # may never raise the preregistered same-model hard cap.
        hard_attempt_cap = (
            V3_NATIVE_ATTEMPT_CAP if study_track == "NATIVE_COMPUTE"
            else V3_SAME_MODEL_ATTEMPT_CAP
        )
        attempt_cap = min(requested_attempt_cap, hard_attempt_cap)
    else:
        attempt_cap = None
    resource_budgets = cfg.get("resource_budgets")
    if protocol_v3 and resource_budgets is None:
        resource_budgets = {
            "generation": {
                "records": attempt_cap,
                "input_tokens": V3_MAX_INPUT_TOKENS,
                "output_tokens": V3_MAX_OUTPUT_TOKENS,
            },
            "evaluator": {
                "calls": cfg.get("max_evaluator_calls", V3_MAX_SEARCH_EVALUATIONS),
            },
        }
    if protocol_v3:
        # Preregistered resource caps are immutable. A caller may reserve less,
        # but never more or omit a cap from a V3 run.
        configured = resource_budgets or {}
        configured_generation = configured.get("generation", {})
        configured_evaluator = configured.get("evaluator", {})
        if not isinstance(configured_generation, dict) or not isinstance(configured_evaluator, dict):
            raise LedgerError("V3 resource budget phases must be objects")

        def bounded(value, cap, field):
            if value is None:
                return cap
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise LedgerError(f"V3 resource budget {field} must be non-negative numeric")
            return min(value, cap)

        generation_budget = {
            **configured_generation,
            "records": bounded(configured_generation.get("records"), attempt_cap, "records"),
            "input_tokens": bounded(
                configured_generation.get("input_tokens"), V3_MAX_INPUT_TOKENS, "input_tokens"
            ),
            "output_tokens": bounded(
                configured_generation.get("output_tokens"), V3_MAX_OUTPUT_TOKENS, "output_tokens"
            ),
        }
        if study_track == "NATIVE_COMPUTE":
            generation_budget["gpu_seconds"] = bounded(
                configured_generation.get("gpu_seconds"),
                V3_NATIVE_A100_GPU_SECONDS,
                "gpu_seconds",
            )
        resource_budgets = {
            **configured,
            "generation": generation_budget,
            "evaluator": {
                **configured_evaluator,
                "calls": bounded(
                    configured_evaluator.get("calls"),
                    V3_MAX_SEARCH_EVALUATIONS,
                    "evaluator.calls",
                ),
            },
        }
    ledger = None
    if protocol_v3:
        ledger = EventLedger(
            f"{run_dir}/events.jsonl",
            run_id=str(cfg.get("run_id", run_dir)),
            max_attempts=attempt_cap,
            resource_budgets=resource_budgets,
        )
        # A process may resume a complete ledger, but it must never silently
        # resume an attempt whose outcome was not recorded before a crash.
        ledger.assert_invariants()
    # パラメータ化探索空間(param_space+render)を宣言した問題は、LLMに生コードを
    # 書かせず数値摂動のみで変異する(2026-07-06 mc_fusion診断: 崖状スコア+数値意味論が
    # 厳密な問題では生コード変異の生存率が実測ゼロだったことへの対応。forge本体の共通機構)。
    param_mode = hasattr(problem, "param_space") and hasattr(problem, "render")
    score_spread = cfg.get("parent_score_spread", False)
    cheap = (
        _make_seeded_caller("cheap", int(cfg.get("seed", 0)))
        if not param_mode else None
    )
    # Parametric generation never calls an LLM; do not even initialize the
    # smart adapter, since that would make a local numeric search depend on
    # unrelated API configuration.
    smart = (
        _make_seeded_caller("smart", int(cfg.get("seed", 0)) + 1)
        if not param_mode and cfg.get("max_smart_calls", 3) > 0
        else None
    )
    controller_model_callers = cfg.get("controller_model_callers", {})
    if protocol_v3 and not isinstance(controller_model_callers, Mapping):
        raise LedgerError("controller_model_callers must be a mapping")
    if not protocol_v3:
        controller_model_callers = {}
    mock_execution = cfg.get("mock") is True or os.environ.get("FORGE_MOCK") == "1"
    # 島モデル(FunSearch中核機構)。独立した部分集団をN個並行に育て、定期的に
    # 弱い島を強い島の解で作り直す。1つの局所解に集団全体が引き寄せられるのを
    # 構造的に防ぐ(2026-07-26実測: 60世代440呼び出しを使って1度も改善しなかった
    # 走行があり、単一集団では抜け出せない張り付きが起きている)。
    # islands=1 で従来動作。JSONLは1本を共有し、行の "island" で島を分ける。
    n_islands = max(1, cfg.get("islands", 1))
    archive_path = f"{run_dir}/archive.jsonl"
    archives = [Archive(archive_path, cfg.get("archive_capacity", 50),
                        cfg.get("max_per_score", 0), island=i)
                for i in range(n_islands)]
    archive = archives[0]
    dedup = ExactDedup() if param_mode else DedupIndex(cfg.get("dedup_hamming", 3))
    guidance, alarm = "", False
    prev_guidance_hash = None
    stopped_by = "generations_complete"
    cheap_failed = 0
    dead_generations = 0  # 1件も応答が返らなかった世代の連続回数

    # 初期集団: seedはV0を通してから入れる（採点不能なseedは設定ミス）
    # 島は全て同じseedから出発し、以降は独立に育つ(dedupは全島で共有するので、
    # 同じ文面が別の島で重複して生き残ることはない)。
    if not any(a.items for a in archives):
        if param_mode:
            space = problem.param_space()
            seeds_params = problem.seed_params() if hasattr(problem, "seed_params") else \
                [{k: (lo + hi) / 2 for k, (lo, hi) in space.items()}]
            for p in seeds_params:
                text = problem.render(p)
                score, alive = v0(problem, text)
                if not alive or (
                    isinstance(score, bool)
                    or not isinstance(score, (int, float))
                    or not math.isfinite(float(score))
                ):
                    if protocol_v3:
                        raise LedgerError("V3 param seed must have a finite score")
                    raise AssertionError(
                        "paramシードがV0を通りません。問題定義を直してください。"
                    )
                dedup.is_novel(text)
                for a in archives:
                    a.add({"text": text, "params": p, "score": score, "gen": 0})
        else:
            for s in problem.seed():
                score, alive = v0(problem, s)
                if not alive or (
                    isinstance(score, bool)
                    or not isinstance(score, (int, float))
                    or not math.isfinite(float(score))
                ):
                    if protocol_v3:
                        raise LedgerError("V3 seed must have a finite score")
                    raise AssertionError(
                        "seedがV0を通りません。問題定義を直してください。"
                    )
                dedup.is_novel(s)
                for a in archives:
                    a.add({"text": s, "score": score, "gen": 0})
    else:
        # 再開時: 既存解をdedup索引に登録しないと言い換えが素通りしV0を浪費する
        for a in archives:
            for it in a.items:
                dedup.is_novel(it["text"])

    generations_done = 0
    controller_actions: list[dict] = []
    stop_after_generation = False
    # Search-side controller features are updated only from the completed
    # generation immediately preceding the decision.  They never consult
    # hidden-test feedback or post-hoc scores.
    previous_best_score = float("-inf")
    time_since_improvement = 0
    previous_invalid_rate = 0.0
    previous_duplicate_rate = 0.0
    previous_operator_success = 0.0
    previous_model_success = 0.0
    previous_lineage_depth = 0.0
    generation_limit = int(cfg.get("generations", 20))
    generation_start = 1
    if protocol_v3 and ledger is not None:
        # Resume from the first generation not represented in the append-only
        # ledger.  Replaying archive state alone is insufficient: restarting
        # at generation 1 would collide with the ledger's immutable
        # (generation, slot) keys after a crash or deliberate pause.
        completed_generations = [
            event.get("payload", {}).get("generation")
            for event in ledger.events
            if event.get("event_type") == "attempt_started"
            and isinstance(event.get("payload"), Mapping)
        ]
        last_generation = max(
            (value for value in completed_generations if isinstance(value, int)),
            default=0,
        )
        generation_start = last_generation + 1
        generations_done = last_generation
        if controller is not None:
            prior_by_generation: dict[int, dict] = {}
            for event in ledger.events:
                if event.get("event_type") != "attempt_started":
                    continue
                payload = event.get("payload")
                if not isinstance(payload, Mapping):
                    continue
                generation = payload.get("generation")
                metadata = payload.get("metadata")
                if (
                    isinstance(generation, bool)
                    or not isinstance(generation, int)
                    or not isinstance(metadata, Mapping)
                    or not isinstance(metadata.get("controller_action"), Mapping)
                ):
                    continue
                prior_by_generation.setdefault(generation, {
                    "generation": generation,
                    "action": dict(metadata["controller_action"]),
                    "state": dict(metadata.get("controller_state", {}))
                    if isinstance(metadata.get("controller_state"), Mapping)
                    else {},
                })
            controller_actions = [
                prior_by_generation[generation]
                for generation in sorted(prior_by_generation)
            ]
            if controller_actions:
                latest_state = controller_actions[-1].get("state", {})
                if isinstance(latest_state, Mapping):
                    time_since_improvement = latest_state.get(
                        "time_since_last_improvement", time_since_improvement
                    )
                    previous_invalid_rate = latest_state.get(
                        "candidate_invalid_rate", previous_invalid_rate
                    )
                    previous_duplicate_rate = latest_state.get(
                        "duplicate_rate", previous_duplicate_rate
                    )
                    previous_operator_success = latest_state.get(
                        "recent_operator_success", previous_operator_success
                    )
                    previous_model_success = latest_state.get(
                        "recent_model_success", previous_model_success
                    )
                    previous_lineage_depth = latest_state.get(
                        "parent_lineage_depth", previous_lineage_depth
                    )
    for gen in range(generation_start, generation_limit + 1):
        if protocol_v3:
            if ledger.attempt_count >= (attempt_cap or 0):
                print(f"[gen {gen}] V3 attempt予算枯渇。終了。")
                stopped_by = "budget_exhausted"
                break
        elif not budget.can("cheap"):
            print(f"[gen {gen}] cheap予算枯渇。終了。")
            stopped_by = "budget_exhausted"
            break

        controller_action = None
        state = None
        if protocol_v3 and controller is not None:
            live_for_state = [a for a in archives if a.items]
            scores_for_state = [item["score"] for archive_item in live_for_state
                                for item in archive_item.items]
            current_best_score = (
                max(scores_for_state) if scores_for_state else float("-inf")
            )
            improvement_slope = (
                0.0 if not math.isfinite(previous_best_score)
                else current_best_score - previous_best_score
            )
            if improvement_slope > 0:
                time_since_improvement = 0
            else:
                time_since_improvement += 1
            state = SearchState(
                remaining_budget=(attempt_cap or 0) - ledger.attempt_count,
                improvement_slope=improvement_slope,
                time_since_last_improvement=time_since_improvement,
                archive_behavioral_entropy=(
                    len(set(scores_for_state)) / max(1, len(scores_for_state))
                ),
                archive_score_dispersion=(
                    max(scores_for_state) - min(scores_for_state)
                    if scores_for_state else 0.0
                ),
                candidate_invalid_rate=previous_invalid_rate,
                duplicate_rate=previous_duplicate_rate,
                parent_lineage_depth=previous_lineage_depth,
                recent_operator_success=previous_operator_success,
                recent_model_success=previous_model_success,
                estimated_generation_cost=float(cfg.get("estimated_generation_cost", 1.0)),
            )
            # Retain only the latest search-side incumbent for the next
            # controller decision; hidden evaluation is never involved.
            previous_best_score = current_best_score
            controller_action = controller.choose(state)
            score_spread = (
                controller_action.parent_selection_policy in {"diverse", "score_spread"}
                or controller_action.archive_sampling_policy == "score_spread"
            )
            controller_actions.append({
                "generation": gen,
                "action": controller_action.__dict__,
                "state": asdict(state),
            })
        # --- 生成（並列・多様性注入） ---
        if protocol_v3:
            requested_batch = (
                controller_action.number_of_offspring
                if controller_action is not None
                else cfg.get("batch_size", 8)
            )
            if requested_batch <= 0:
                raise LedgerError("controller selected non-positive offspring count")
            n = min(requested_batch, (attempt_cap or 0) - ledger.attempt_count)
        else:
            n = min(cfg.get("batch_size", 8), budget.max_cheap - budget.cheap_used)
        attempt_meta = []
        incumbent_text = None
        incumbent_score = float("-inf")
        if protocol_v3:
            existing_live = [a for a in archives if a.items]
            if existing_live:
                existing_best = max((a.best for a in existing_live), key=lambda c: c["score"])
                incumbent_text = existing_best["text"]
                incumbent_score = existing_best["score"]
        if protocol_v3:
            for slot in range(n):
                attempt_id = ledger.start_attempt(
                    generation=gen,
                    slot=slot,
                    # Parametric search is a real Forge generator, but it does
                    # not invoke an LLM.  Keep its observed generator identity
                    # distinct from the controller's planned model choice so
                    # resource telemetry cannot claim a model call that never
                    # happened.
                    model=str(
                        "PARAM_MUTATION"
                        if param_mode
                        else (
                            controller_action.generator_model
                            if controller_action is not None
                            else cfg.get("model_manifest_id", "CHEAP")
                        )
                    ),
                    track=str(cfg.get("track", "SAME_MODEL")),
                    metadata=(
                        {
                            # Keep the visible incumbent at generation start
                            # so development traces can measure the first
                            # generation against the fixed seed.
                            "generation_baseline_score": incumbent_score,
                            "controller_action": controller_action.__dict__,
                            "controller_state": asdict(state),
                            "generation_mode": "parametric" if param_mode else "llm",
                            "mock_execution": mock_execution,
                        }
                        if controller_action is not None else {
                            "generation_baseline_score": incumbent_score,
                            "generation_mode": "parametric" if param_mode else "llm",
                            "mock_execution": mock_execution,
                        }
                    ),
                )
                attempt_meta.append({
                    "attempt_id": attempt_id,
                    "attempt_index": ledger.attempt_count,
                    "slot": slot,
                    "generation_started": time.perf_counter(),
                    "status": None,
                    "candidate": None,
                    "score": None,
                    "error_class": None,
                    "generation_resource": None,
                    "evaluator_resource": None,
                    "controller_action": (controller_action.__dict__
                                          if controller_action is not None else None),
                })
        text_to_params = {}
        if param_mode:
            space = problem.param_space()
            outs = []
            for slot in range(n):
                generation_started = time.perf_counter()
                home = _select_archive(
                    archives,
                    slot,
                    controller_action.archive_sampling_policy
                    if controller_action is not None else None,
                    rng,
                )
                parents = sample_parents(
                    home.items,
                    cfg.get("parents", 3),
                    rng,
                    score_spread,
                    selection_policy=(
                        controller_action.parent_selection_policy
                        if controller_action is not None else None
                    ),
                )
                if (
                    state is not None
                    and controller is not None
                    and controller_action is not None
                ):
                    replacement = controller.restricted_parents(
                        state, controller_action, home.items
                    )
                    if replacement is not None:
                        parents = replacement
                if protocol_v3:
                    attempt_meta[slot]["parents"] = [p["text"] for p in parents]
                parent = rng.choice(parents)["params"] if parents else \
                    {k: (lo + hi) / 2 for k, (lo, hi) in space.items()}
                new_params = mutate_params(
                    space,
                    parent,
                    rng,
                    alarm,
                    mutation_operator=(
                        controller_action.mutation_operator
                        if controller_action is not None else None
                    ),
                    parent_pool=[
                        candidate["params"] for candidate in parents
                        if isinstance(candidate.get("params"), dict)
                    ],
                )
                text = problem.render(new_params).strip()  # extract_block()もstrip()するためキーを合わせる
                text_to_params[text] = new_params
                outs.append("```\n" + text + "\n```")
                if protocol_v3:
                    attempt_meta[slot]["generation_resource"] = generation_usage(
                        wall_time_ms=(time.perf_counter() - generation_started) * 1000.0,
                        model_identity="PARAM_MUTATION",
                        sampling_profile={"mode": "parametric"},
                        notes=["non_llm_parametric_generation"],
                    )
        else:
            prompts = []
            prompt_callers = []
            for slot in range(n):
                # 島ごとに親を引く。同じ世代の候補が別々の部分集団から派生する。
                home = _select_archive(
                    archives,
                    slot,
                    controller_action.archive_sampling_policy
                    if controller_action is not None else None,
                    rng,
                )
                parents = sample_parents(
                    home.items,
                    cfg.get("parents", 3),
                    rng,
                    score_spread,
                    selection_policy=(
                        controller_action.parent_selection_policy
                        if controller_action is not None else None
                    ),
                )
                if (
                    state is not None
                    and controller is not None
                    and controller_action is not None
                ):
                    replacement = controller.restricted_parents(
                        state, controller_action, home.items
                    )
                    if replacement is not None:
                        parents = replacement
                if protocol_v3:
                    attempt_meta[slot]["parents"] = [p["text"] for p in parents]
                prompt_text = build_prompt(
                    problem,
                    parents,
                    guidance,
                    rng,
                    cfg.get("ssot", False),
                    mutation_operator=(
                        controller_action.mutation_operator
                        if controller_action is not None else None
                    ),
                    reflection_depth=(
                        controller_action.reflection_depth
                        if controller_action is not None else 0
                    ),
                    controller_mechanism=(
                        controller.mechanism_id
                        if controller is not None and controller_action is not None
                        else None
                    ),
                    controller_state=(
                        asdict(state)
                        if state is not None and controller_action is not None
                        else None
                    ),
                )
                prompt_temperature = jitter_temperature(
                    cfg.get("temperature", 0.8), alarm, rng
                )
                prompts.append((prompt_text, prompt_temperature))
                if protocol_v3:
                    attempt_meta[slot]["prompt_sha256"] = hashlib.sha256(
                        prompt_text.encode("utf-8")
                    ).hexdigest()
                    attempt_meta[slot]["prompt_profile"] = "FORGE_BUILD_PROMPT"
                    attempt_meta[slot]["temperature"] = prompt_temperature
                caller = cheap
                if protocol_v3 and controller_action is not None:
                    model_identity = controller_action.generator_model
                    if model_identity in controller_model_callers:
                        mapped_caller = controller_model_callers[model_identity]
                        if not callable(mapped_caller):
                            raise LedgerError(
                                "controller model mapping must contain callables"
                            )
                        caller = mapped_caller
                    elif not mock_execution:
                        raise LedgerError(
                            "V3 controller model identity has no pinned callable adapter: "
                            f"{model_identity}"
                        )
                prompt_callers.append(caller)
            # cheap_workers は LLM 呼び出しの同時発火数。V0採点の並列度(workers)とは
            # 別に持つ。無料枠の429がレート制限(単位時間あたりの総数)由来なのか
            # 同時実行数由来なのかを切り分けるとき、両方を1つのキーで動かすと
            # 壁時計の増加分が採点側の減速と混ざって帰属できなくなる。既定はworkers。
            cheap_workers = cfg.get("cheap_workers") or cfg.get("workers", 8)
            with cf.ThreadPoolExecutor(max_workers=cheap_workers) as ex:
                if protocol_v3:
                    detailed = list(ex.map(
                        lambda pair: _safe_detailed(pair[0], *pair[1]),
                        zip(prompt_callers, prompts),
                    ))
                    outs = [item["text"] for item in detailed]
                    for meta, item in zip(attempt_meta, detailed):
                        if item["error_class"]:
                            meta["status"] = "model_error"
                            meta["error_class"] = item["error_class"]
                        meta["generation_resource"] = item["resource_usage"]
                else:
                    outs = list(ex.map(lambda pt: _safe(cheap, *pt), prompts))
        if param_mode:
            # param_modeはLLMを呼ばない。予算は生成候補数の上限として機能させる。
            budget.cheap_used += n
        else:
            # Legacy mode historically charged only successful responses.  V3
            # instead charges every generation slot: model errors and empty
            # responses are still attempts and must remain on the denominator.
            failed = sum(1 for out in outs if not out)
            cheap_failed += failed
            budget.cheap_used += n if protocol_v3 else n - failed
            if failed == n:
                dead_generations += 1
                if dead_generations >= cfg.get("max_dead_generations", 3):
                    if protocol_v3:
                        stop_after_generation = True
                    else:
                        print(f"[gen {gen}] cheap層が{dead_generations}世代連続で全滅。終了。")
                        stopped_by = "llm_unavailable"
                        break
            else:
                dead_generations = 0

        # --- dedup → V0 → V1 の順で安い方から棄却 ---
        novel, dup = [], 0
        cand_island: dict[str, int] = {}
        cand_meta: dict[str, dict] = {}
        for slot, out in enumerate(outs):
            cand = extract_block(out)
            if not cand:
                if protocol_v3 and attempt_meta[slot]["status"] is None:
                    attempt_meta[slot]["status"] = "empty_response"
                continue
            if protocol_v3:
                hack_audit = audit_candidate(cand)
                attempt_meta[slot]["candidate"] = cand
                attempt_meta[slot]["evaluator_hack_audit"] = hack_audit
                if hack_audit.get("suspected_hack") is True:
                    attempt_meta[slot]["status"] = "evaluation_hack"
                    attempt_meta[slot]["error_class"] = "EvaluatorHackSignal"
                    continue
                # A controller may construct a synthetic recombination parent
                # that is not yet present in the archive/dedup index.  A
                # no-op mock mutation can return that exact parent, which
                # would otherwise serialize a candidate as its own ancestor
                # and fail the lineage-cycle invariant.  Treat it as the same
                # duplicate as an archive-backed parent.
                if cand in attempt_meta[slot].get("parents", []):
                    dup += 1
                    attempt_meta[slot]["status"] = "duplicate_candidate"
                    continue
            if not dedup.is_novel(cand):
                dup += 1
                if protocol_v3:
                    attempt_meta[slot]["status"] = "duplicate_candidate"
                    attempt_meta[slot]["candidate"] = cand
                continue
            cand_island[cand] = slot % n_islands
            if protocol_v3:
                attempt_meta[slot]["candidate"] = cand
                cand_meta[cand] = attempt_meta[slot]
            novel.append(cand)

        survivors = []
        scoreable = novel
        if protocol_v3 and budget.evaluator_separate:
            evaluator_remaining = max(0, budget.max_evaluator - budget.evaluator_used)
            scoreable = novel[:evaluator_remaining]
            for deferred in novel[evaluator_remaining:]:
                deferred_meta = cand_meta.get(deferred)
                if deferred_meta is not None:
                    deferred_meta["status"] = "evaluator_budget_exhausted"
                    deferred_meta["candidate"] = deferred
        scored = score_candidates(
            problem,
            scoreable,
            cfg.get("workers", 8),
            include_resource=protocol_v3,
            include_diagnostic=protocol_v3,
        )
        # V0 is itself a search evaluator and consumes the separate V3
        # evaluator-call budget.  Count it before V1 so an expensive judge
        # cannot be invoked after the hard evaluator cap is already spent.
        if protocol_v3 and budget.evaluator_separate:
            budget.evaluator_used += len(scored)
        for scored_item in scored:
            if protocol_v3:
                cand, score, alive, eval_resource, failure_status, error_class = scored_item
            else:
                cand, score, alive = scored_item
                eval_resource = None
            meta = cand_meta.get(cand) if protocol_v3 else None
            if not alive:
                if meta is not None:
                    meta["status"] = failure_status
                    if error_class:
                        meta["error_class"] = error_class
                    # ``v0_diagnostic`` uses -inf as a legacy failure
                    # sentinel.  Failure scores are not metric inputs and
                    # must not be serialized as non-finite JSON; retain an
                    # explicit missing value instead.  A valid candidate's
                    # finite score is still checked by the ledger.
                    meta["score"] = (
                        score
                        if (
                            isinstance(score, (int, float))
                            and not isinstance(score, bool)
                            and math.isfinite(float(score))
                        )
                        else None
                    )
                    meta["evaluator_resource"] = eval_resource
                continue
            if not param_mode:
                evaluator_before = (
                    budget.evaluator_used if budget.evaluator_separate else budget.cheap_used
                )
                evaluator_started = time.perf_counter()
                passed_v1, v1_resource = v1(
                    problem, cand, cheap, budget, return_resource=protocol_v3
                ) if protocol_v3 else (v1(problem, cand, cheap, budget), None)
                evaluator_after = (
                    budget.evaluator_used if budget.evaluator_separate else budget.cheap_used
                )
                if evaluator_after > evaluator_before:
                    if v1_resource is None:
                        evaluator_elapsed_ms = (time.perf_counter() - evaluator_started) * 1000.0
                        v1_resource = evaluator_usage(
                            wall_time_ms=evaluator_elapsed_ms,
                            evaluator_cost=evaluator_elapsed_ms / 1000.0,
                            evaluator_id="FORGE_EVALUATOR",
                        )
                    eval_resource = merge_usage(eval_resource, v1_resource)
                if not passed_v1:
                    if meta is not None:
                        meta["status"] = "evaluator_rejected"
                        meta["score"] = score
                        meta["evaluator_resource"] = eval_resource
                    continue
            if meta is not None:
                meta["status"] = "valid_candidate"
                meta["score"] = score
                meta["evaluator_resource"] = eval_resource
            item = {"text": cand, "score": score, "gen": gen,
                    "island": cand_island.get(cand, 0)}
            if param_mode and cand in text_to_params:
                item["params"] = text_to_params[cand]
            survivors.append(item)
        alarm = (dup / max(1, n)) > cfg.get("dup_alarm_rate", 0.5)

        if protocol_v3:
            # These are deliberately generation-side diagnostics.  They are
            # not evaluator scores and are safe to expose to a frozen
            # controller before the next generation.
            failed_statuses = {
                "model_error", "empty_response", "invalid_syntax",
                "constraint_violation", "runtime_error", "timeout",
                "sandbox_rejected", "evaluation_hack",
            }
            previous_invalid_rate = sum(
                meta.get("status") in failed_statuses for meta in attempt_meta
            ) / max(1, n)
            previous_duplicate_rate = dup / max(1, n)
            previous_model_success = sum(
                meta.get("status") not in {None, "model_error", "empty_response"}
                for meta in attempt_meta
            ) / max(1, n)
            previous_operator_success = len(survivors) / max(1, len(novel))
            parent_counts = [len(meta.get("parents", [])) for meta in attempt_meta]
            previous_lineage_depth = (
                sum(parent_counts) / len(parent_counts) if parent_counts else 0.0
            )

        for item in survivors:
            archives[item["island"]].add(item)

        if protocol_v3:
            for meta in attempt_meta:
                if meta["status"] is None:
                    # The only path left is a defensive classification failure;
                    # fail closed instead of silently dropping an attempt.
                    meta["status"] = "runtime_error"
                    meta["error_class"] = "unclassified_attempt"
                if meta.get("generation_resource") is None:
                    meta["generation_resource"] = generation_usage(
                        wall_time_ms=(time.perf_counter() - meta["generation_started"]) * 1000.0,
                        notes=["generation_resource_missing_from_adapter"],
                    )
                if meta.get("evaluator_resource") is None:
                    meta["evaluator_resource"] = evaluator_usage(
                        wall_time_ms=0.0,
                        evaluator_cost=0.0,
                        evaluator_id="FORGE_EVALUATOR",
                        calls=0,
                        notes=["evaluator_not_invoked"],
                    )
                ledger.finish_attempt(
                    meta["attempt_id"],
                    status=meta["status"],
                    candidate_hash=(candidate_sha256(meta["candidate"])
                                    if meta["candidate"] is not None else None),
                    score=meta["score"],
                    error_class=meta["error_class"],
                    resource_usage=meta["generation_resource"],
                    evaluator_resource_usage=meta["evaluator_resource"],
                    metadata=(
                        {
                            **lineage_metadata(meta["candidate"], meta.get("parents", [])),
                            "prompt_sha256": meta.get("prompt_sha256"),
                            "prompt_profile": meta.get("prompt_profile"),
                            "temperature": meta.get("temperature"),
                            "evaluator_hack_audit": meta.get("evaluator_hack_audit", {
                                "parseable": False,
                                "suspected_hack": False,
                                "findings": [],
                            }),
                        }
                        if meta["status"] == "valid_candidate" and meta["candidate"] is not None
                        else {
                            "parent_count": len(meta.get("parents", [])),
                            "prompt_sha256": meta.get("prompt_sha256"),
                            "prompt_profile": meta.get("prompt_profile"),
                            "temperature": meta.get("temperature"),
                            "evaluator_hack_audit": meta.get("evaluator_hack_audit", {
                                "parseable": False,
                                "suspected_hack": False,
                                "findings": [],
                            }),
                        }
                    ),
                )
                if (meta["status"] == "valid_candidate"
                        and meta["score"] is not None
                        and meta["score"] > incumbent_score):
                    incumbent_text = meta["candidate"]
                    incumbent_score = meta["score"]
                if incumbent_text is None:
                    raise LedgerError("no incumbent available for attempt checkpoint")
                ledger.record_event("incumbent_selected", {
                    "attempt_id": meta["attempt_id"],
                    "after_attempt": meta["attempt_index"],
                    "candidate_sha256": candidate_sha256(incumbent_text),
                    "score": incumbent_score,
                })
            ledger.assert_invariants()

        if protocol_v3 and stop_after_generation:
            print(f"[gen {gen}] cheap層が{dead_generations}世代連続で全滅。終了。")
            stopped_by = "llm_unavailable"
            generations_done = gen
            break

        # --- 島の入れ替え(FunSearch型): 弱い島を強い島の解で作り直す ---
        migrate_every = cfg.get("migrate_every", 0)
        if n_islands > 1 and migrate_every and gen % migrate_every == 0:
            ranked = sorted(
                (a for a in archives if a.items),
                key=lambda a: -a.best["score"],
            )
            if len(ranked) > 1:
                strong = ranked[: max(1, len(ranked) // 2)]
                weak = ranked[max(1, len(ranked) // 2):]
                for a in weak:
                    donor = rng.choice(strong)
                    a.reset_to(dict(donor.best, gen=gen))
                print(f"[gen {gen}] 島の入れ替え: 弱い{len(weak)}島を作り直した")

        live = [a for a in archives if a.items]
        best = max((a.best for a in live), key=lambda c: c["score"])
        distinct = len({c["score"] for a in live for c in a.items})
        # scores= は親プール内の異なるスコア数。1〜数種類に潰れたら探索は閉ループに
        # 入っている(dup率は低いままなので、dup率だけ見ていても気づけない)。
        island_note = f" islands={n_islands}" if n_islands > 1 else ""
        print(f"[gen {gen}] batch={n} dup={dup} alive={len(survivors)} "
              f"best={best['score']:.4f} scores={distinct} alarm={alarm}{island_note} "
              f"budget={budget.cheap_used}/{budget.max_cheap}")
        generations_done = gen

        # --- V2: Nエポックに1回だけ賢いモデルに指針を買いに行く ---
        if smart and gen % cfg.get("reflect_every", 5) == 0 and live:
            top = sorted((c for a in live for c in a.items),
                         key=lambda c: -c["score"])[:3]
            if protocol_v3:
                guidance, reflection_resource = v2_reflect(
                    problem, top, smart, budget, return_resource=True
                )
                if reflection_resource is not None:
                    ledger.record_evaluation(
                        f"{ledger.run_id}:reflection:{gen}",
                        resource_usage=reflection_resource,
                        allow_unbound=True,
                        status="reflection_completed",
                    )
            else:
                guidance = v2_reflect(problem, top, smart, budget)
            if guidance:
                print(f"[gen {gen}] 審査員指針: {guidance[:100]}...")
                h = simhash(guidance)
                if prev_guidance_hash is not None and hamming(h, prev_guidance_hash) <= 3:
                    print(f"[gen {gen}] 審査員指針が収束。反省チャネルを停止。")
                    smart = None
                else:
                    prev_guidance_hash = h

    live = [a for a in archives if a.items]
    overall = max((a.best for a in live), key=lambda c: c["score"])
    print(f"\n=== BEST (score={overall['score']:.4f}) ===\n{overall['text']}")
    with open(f"{run_dir}/result.json", "w", encoding="utf-8") as f:
        result_payload = {
            "archive_distinct_scores": len({c["score"] for a in live for c in a.items}),
            "best_score": overall["score"],
            "islands": n_islands,
            "cheap_failed": cheap_failed,
            "generations_done": generations_done,
            "cheap_used": budget.cheap_used,
            "smart_used": budget.smart_used,
            "wall_secs": time.time() - t0,
            "stopped_by": stopped_by,
        }
        if protocol_v3:
            ledger.assert_invariants()
            ledger_summary = ledger.summary()
            result_payload["attempt_count"] = ledger.attempt_count
            result_payload["attempt_cap"] = attempt_cap
            result_payload["track"] = study_track
            # A V3 run may be promoted to a registered study artifact only
            # when these identity fields are supplied by the frozen study
            # configuration.  Mock/dev runs may omit them, but the public
            # bundle verifier will fail closed rather than infer them.
            identity_fields = (
                "study_id", "study_version", "run_id", "method_id",
                "problem_id", "problem_family", "distribution", "model_tier",
                "seed", "seed_role",
            )
            if all(field in cfg for field in identity_fields):
                for field in identity_fields:
                    result_payload[field] = cfg[field]
                result_payload["run_identity_sha256"] = result_identity_sha256(result_payload)
            result_payload["native_a100_gpu_seconds_cap"] = int(NATIVE_GPU_SECONDS)
            result_payload["gpu_anytime_curve"] = []
            result_payload["auc_gpu"] = None
            result_payload["gpu_auc_status"] = (
                "not_applicable" if study_track == "SAME_MODEL" else "pending_unblinding"
            )
            result_payload["event_ledger_path"] = f"{run_dir}/events.jsonl"
            result_payload["event_ledger_head_hash"] = ledger_summary["head_hash"]
            result_payload["decision_hash"] = replay_decision_hash(
                f"{run_dir}/events.jsonl"
            )
            result_payload["result_recomputation_hash"] = replay_result_hash(
                f"{run_dir}/events.jsonl"
            )
            result_payload["event_ledger_status_counts"] = ledger_summary["status_counts"]
            result_payload["resource_summary"] = ledger_summary["resource_summary"]
            result_payload["resource_ledger_hash"] = ledger_summary["resource_ledger_hash"]
            result_payload["generation_budget"] = {
                "attempts": ledger.attempt_count,
                "max_attempts": attempt_cap,
                "cheap_calls": budget.cheap_used,
                "max_cheap_calls": budget.max_cheap,
            }
            result_payload["evaluator_budget"] = {
                "calls": budget.evaluator_used if budget.evaluator_separate else None,
                "max_calls": budget.max_evaluator,
                "separate_from_generation": budget.evaluator_separate,
            }
            result_payload["candidate_ast_hash_coverage"] = ledger_summary["candidate_ast_hash_coverage"]
            result_payload["accepted_candidate_diff_coverage"] = ledger_summary["accepted_candidate_diff_coverage"]
            for coverage_field in (
                "trace_parent_child_links_complete",
                "parent_child_link_coverage",
                "deterministic_cycle_detection_coverage",
                "lineage_cycle_count",
                "evaluator_hack_audit_coverage",
            ):
                result_payload[coverage_field] = ledger_summary[coverage_field]
            if controller is not None:
                result_payload["controller_mechanism_id"] = controller.mechanism_id
                result_payload["controller_policy_sha256"] = controller.policy_sha256
                result_payload["controller_training_problem_ids"] = list(
                    controller.training_problem_ids
                )
                result_payload["controller_actions"] = controller_actions
                result_payload["controller_holdout_update_attempts"] = controller.holdout_update_attempts
        # Persist the deterministic development metrics alongside the run
        # result.  This is derived only from the visible archive and result
        # counters; it never invokes an evaluator or consults hidden data.
        result_payload["metrics"] = run_metrics(
            archive_path,
            result=result_payload,
            archive_label="archive.jsonl",
        )
        json.dump(result_payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    return overall


def _score_candidate(problem, cand: str, *, include_resource: bool,
                     include_diagnostic: bool = False):
    started = time.perf_counter()
    if include_diagnostic:
        score, alive, failure_status, error_class = v0_diagnostic(problem, cand)
    else:
        score, alive = v0(problem, cand)
        failure_status, error_class = ("valid_candidate" if alive else "runtime_error"), None
    if not include_resource:
        if include_diagnostic:
            return cand, score, alive, failure_status, error_class
        return cand, score, alive
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    result = (
        cand,
        score,
        alive,
        evaluator_usage(
            wall_time_ms=elapsed_ms,
            evaluator_cost=elapsed_ms / 1000.0,
            evaluator_id="FORGE_EVALUATOR",
        ),
    )
    if include_diagnostic:
        return (*result, failure_status, error_class)
    return result


def score_candidates(problem, candidates: list[str], workers: int,
                     *, include_resource: bool = False,
                     include_diagnostic: bool = False):
    if workers <= 1 or len(candidates) <= 1:
        return [
            _score_candidate(
                problem, cand, include_resource=include_resource,
                include_diagnostic=include_diagnostic,
            )
            for cand in candidates
        ]

    max_workers = min(workers, len(candidates))
    with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        return list(ex.map(
            lambda cand: _score_candidate(
                problem, cand, include_resource=include_resource,
                include_diagnostic=include_diagnostic,
            ),
            candidates,
        ))


def _safe(fn, prompt, temp):
    try:
        return fn(prompt, temperature=temp)
    except Exception as e:
        print(f"[warn] call failed: {e}")
        return ""


def _safe_detailed(fn, prompt, temp):
    """Return a response plus an explicit failure class for the V3 ledger."""
    started = time.perf_counter()
    try:
        detailed = getattr(fn, "with_metadata", None)
        raw = detailed(prompt, temperature=temp) if callable(detailed) else fn(
            prompt, temperature=temp
        )
        if isinstance(raw, dict):
            text = raw.get("text", "") or ""
            usage = raw.get("resource_usage")
        else:
            text = raw or ""
            usage = None
        try:
            usage = normalize_usage(usage)
        except ValueError:
            usage = empty_usage(notes=["adapter_resource_usage_invalid"])
        # Adapters may provide API wall time.  A plain legacy/fake caller does
        # not, so the wrapper's monotonic measurement is the observed value.
        if usage.get("wall_time_ms") is None:
            # ``missing`` and ``telemetry_complete`` are derived fields and
            # must be recomputed after adding the wrapper observation.
            usage.pop("missing", None)
            usage.pop("telemetry_complete", None)
            usage["wall_time_ms"] = (time.perf_counter() - started) * 1000.0
            usage = normalize_usage(usage)
        return {"text": text, "error_class": None, "resource_usage": usage}
    except Exception as e:
        print(f"[warn] call failed: {e}")
        return {
            "text": "",
            "error_class": type(e).__name__,
            "resource_usage": generation_usage(
                wall_time_ms=(time.perf_counter() - started) * 1000.0,
                notes=["generation_call_failed", type(e).__name__],
            ),
        }
