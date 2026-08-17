import pytest

from forge.holdout import HiddenTestAccessError, SealedHoldout, VerifierAuthority


def test_search_view_has_metadata_but_no_hidden_content_or_scores():
    holdout = SealedHoldout(
        problem_id="h01",
        family="synthetic",
        distribution="distribution_shift",
        hidden_instances=[{"secret": 1}, {"secret": 2}],
    )
    view = holdout.search_view
    assert view.problem_id == "h01"
    assert view.hidden_instance_count == 0
    with pytest.raises(HiddenTestAccessError):
        holdout.hidden_instances()
    with pytest.raises(HiddenTestAccessError):
        holdout.hidden_scores("candidate")
    assert holdout.hidden_access_violation_count == 2
    assert holdout.audit()["search_side_scores_exposed"] is False


def test_only_verifier_capability_can_unblind_once():
    authority = VerifierAuthority()
    holdout = SealedHoldout(
        problem_id="h02",
        family="external",
        distribution="iid_heldout",
        hidden_instances=[2, 3],
        authority=authority,
    )
    with pytest.raises(HiddenTestAccessError):
        holdout.evaluate_after_unblinding("wrong", 4, lambda c, x: c + x)
    with pytest.raises(HiddenTestAccessError):
        holdout.issue_unblinding_capability()
    with pytest.raises(HiddenTestAccessError):
        holdout.issue_unblinding_capability(authority=VerifierAuthority())
    capability = authority.issue_unblinding_capability(holdout)
    assert holdout.evaluate_after_unblinding(capability, 4, lambda c, x: c + x) == (6.0, 7.0)
    with pytest.raises(HiddenTestAccessError):
        authority.issue_unblinding_capability(holdout)


def test_hidden_evaluator_failure_and_nonfinite_score_fail_closed():
    authority = VerifierAuthority()
    holdout = SealedHoldout(
        problem_id="h03",
        family="external",
        distribution="size_shift",
        hidden_instances=[1],
        authority=authority,
    )
    capability = authority.issue_unblinding_capability(holdout)
    with pytest.raises(HiddenTestAccessError, match="non-finite"):
        holdout.evaluate_after_unblinding(capability, 4, lambda c, x: float("nan"))
    assert holdout.hidden_access_violation_count == 1

    authority = VerifierAuthority()
    holdout = SealedHoldout(
        problem_id="h04",
        family="external",
        distribution="distribution_shift",
        hidden_instances=[1],
        authority=authority,
    )
    capability = authority.issue_unblinding_capability(holdout)
    with pytest.raises(HiddenTestAccessError, match="evaluator failed"):
        holdout.evaluate_after_unblinding(
            capability, 4, lambda c, x: (_ for _ in ()).throw(RuntimeError("broken"))
        )
    assert holdout.hidden_access_violation_count == 1
