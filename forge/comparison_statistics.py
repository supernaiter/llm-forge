"""Descriptive paired statistics for the matched-model comparison track.

These statistics are deliberately separate from the V3 hidden-test gate.  They
describe the raw matched-harness metric by problem and paired seed; they do not
pretend that scores from OBP and TSP share a meaningful raw scale.
"""
from __future__ import annotations

import json
import math
import random
import statistics
from pathlib import Path
from typing import Any, Mapping

from .protocol import ProtocolError, canonical_json, strict_json_loads


STATISTICS_SCHEMA_VERSION = 1
BOOTSTRAP_REPLICATES = 20_000
BOOTSTRAP_SEED = 2_026_081_801
PRIMARY_METHOD = "TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V2"


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProtocolError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ProtocolError(f"{label} must be a JSON object")
    return value


def _read_results(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ProtocolError(f"comparison results are missing: {path}")
    rows: list[dict[str, Any]] = []
    try:
        for line_number, raw in enumerate(path.read_bytes().splitlines(), 1):
            value = strict_json_loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError(f"line {line_number} is not an object")
            rows.append(value)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError(f"invalid comparison results: {path}") from exc
    return rows


def _percentile(values: list[float], quantile: float) -> float:
    if not values or not 0.0 <= quantile <= 1.0:
        raise ProtocolError("cannot compute percentile")
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _bootstrap_mean(values: list[float], *, seed: int) -> tuple[float, float]:
    if not values:
        raise ProtocolError("cannot bootstrap an empty paired sample")
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(BOOTSTRAP_REPLICATES):
        samples.append(statistics.fmean(values[rng.randrange(len(values))] for _ in values))
    return _percentile(samples, 0.025), _percentile(samples, 0.975)


def _finite_values(rows: list[Mapping[str, Any]], method_id: str, problem_id: str) -> list[Mapping[str, Any]]:
    selected = [
        row for row in rows
        if row.get("method_id") == method_id and row.get("problem_id") == problem_id
    ]
    for row in selected:
        value = row.get("auc_by_generation")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ProtocolError(f"non-finite AUC in {method_id}/{problem_id}")
    return selected


def compute_comparison_statistics(bundle: str | Path) -> dict[str, Any]:
    """Compute paired per-problem seed statistics without pooling raw scales."""
    target = Path(bundle)
    manifest = _read_json(target / "comparison_manifest.json", "comparison manifest")
    rows = _read_results(target / "comparison_results.jsonl")
    methods = manifest.get("methods")
    if not isinstance(methods, list):
        raise ProtocolError("comparison manifest methods are missing")
    method_ids = [item.get("method_id") for item in methods if isinstance(item, Mapping)]
    if PRIMARY_METHOD not in method_ids:
        raise ProtocolError("primary matched method is missing")
    problem_items = manifest.get("problems")
    if not isinstance(problem_items, list) or not problem_items:
        raise ProtocolError("comparison manifest problems are missing")
    problem_ids = [item.get("problem_id") for item in problem_items if isinstance(item, Mapping)]

    by_key: dict[tuple[str, str, int], Mapping[str, Any]] = {}
    for row in rows:
        method_id = row.get("method_id")
        problem_id = row.get("problem_id")
        seed = row.get("seed")
        if not isinstance(method_id, str) or not isinstance(problem_id, str) or isinstance(seed, bool) or not isinstance(seed, int):
            raise ProtocolError("comparison row identity is malformed")
        key = (method_id, problem_id, seed)
        if key in by_key:
            raise ProtocolError(f"duplicate comparison cell: {key}")
        by_key[key] = row

    comparisons: dict[str, dict[str, Any]] = {}
    for comparator in method_ids:
        if comparator in {PRIMARY_METHOD, "FIXED_DEV_BEST"}:
            # FIXED_DEV_BEST remains included below; the primary is not compared
            # with itself.
            if comparator == PRIMARY_METHOD:
                continue
        per_problem: dict[str, Any] = {}
        for problem_index, problem_id in enumerate(problem_ids):
            primary_rows = _finite_values(rows, PRIMARY_METHOD, problem_id)
            comparator_rows = _finite_values(rows, comparator, problem_id)
            primary_by_seed = {int(row["seed"]): float(row["auc_by_generation"]) for row in primary_rows}
            comparator_by_seed = {int(row["seed"]): float(row["auc_by_generation"]) for row in comparator_rows}
            if set(primary_by_seed) != set(comparator_by_seed):
                raise ProtocolError(f"unpaired seeds for {comparator}/{problem_id}")
            seeds = sorted(primary_by_seed)
            deltas = [primary_by_seed[seed] - comparator_by_seed[seed] for seed in seeds]
            ci_low, ci_high = _bootstrap_mean(
                deltas,
                seed=BOOTSTRAP_SEED + problem_index,
            )
            per_problem[problem_id] = {
                "seed_count": len(seeds),
                "seeds": seeds,
                "primary_values": [primary_by_seed[seed] for seed in seeds],
                "comparator_values": [comparator_by_seed[seed] for seed in seeds],
                "delta_values": deltas,
                "primary_mean": statistics.fmean(primary_by_seed.values()),
                "primary_sample_sd": statistics.stdev(primary_by_seed.values()) if len(seeds) > 1 else 0.0,
                "comparator_mean": statistics.fmean(comparator_by_seed.values()),
                "comparator_sample_sd": statistics.stdev(comparator_by_seed.values()) if len(seeds) > 1 else 0.0,
                "delta_mean": statistics.fmean(deltas),
                "delta_sample_sd": statistics.stdev(deltas) if len(seeds) > 1 else 0.0,
                "delta_bootstrap_95ci": [ci_low, ci_high],
                "win_rate": sum(delta > 0.0 for delta in deltas) / len(deltas),
                "tie_rate": sum(delta == 0.0 for delta in deltas) / len(deltas),
            }
        comparisons[comparator] = {"by_problem": per_problem}

    return {
        "schema_version": STATISTICS_SCHEMA_VERSION,
        "study_id": manifest.get("study_id"),
        "track": manifest.get("track"),
        "primary_method": PRIMARY_METHOD,
        "metric": "auc_by_generation",
        "metric_unit": "raw_problem_score",
        "cross_problem_raw_pooling": "not_reported",
        "pairing": ["problem_id", "seed"],
        "bootstrap": {
            "type": "paired_seed_resampling",
            "replicates": BOOTSTRAP_REPLICATES,
            "seed": BOOTSTRAP_SEED,
            "confidence_interval": "two_sided_percentile_95",
        },
        "comparisons": comparisons,
    }


def write_comparison_statistics(bundle: str | Path, output: str | Path | None = None) -> Path:
    target = Path(bundle)
    destination = Path(output) if output is not None else target / "comparison_statistics.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(canonical_json(compute_comparison_statistics(target)))
    return destination
