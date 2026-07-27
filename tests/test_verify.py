from forge.llm import Budget
from forge.verify import v1


class _Problem:
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
