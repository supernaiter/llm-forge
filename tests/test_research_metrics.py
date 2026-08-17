import pytest

from forge.research_metrics import (
    MetricError,
    auc_attempt,
    auc_gpu,
    champion_delta_statistic,
    final_normalized_quality,
    hierarchical_bootstrap,
    normalized_anytime_curve,
    normalized_quality,
    ood_delta_statistic,
    ood_drop_statistic,
    oracle_delta_statistic,
    percentile_interval,
)


def test_normalized_attempt_auc_carries_forward_incumbent():
    scores = {"seed": 10.0, "new": [20.0, 22.0]}
    curve = normalized_anytime_curve(
        ["seed", None, "new"], scores,
        seed_reference=10.0, fixed_reference=30.0,
    )
    assert curve == [0.0, 0.0, 0.55]
    assert auc_attempt(["seed", None, "new"], scores,
                       seed_reference=10.0, fixed_reference=30.0) == pytest.approx(0.1833333)


def test_gpu_auc_and_degenerate_anchor_fail_closed():
    scores = {"a": 2.0, "b": 4.0}
    assert auc_gpu({0.05: "a", 0.1: "b"}, scores,
                   seed_reference=0.0, fixed_reference=4.0,
                   fractions=[0.05, 0.1]) == pytest.approx(0.75)
    with pytest.raises(MetricError):
        normalized_quality(1.0, 1.0, 1.0)
    with pytest.raises(MetricError):
        normalized_quality(float("nan"), 0.0, 1.0)
    with pytest.raises(MetricError):
        normalized_quality(1.0, float("inf"), 2.0)
    with pytest.raises(MetricError, match="strictly increasing"):
        auc_gpu({0.1: "a"}, scores, seed_reference=0.0, fixed_reference=4.0,
                fractions=[0.1, 0.1])
    with pytest.raises(MetricError, match="numeric sequence"):
        auc_gpu({}, scores, seed_reference=0.0, fixed_reference=4.0, fractions=[])


def test_hidden_score_vectors_reject_boolean_nonfinite_and_non_numeric_values():
    kwargs = {
        "seed_reference": 0.0,
        "fixed_reference": 1.0,
    }
    with pytest.raises(MetricError, match="must be numeric"):
        normalized_anytime_curve(["candidate"], {"candidate": True}, **kwargs)
    with pytest.raises(MetricError, match="finite number"):
        normalized_anytime_curve(["candidate"], {"candidate": [0.5, float("nan")]}, **kwargs)
    with pytest.raises(MetricError, match="finite number"):
        normalized_anytime_curve(["candidate"], {"candidate": [0.5, "bad"]}, **kwargs)


def test_final_champion_and_ood_metrics_are_distinct_from_best_score():
    scores = {"seed": 10.0, "new": 20.0}
    assert final_normalized_quality(
        ["seed", None, "new"], scores,
        seed_reference=10.0, fixed_reference=30.0,
    ) == pytest.approx(0.5)
    rows = [
        {"forge": 0.8, "champion": 0.6},
        {"forge": 0.7, "champion": 0.5},
    ]
    assert champion_delta_statistic(rows) == pytest.approx(0.2)
    ood = [
        {"distribution": "iid_heldout", "forge": 0.8, "baselines": {"b": 0.5}},
        {"distribution": "size_shift", "forge": 0.6, "baselines": {"b": 0.4}},
        {"distribution": "distribution_shift", "forge": 0.5, "baselines": {"b": 0.3}},
    ]
    assert ood_delta_statistic(ood[1:]) == pytest.approx(0.2)
    assert ood_drop_statistic(ood) == pytest.approx(0.25)
    with pytest.raises(MetricError):
        ood_drop_statistic(ood[:2])


def test_hierarchical_bootstrap_is_seeded_and_oracle_is_cellwise():
    rows = [
        {"problem_family": "f1", "problem": "p1", "seed": 1, "cluster": 1,
         "forge": 0.8, "baselines": {"b1": 0.4, "b2": 0.5}},
        {"problem_family": "f1", "problem": "p1", "seed": 1, "cluster": 2,
         "forge": 0.6, "baselines": {"b1": 0.7, "b2": 0.2}},
        {"problem_family": "f2", "problem": "p2", "seed": 2, "cluster": 1,
         "forge": 0.5, "baselines": {"b1": 0.1, "b2": 0.3}},
    ]
    assert oracle_delta_statistic(rows) == pytest.approx((0.3 - 0.1 + 0.2) / 3)
    first = hierarchical_bootstrap(rows, oracle_delta_statistic, replicates=40, seed=9)
    second = hierarchical_bootstrap(rows, oracle_delta_statistic, replicates=40, seed=9)
    assert first == second
    assert percentile_interval(first)[0] <= percentile_interval(first)[1]


def test_hierarchical_bootstrap_rejects_ambiguous_seed_types():
    rows = [{
        "problem_family": "f", "problem": "p", "seed": True,
        "hidden_test_instance_cluster": "c", "forge": 0.0,
        "baselines": {"b": 0.0},
    }]
    with pytest.raises(MetricError, match="seed must be an integer"):
        hierarchical_bootstrap(rows, oracle_delta_statistic, replicates=1, seed=1)


def test_hierarchical_bootstrap_rejects_invalid_replicate_and_rng_types():
    rows = [{
        "problem_family": "f", "problem": "p", "seed": 1,
        "hidden_test_instance_cluster": "c", "forge": 0.0,
        "baselines": {"b": 0.0},
    }]
    with pytest.raises(MetricError, match="replicates must be a positive integer"):
        hierarchical_bootstrap(rows, oracle_delta_statistic, replicates=True, seed=1)
    with pytest.raises(MetricError, match="replicates must be a positive integer"):
        hierarchical_bootstrap(rows, oracle_delta_statistic, replicates=1.5, seed=1)
    with pytest.raises(MetricError, match="seed must be an integer"):
        hierarchical_bootstrap(rows, oracle_delta_statistic, replicates=1, seed=True)
    with pytest.raises(MetricError, match="seed must be an integer"):
        hierarchical_bootstrap(rows, oracle_delta_statistic, replicates=1, seed=1.5)


def test_hierarchical_bootstrap_keeps_rows_in_same_hidden_cluster_together():
    rows = [
        {"problem_family": "f", "problem": "p", "seed": 1,
         "hidden_test_instance_cluster": "c1", "forge": 1.0,
         "baselines": {"b": 0.0}},
        {"problem_family": "f", "problem": "p", "seed": 1,
         "hidden_test_instance_cluster": "c1", "forge": 3.0,
         "baselines": {"b": 0.0}},
        {"problem_family": "f", "problem": "p", "seed": 1,
         "hidden_test_instance_cluster": "c2", "forge": 5.0,
         "baselines": {"b": 0.0}},
    ]
    # One replicate is enough to exercise the four-level sampler.  A sampled
    # c1 cluster contributes both paired rows, never just one of them.
    def statistic(sample):
        c1_count = sum(
            row["hidden_test_instance_cluster"] == "c1" for row in sample
        )
        assert c1_count in {0, 2, 4}
        return sum(row["forge"] for row in sample) / len(sample)

    samples = hierarchical_bootstrap(rows, statistic, replicates=1, seed=4)
    assert len(samples) == 1


def test_percentile_interval_rejects_non_numeric_or_out_of_range_bounds():
    with pytest.raises(MetricError, match="lower percentile"):
        percentile_interval([0.0, 1.0], lower=True)
    with pytest.raises(MetricError, match="upper percentile"):
        percentile_interval([0.0, 1.0], upper="0.9")
    with pytest.raises(MetricError, match="invalid percentile interval"):
        percentile_interval([0.0, 1.0], lower=-0.1)
