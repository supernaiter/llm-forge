#!/usr/bin/env python3
"""Collect development-only controller traces from a Forge event ledger.

The collector consumes search-side events only.  It never opens a task pack or
reads hidden-test results; its output is suitable as input to
``tools/freeze_controller.py`` and is therefore deliberately restricted to
``split=dev`` traces.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forge.protocol import ProtocolError, canonical_json  # noqa: E402
from forge.replay import load_ledger  # noqa: E402


def collect_traces(events_path: str | Path, *, problem_id: str) -> list[dict[str, Any]]:
    """Recompute one development trace per controller generation.

    ``quality_gain`` is the change in the final search-side incumbent score
    from the preceding generation.  ``cost`` is the sum of observed mock
    output-token counts when available, making seeded mock fitting
    deterministic; otherwise it is observed generation wall time in seconds.
    Missing/non-finite telemetry is rejected instead of being imputed, because
    a fitted controller must not learn from an invented cost.
    """
    path = Path(events_path)
    if not isinstance(problem_id, str) or not problem_id.strip():
        raise ProtocolError("problem_id must be a non-empty development identifier")
    try:
        ledger = load_ledger(path)
        ledger.assert_invariants(require_checkpoints=True)
    except Exception as exc:
        if isinstance(exc, ProtocolError):
            raise
        raise ProtocolError(f"cannot replay development ledger: {type(exc).__name__}") from exc

    attempts: dict[str, int] = {}
    actions: dict[int, Mapping[str, Any]] = {}
    states: dict[int, Mapping[str, Any] | None] = {}
    baseline_scores: dict[int, float] = {}
    costs: defaultdict[int, float] = defaultdict(float)
    incumbent_scores: dict[int, float] = {}

    for event in ledger.events:
        event_type = event.get("event_type")
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            raise ProtocolError("development ledger payload is not an object")
        if event_type == "attempt_started":
            attempt_id = payload.get("attempt_id")
            generation = payload.get("generation")
            metadata = payload.get("metadata")
            if (
                not isinstance(attempt_id, str)
                or isinstance(generation, bool)
                or not isinstance(generation, int)
                or not isinstance(metadata, Mapping)
                or not isinstance(metadata.get("controller_action"), Mapping)
            ):
                raise ProtocolError(
                    "development ledger requires controller_action on every attempt_started"
                )
            action = dict(metadata["controller_action"])
            state_value = metadata.get("controller_state")
            if state_value is not None and not isinstance(state_value, Mapping):
                raise ProtocolError(
                    "development controller_state must be an object when present"
                )
            previous = actions.get(generation)
            if previous is not None and canonical_json(previous) != canonical_json(action):
                raise ProtocolError(
                    f"controller action differs within development generation {generation}"
                )
            previous_state = states.get(generation)
            normalized_state = dict(state_value) if isinstance(state_value, Mapping) else None
            if generation in states and canonical_json(previous_state) != canonical_json(normalized_state):
                raise ProtocolError(
                    f"controller state differs within development generation {generation}"
                )
            baseline = metadata.get("generation_baseline_score")
            if baseline is not None:
                if (
                    isinstance(baseline, bool)
                    or not isinstance(baseline, (int, float))
                    or not math.isfinite(float(baseline))
                ):
                    raise ProtocolError(
                        "development generation baseline score is missing or invalid"
                    )
                baseline = float(baseline)
                previous_baseline = baseline_scores.get(generation)
                if previous_baseline is not None and previous_baseline != baseline:
                    raise ProtocolError(
                        f"generation baseline differs within development generation {generation}"
                    )
                baseline_scores[generation] = baseline
            actions[generation] = action
            states[generation] = normalized_state
            attempts[attempt_id] = generation
        elif event_type == "attempt_finished":
            attempt_id = payload.get("attempt_id")
            generation = attempts.get(attempt_id)
            usage = payload.get("resource_usage")
            if generation is None or not isinstance(usage, Mapping):
                raise ProtocolError("development attempt resource usage is missing")
            wall_time_ms = usage.get("wall_time_ms")
            if (
                isinstance(wall_time_ms, bool)
                or not isinstance(wall_time_ms, (int, float))
                or not math.isfinite(float(wall_time_ms))
                or float(wall_time_ms) < 0
            ):
                raise ProtocolError("development generation wall time is missing or invalid")
            # Mock generation records expose observed output token counts.  They
            # are deterministic for a declared seed, whereas wall-clock time
            # is affected by host load and made controller fitting
            # non-replayable.  The fixed attempt budget already accounts for
            # the number of generations; output tokens are the variable model
            # compute controlled by an action.  Prompt input tokens remain in
            # the immutable resource ledger but are not double-counted in this
            # mock controller utility.  Keep wall time as the fallback for
            # ordinary adapters and legacy hand-authored ledgers.
            output_tokens = usage.get("output_tokens")
            mock_tokens = (
                output_tokens is not None
                and not isinstance(output_tokens, bool)
                and isinstance(output_tokens, int)
                and output_tokens >= 0
            )
            costs[generation] += (
                float(output_tokens)
                if mock_tokens
                else float(wall_time_ms) / 1000.0
            )
        elif event_type == "incumbent_selected":
            attempt_id = payload.get("attempt_id")
            generation = attempts.get(attempt_id)
            score = payload.get("score")
            if generation is None or (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(float(score))
            ):
                raise ProtocolError("development incumbent score is missing or invalid")
            incumbent_scores[generation] = float(score)

    if not actions:
        raise ProtocolError("development ledger contains no controller actions")
    traces: list[dict[str, Any]] = []
    previous_score: float | None = None
    source_run_id = ledger.run_id
    source_events_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    for generation in sorted(actions):
        if generation not in incumbent_scores:
            raise ProtocolError(
                f"development generation {generation} has no incumbent checkpoint"
            )
        cost = costs[generation]
        score = incumbent_scores[generation]
        baseline = baseline_scores.get(generation)
        quality_gain = (
            score - baseline
            if baseline is not None
            else (0.0 if previous_score is None else score - previous_score)
        )
        if not math.isfinite(quality_gain):
            raise ProtocolError(
                f"development quality gain is non-finite at generation {generation}"
            )
        traces.append({
            "split": "dev",
            "problem_id": problem_id.strip(),
            "generation": generation,
            "action": dict(actions[generation]),
            "state": states[generation],
            "quality_gain": quality_gain,
            "cost": cost,
            "source_run_id": source_run_id,
            "source_events_sha256": source_events_sha256,
        })
        previous_score = score
    return traces


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--events", type=Path, action="append", required=True,
        help="development events.jsonl; repeat once per development problem",
    )
    parser.add_argument(
        "--problem-id", action="append", required=True,
        help="development problem ID corresponding positionally to --events",
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if len(args.events) != len(args.problem_id):
            raise ProtocolError("--events and --problem-id must have equal counts")
        traces = [
            trace
            for events_path, problem_id in zip(args.events, args.problem_id)
            for trace in collect_traces(events_path, problem_id=problem_id)
        ]
        args.out.parent.mkdir(parents=True, exist_ok=True)
        # ``canonical_json`` is strict about non-finite values and emits one
        # delimiter per record, so the collector cannot create permissive
        # JSONL that later fails in the freeze tool.
        args.out.write_bytes(b"".join(canonical_json(trace) for trace in traces))
        source_hash = [
            hashlib.sha256(events_path.read_bytes()).hexdigest()
            for events_path in args.events
        ]
    except (OSError, ProtocolError, ValueError) as exc:
        print(f"DENIED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "trace_count": len(traces),
        "problem_ids": args.problem_id,
        "source_events_sha256": source_hash,
        "out": str(args.out),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
