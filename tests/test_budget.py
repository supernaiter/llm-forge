from forge.llm import Budget


def test_zero_cheap_budget_disables_cheap_calls():
    budget = Budget(0, 1)

    assert not budget.can("cheap")
    assert budget.can("smart")
