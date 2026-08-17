"""検証カスケード。原則: 上の階層で殺せるものを下の階層に流したら負け。
  V0: 決定論的採点（Problem.score）。必須。無料。ここで99%を棄却する。
  V1: 安価LLM judge。Problemが judge_prompt を定義した場合のみ。形式化不能な目的関数の近似。
  V2: 賢いモデルの反省。生き残った上位kにのみ、Nエポックに1回。出力は「次世代への指針」テキスト。
V0が書けない問題はこのハーネスに載せるべきではない（前提が崩れている）。"""
from __future__ import annotations
import re
import time
import math
from .llm import Budget
from .ledger import ATTEMPT_STATUSES
from .resources import evaluator_usage, normalize_usage


def extract_block(text: str) -> str:
    m = re.findall(r"```(?:\w*)\n(.*?)```", text, re.S)
    return (m[-1] if m else text).strip()


def v0(problem, candidate: str):
    """(score: float, alive: bool) を返す。例外＝死。
    ただし fatal=True 印付き例外(計測器自体の故障)は再送出してランを止める。
    候補死とインフラ故障を混同すると、壊れたクリップがCER 1.0のまま
    全世代を片肺で走る沈黙故障になる。"""
    try:
        return problem.score(candidate)
    except Exception as e:
        if getattr(e, "fatal", False):
            raise
        return float("-inf"), False


def v0_diagnostic(problem, candidate: str):
    """Return ``(score, alive, status, error_class)`` for V3 accounting.

    Problem packs may expose ``score_with_status`` when they can distinguish
    syntax, policy, timeout, and evaluator failures.  Legacy packs retain the
    two-value ``score`` API; their failed candidates are conservatively marked
    ``runtime_error`` rather than being silently counted as valid.
    """
    scorer = getattr(problem, "score_with_status", None)
    if callable(scorer):
        try:
            result = scorer(candidate)
        except Exception as exc:  # a pack failure is a candidate failure here
            return float("-inf"), False, "runtime_error", type(exc).__name__
        if not isinstance(result, tuple) or len(result) not in {3, 4}:
            raise TypeError("score_with_status must return (score, alive, status[, error_class])")
        score, alive, status = result[:3]
        error_class = result[3] if len(result) == 4 else None
        if not isinstance(alive, bool) or not isinstance(status, str):
            raise TypeError("score_with_status returned invalid alive/status values")
        if status not in ATTEMPT_STATUSES - {"started"}:
            return float("-inf"), False, "runtime_error", "UnknownStatus"
        if alive and status != "valid_candidate":
            return float("-inf"), False, "runtime_error", "InconsistentStatus"
        if not alive and status == "valid_candidate":
            return float("-inf"), False, "runtime_error", "InconsistentStatus"
        if alive and (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(float(score))
        ):
            return float("-inf"), False, "constraint_violation", "NonFiniteScore"
        return score, alive, status, error_class
    try:
        score, alive = problem.score(candidate)
    except Exception as exc:
        return float("-inf"), False, "runtime_error", type(exc).__name__
    status = "valid_candidate" if alive else "runtime_error"
    if alive and (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(float(score))
    ):
        return float("-inf"), False, "constraint_violation", "NonFiniteScore"
    return score, alive, status, None


def v1(problem, candidate: str, cheap, budget: Budget, *, return_resource: bool = False):
    tier = "evaluator" if budget.evaluator_separate else "cheap"
    if not hasattr(problem, "judge_prompt") or not budget.can(tier):
        return (True, None) if return_resource else True
    budget.spend(tier)
    started = time.perf_counter()
    try:
        detailed = getattr(cheap, "with_metadata", None)
        raw = detailed(problem.judge_prompt(candidate), temperature=0.0) \
            if callable(detailed) else cheap(problem.judge_prompt(candidate), temperature=0.0)
    except Exception as exc:
        resource = evaluator_usage(
            wall_time_ms=(time.perf_counter() - started) * 1000.0,
            evaluator_cost=(time.perf_counter() - started),
            evaluator_id="FORGE_EVALUATOR",
            calls=1,
            notes=["evaluator_call_failed", type(exc).__name__],
        )
        return (False, resource) if return_resource else False
    if isinstance(raw, dict):
        out = raw.get("text", "") or ""
        observed = raw.get("resource_usage")
    else:
        out = raw or ""
        observed = None
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    observed = dict(observed) if isinstance(observed, dict) else {}
    # Recompute derived fields after adding evaluator-specific observations.
    observed.pop("missing", None)
    observed.pop("telemetry_complete", None)
    observed["wall_time_ms"] = observed.get("wall_time_ms") or elapsed_ms
    observed["evaluator_cost"] = elapsed_ms / 1000.0
    observed["evaluator_cost_unit"] = "wall_seconds"
    observed["evaluator_calls"] = 1
    observed["model_identity"] = observed.get("model_identity") or "FORGE_EVALUATOR"
    try:
        resource = normalize_usage(observed)
    except ValueError:
        resource = evaluator_usage(
            wall_time_ms=elapsed_ms,
            evaluator_cost=elapsed_ms / 1000.0,
            evaluator_id="FORGE_EVALUATOR",
            notes=["evaluator_adapter_resource_usage_invalid"],
        )
    last = out.strip().splitlines()[-1].upper() if out.strip() else ""
    passed = "PASS" in last and "FAIL" not in last
    return (passed, resource) if return_resource else passed


def v2_reflect(problem, top: list[dict], smart, budget: Budget, *, return_resource: bool = False):
    """賢いモデルは発明者ではなく審査員。上位解を見せて戦略的指針だけ買う。"""
    if not budget.can("smart"):
        return ("", None) if return_resource else ""
    if budget.evaluator_separate and not budget.can("evaluator"):
        return ("", None) if return_resource else ""
    budget.spend("smart")
    if budget.evaluator_separate:
        budget.spend("evaluator")
    listing = "\n\n".join(f"[score={c['score']:.4f}]\n{c['text']}" for c in top)
    prompt = (
        f"You are the judge of a search process. Here are the current top solutions.\n{listing}\n\n"
        f"Problem: {problem.DESCRIPTION}\n"
        "In 3 lines or fewer, state the weaknesses shared by the top solutions and the "
        "mutation direction to try next. Do not write a solution yourself."
    )
    started = time.perf_counter()
    detailed = getattr(smart, "with_metadata", None)
    try:
        raw = detailed(prompt, temperature=0.3) if callable(detailed) else smart(
            prompt, temperature=0.3
        )
    except Exception as exc:
        resource = evaluator_usage(
            wall_time_ms=(time.perf_counter() - started) * 1000.0,
            evaluator_cost=(time.perf_counter() - started),
            evaluator_id="FORGE_REFLECTION",
            calls=1,
            notes=["reflection_call_failed", type(exc).__name__],
        )
        return ("", resource) if return_resource else ""
    if isinstance(raw, dict):
        text = raw.get("text", "") or ""
        observed = raw.get("resource_usage")
    else:
        text = raw or ""
        observed = None
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    observed = dict(observed) if isinstance(observed, dict) else {}
    observed.pop("missing", None)
    observed.pop("telemetry_complete", None)
    observed["wall_time_ms"] = observed.get("wall_time_ms") or elapsed_ms
    observed["evaluator_cost"] = elapsed_ms / 1000.0
    observed["evaluator_cost_unit"] = "wall_seconds"
    observed["evaluator_calls"] = 1
    observed["model_identity"] = observed.get("model_identity") or "FORGE_REFLECTOR"
    try:
        resource = normalize_usage(observed)
    except ValueError:
        resource = evaluator_usage(
            wall_time_ms=elapsed_ms,
            evaluator_cost=elapsed_ms / 1000.0,
            evaluator_id="FORGE_REFLECTOR",
            notes=["reflection_adapter_resource_usage_invalid"],
        )
    return (text, resource) if return_resource else text
