from forge.llm import Budget
from forge.codecheck import audit_candidate
from forge.verify import v0_diagnostic, v1


class _Problem:
    DESCRIPTION = "test problem"

    def judge_prompt(self, cand: str) -> str:
        return f"judge: {cand}"


def test_v1_passes_when_last_line_is_pass():
    budget = Budget(max_cheap_calls=5, max_smart_calls=0)

    def cheap(prompt: str, temperature: float) -> str:
        return "reasoning that mentions FAIL as a possibility\nPASS"

    assert v1(_Problem(), "cand", cheap, budget)


def test_v1_fails_when_last_line_is_fail():
    budget = Budget(max_cheap_calls=5, max_smart_calls=0)

    def cheap(prompt: str, temperature: float) -> str:
        return "reasoning\nFAIL"

    assert not v1(_Problem(), "cand", cheap, budget)


def test_v1_fails_when_last_line_contains_both_words():
    budget = Budget(max_cheap_calls=5, max_smart_calls=0)

    def cheap(prompt: str, temperature: float) -> str:
        return "PASS/FAIL template line"

    assert not v1(_Problem(), "cand", cheap, budget)


def test_v1_evaluator_failure_is_rejection_with_resource_record():
    budget = Budget(max_cheap_calls=5, max_smart_calls=0, max_evaluator_calls=5)

    def cheap(prompt: str, temperature: float) -> str:
        raise RuntimeError("judge unavailable")

    passed, resource = v1(_Problem(), "cand", cheap, budget, return_resource=True)
    assert passed is False
    assert resource["evaluator_calls"] == 1
    assert "evaluator_call_failed" in resource["telemetry_notes"]


def test_v2_reflection_failure_is_recorded_without_raising():
    from forge.verify import v2_reflect
    budget = Budget(max_cheap_calls=5, max_smart_calls=1, max_evaluator_calls=5)

    def smart(prompt: str, temperature: float) -> str:
        raise RuntimeError("reflection unavailable")

    guidance, resource = v2_reflect(
        _Problem(), [{"score": 1.0, "text": "candidate"}], smart, budget,
        return_resource=True,
    )
    assert guidance == ""
    assert resource["evaluator_calls"] == 1
    assert "reflection_call_failed" in resource["telemetry_notes"]


def test_v0_diagnostic_preserves_pack_failure_classification():
    class Pack:
        def score_with_status(self, candidate):
            return float("-inf"), False, "sandbox_rejected", "CodeRejected"

    assert v0_diagnostic(Pack(), "candidate") == (
        float("-inf"), False, "sandbox_rejected", "CodeRejected"
    )


def test_v0_diagnostic_legacy_pack_fails_closed_as_runtime_error():
    class Legacy:
        def score(self, candidate):
            return float("-inf"), False

    assert v0_diagnostic(Legacy(), "candidate")[2:] == ("runtime_error", None)


def test_v0_diagnostic_rejects_nonfinite_or_inconsistent_pack_scores():
    class NonFinite:
        def score_with_status(self, candidate):
            return float("inf"), True, "valid_candidate", None

    class Inconsistent:
        def score_with_status(self, candidate):
            return 1.0, True, "runtime_error", "BadStatus"

    assert v0_diagnostic(NonFinite(), "candidate")[2:] == (
        "constraint_violation", "NonFiniteScore"
    )
    assert v0_diagnostic(Inconsistent(), "candidate")[2:] == (
        "runtime_error", "InconsistentStatus"
    )


def test_candidate_hack_audit_is_static_and_deterministic():
    clean = audit_candidate("def f(x):\n    return x + 1\n")
    assert clean["suspected_hack"] is False
    flagged = audit_candidate("def f(x):\n    return open('hidden_score_file')\n")
    assert flagged["suspected_hack"] is True
    assert flagged == audit_candidate("def f(x):\n    return open('hidden_score_file')\n")
