"""Research V3 metrics and paired hierarchical bootstrap utilities."""
from __future__ import annotations

import random
import statistics
import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any


class MetricError(ValueError):
    """Raised when a research metric cannot be computed without guessing."""


def _finite_metric(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MetricError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise MetricError(f"{field} must be a finite number")
    return result


def normalized_quality(score: float, seed_reference: float,
                       fixed_reference: float) -> float:
    """Normalize without clipping and reject degenerate anchors."""
    score = _finite_metric(score, "score")
    seed_reference = _finite_metric(seed_reference, "seed_reference")
    fixed_reference = _finite_metric(fixed_reference, "fixed_reference")
    denominator = fixed_reference - seed_reference
    if denominator == 0:
        raise MetricError("normalization anchors are identical")
    return _finite_metric(
        (score - seed_reference) / denominator,
        "normalized quality",
    )


def _candidate_score(hidden_scores: Mapping[str, float | Sequence[float]], candidate: str) -> float:
    if candidate not in hidden_scores:
        raise MetricError(f"missing hidden score for candidate: {candidate}")
    value = hidden_scores[candidate]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _finite_metric(value, f"hidden score for candidate {candidate}")
    if isinstance(value, bool):
        raise MetricError(f"hidden score for candidate {candidate} must be numeric")
    if isinstance(value, (str, bytes)):
        raise MetricError(f"hidden score vector for candidate {candidate} must be numeric")
    values = list(value)
    if not values:
        raise MetricError(f"empty hidden score vector for candidate: {candidate}")
    normalized = [
        _finite_metric(item, f"hidden score for candidate {candidate}")
        for item in values
    ]
    return statistics.fmean(normalized)


def normalized_anytime_curve(
    selected_by_attempt: Sequence[str | None],
    hidden_scores: Mapping[str, float | Sequence[float]],
    *,
    seed_reference: float,
    fixed_reference: float,
) -> list[float]:
    """Evaluate selected incumbents by attempt using search-side selection only."""
    if not selected_by_attempt:
        raise MetricError("empty selected incumbent curve")
    out: list[float] = []
    last: str | None = None
    for candidate in selected_by_attempt:
        if candidate is not None:
            last = candidate
        if last is None:
            raise MetricError("incumbent curve has no candidate before a checkpoint")
        out.append(normalized_quality(
            _candidate_score(hidden_scores, last), seed_reference, fixed_reference
        ))
    return out


def auc_attempt(selected_by_attempt: Sequence[str | None],
                hidden_scores: Mapping[str, float | Sequence[float]], *,
                seed_reference: float, fixed_reference: float) -> float:
    curve = normalized_anytime_curve(
        selected_by_attempt, hidden_scores,
        seed_reference=seed_reference, fixed_reference=fixed_reference,
    )
    return statistics.fmean(curve)


def final_normalized_quality(
    selected_by_attempt: Sequence[str | None],
    hidden_scores: Mapping[str, float | Sequence[float]],
    *,
    seed_reference: float,
    fixed_reference: float,
) -> float:
    """Return the normalized hidden-test quality at the final checkpoint.

    The final checkpoint is the last selected incumbent after carry-forward;
    it is deliberately separate from ``auc_attempt`` so callers cannot
    substitute a single best score for the registered anytime endpoint.
    """
    curve = normalized_anytime_curve(
        selected_by_attempt, hidden_scores,
        seed_reference=seed_reference, fixed_reference=fixed_reference,
    )
    return curve[-1]


def auc_gpu(selected_by_fraction: Mapping[float, str | None],
            hidden_scores: Mapping[str, float | Sequence[float]], *,
            seed_reference: float, fixed_reference: float,
            fractions: Sequence[float]) -> float:
    if isinstance(fractions, (str, bytes)) or not isinstance(fractions, Sequence) or not fractions:
        raise MetricError("GPU fractions must be a non-empty numeric sequence")
    previous = None
    for index, fraction in enumerate(fractions):
        value = _finite_metric(fraction, f"GPU fraction {index}")
        if value < 0 or value > 1:
            raise MetricError("GPU fractions must lie in [0, 1]")
        if previous is not None and value <= previous:
            raise MetricError("GPU fractions must be strictly increasing")
        previous = value
    selected = [selected_by_fraction.get(fraction) for fraction in fractions]
    return auc_attempt(
        selected, hidden_scores,
        seed_reference=seed_reference, fixed_reference=fixed_reference,
    )


def champion_delta_statistic(rows: Sequence[Mapping[str, Any]]) -> float:
    """Return Forge minus the single development-frozen champion baseline."""
    if not rows:
        raise MetricError("empty champion rows")
    deltas = []
    for row in rows:
        if "forge" not in row or "champion" not in row:
            raise MetricError("champion row needs forge and champion values")
        deltas.append(
            _finite_metric(row["forge"], "forge")
            - _finite_metric(row["champion"], "champion")
        )
    return statistics.fmean(deltas)


def ood_delta_statistic(rows: Sequence[Mapping[str, Any]]) -> float:
    """Return Forge minus the cellwise oracle on explicitly OOD rows."""
    if not rows:
        raise MetricError("empty OOD rows")
    for row in rows:
        if row.get("distribution") not in {"size_shift", "distribution_shift"}:
            raise MetricError("OOD row has a non-OOD distribution")
    return oracle_delta_statistic(rows)


def ood_drop_statistic(rows: Sequence[Mapping[str, Any]]) -> float:
    """Return IID quality minus the mean quality over the two OOD shifts."""
    if not rows:
        raise MetricError("empty OOD drop rows")
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        distribution = row.get("distribution")
        if distribution not in {"iid_heldout", "size_shift", "distribution_shift"}:
            raise MetricError(f"unknown distribution: {distribution}")
        if "forge" not in row:
            raise MetricError("OOD drop row needs forge value")
        grouped[str(distribution)].append(_finite_metric(row["forge"], "forge"))
    required = {"iid_heldout", "size_shift", "distribution_shift"}
    if set(grouped) != required:
        raise MetricError("OOD drop requires iid_heldout, size_shift, distribution_shift")
    return statistics.fmean(grouped["iid_heldout"]) - statistics.fmean(
        grouped["size_shift"] + grouped["distribution_shift"]
    )


def _hierarchy(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, dict[str, dict[str, list[Mapping[str, Any]]]]]]:
    """Group rows without coercing identity types or flattening clusters.

    The registered hierarchy is family -> problem -> seed -> hidden cluster.
    Older public fixtures used ``cluster``; rows without either cluster field
    are assigned a deterministic row-local cluster so they remain valid while
    still being sampled independently.  Type-tagged keys prevent values such
    as integer seed ``1`` and string seed ``"1"`` from silently colliding.
    """
    grouped: dict[str, dict[str, dict[str, dict[str, list[Mapping[str, Any]]]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    )

    def key(value: Any, field: str, *, require_int: bool = False) -> str:
        if require_int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise MetricError(f"bootstrap {field} must be an integer")
            return f"int:{value}"
        if isinstance(value, str) and value:
            return f"str:{value}"
        if isinstance(value, int) and not isinstance(value, bool):
            return f"int:{value}"
        raise MetricError(f"bootstrap {field} must be a non-empty string or integer")

    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise MetricError(f"bootstrap row is not an object: {index}")
        try:
            family = row["problem_family"]
            problem = row["problem"]
            seed = row["seed"]
        except KeyError as exc:
            raise MetricError(f"bootstrap row missing hierarchy key: {exc.args[0]}") from exc
        family_key = key(family, "problem_family")
        problem_key = key(problem, "problem")
        seed_key = key(seed, "seed", require_int=True)
        cluster_value = row.get(
            "hidden_test_instance_cluster",
            row.get("cluster", f"implicit-row-{index}"),
        )
        cluster_key = key(cluster_value, "hidden_test_instance_cluster")
        grouped[family_key][problem_key][seed_key][cluster_key].append(row)
    return grouped


def _resample_hierarchy(rows: Sequence[Mapping[str, Any]], rng: random.Random) -> list[Mapping[str, Any]]:
    grouped = _hierarchy(rows)
    families = list(grouped)
    if not families:
        raise MetricError("cannot bootstrap empty rows")
    sampled: list[Mapping[str, Any]] = []
    for _ in range(len(families)):
        family = rng.choice(families)
        problems = list(grouped[family])
        for _ in range(len(problems)):
            problem = rng.choice(problems)
            seeds = list(grouped[family][problem])
            for _ in range(len(seeds)):
                seed = rng.choice(seeds)
                clusters = list(grouped[family][problem][seed])
                for _ in range(len(clusters)):
                    cluster = rng.choice(clusters)
                    # Preserve all observations belonging to the sampled
                    # hidden cluster; never split a paired cluster apart.
                    sampled.extend(grouped[family][problem][seed][cluster])
    return sampled


def hierarchical_bootstrap(
    rows: Sequence[Mapping[str, Any]],
    statistic: Callable[[Sequence[Mapping[str, Any]]], float],
    *,
    replicates: int = 20000,
    seed: int = 2026080901,
) -> list[float]:
    """Resample family -> problem -> seed -> hidden cluster with replacement."""
    if (
        isinstance(replicates, bool)
        or not isinstance(replicates, int)
        or replicates <= 0
    ):
        raise MetricError("bootstrap replicates must be a positive integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise MetricError("bootstrap seed must be an integer")
    rng = random.Random(seed)
    values = []
    for _ in range(replicates):
        value = _finite_metric(statistic(_resample_hierarchy(rows, rng)), "bootstrap statistic")
        values.append(value)
    return values


def percentile_interval(values: Iterable[float], lower: float = 0.025,
                        upper: float = 0.975) -> tuple[float, float]:
    ordered = sorted(_finite_metric(value, "bootstrap value") for value in values)
    lower = _finite_metric(lower, "lower percentile")
    upper = _finite_metric(upper, "upper percentile")
    if not ordered or not 0 <= lower <= upper <= 1:
        raise MetricError("invalid percentile interval")

    def pick(q: float) -> float:
        index = (len(ordered) - 1) * q
        lo = int(index)
        hi = min(lo + 1, len(ordered) - 1)
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)

    return pick(lower), pick(upper)


def oracle_delta_statistic(rows: Sequence[Mapping[str, Any]]) -> float:
    """Forge minus cellwise-best baseline, with oracle reselected per sample."""
    if not rows:
        raise MetricError("empty oracle rows")
    deltas = []
    for row in rows:
        baselines = row.get("baselines")
        if not isinstance(baselines, Mapping) or not baselines:
            raise MetricError("oracle row has no baseline values")
        forge = _finite_metric(row["forge"], "forge")
        baseline_values = [
            _finite_metric(value, "baseline") for value in baselines.values()
        ]
        deltas.append(forge - max(baseline_values))
    return statistics.fmean(deltas)
