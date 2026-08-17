"""Independent replay helpers for V3 event streams.

Replay consumes recorded events only; it never calls an LLM, evaluator, or
problem pack.  This makes a recorded stream a deterministic audit object.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .ledger import EventLedger, LedgerError
from .protocol import canonical_json, strict_json_loads


def load_ledger(path: str | Path) -> EventLedger:
    target = Path(path)
    if not target.is_file():
        raise LedgerError(f"missing event ledger: {target}")
    first = target.read_text(encoding="utf-8").splitlines()
    if not first:
        raise LedgerError("empty event ledger")
    try:
        run_id = strict_json_loads(first[0])["run_id"]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise LedgerError("event ledger has no run_id") from exc
    return EventLedger(target, run_id=run_id)


def replay_decision_records(path: str | Path) -> list[dict[str, Any]]:
    ledger = load_ledger(path)
    ledger.assert_invariants(require_checkpoints=True)
    records = []
    for event in ledger.events:
        if event["event_type"] in {
            "attempt_started", "attempt_finished", "incumbent_selected",
        }:
            payload = dict(event["payload"])
            # Resource telemetry is audited separately from search decisions;
            # excluding both generation and evaluator observations keeps
            # decision replay independent of wall-clock measurements while
            # preserving the raw usage in the ledger.
            payload.pop("resource_usage", None)
            payload.pop("evaluator_resource_usage", None)
            records.append({
                "seq": event["seq"],
                "event_type": event["event_type"],
                "payload": payload,
            })
    return records


def replay_decision_hash(path: str | Path) -> str:
    """Hash the decisions a verifier would consume, excluding wall-clock data."""
    return hashlib.sha256(canonical_json(replay_decision_records(path))).hexdigest()


def replay_result_records(path: str | Path) -> list[dict[str, Any]]:
    """Return result-bearing events, including resource observations.

    This stream is intentionally separate from ``replay_decision_records``:
    wall-clock/resource telemetry must not alter search decisions, but it must
    be included when independently recomputing a result bundle.
    """
    ledger = load_ledger(path)
    ledger.assert_invariants(require_checkpoints=True)
    records = []
    for event in ledger.events:
        if event["event_type"] in {"attempt_finished", "incumbent_selected"}:
            records.append({
                "seq": event["seq"],
                "event_type": event["event_type"],
                "payload": dict(event["payload"]),
            })
    return records


def replay_result_hash(path: str | Path) -> str:
    """Hash the independent result recomputation stream."""
    return hashlib.sha256(canonical_json(replay_result_records(path))).hexdigest()


def replay_summary(path: str | Path) -> dict[str, Any]:
    ledger = load_ledger(path)
    ledger.assert_invariants(require_checkpoints=True)
    resources = ledger.resource_summary()
    ledger_summary = ledger.summary()
    return {
        "run_id": ledger.run_id,
        "attempt_count": ledger.attempt_count,
        "finished_attempt_count": ledger.finished_attempt_count,
        "generation_slot_count": ledger_summary.get("generation_slot_count"),
        "generation_slots": ledger_summary.get("generation_slots", []),
        "decision_hash": replay_decision_hash(path),
        "result_recomputation_hash": replay_result_hash(path),
        "resource_ledger_valid": bool(resources["valid"] and not resources["budget_violations"]),
        "resource_summary": resources,
        "resource_ledger_hash": hashlib.sha256(
            canonical_json(resources["records"])
        ).hexdigest(),
        "tracks": ledger_summary.get("tracks", []),
        "head_hash": ledger.summary()["head_hash"],
        "candidate_ast_hash_coverage": ledger_summary.get("candidate_ast_hash_coverage"),
        "accepted_candidate_diff_coverage": ledger_summary.get("accepted_candidate_diff_coverage"),
        "trace_parent_child_links_complete": ledger_summary.get("trace_parent_child_links_complete"),
        "parent_child_link_coverage": ledger_summary.get("parent_child_link_coverage"),
        "deterministic_cycle_detection_coverage": ledger_summary.get(
            "deterministic_cycle_detection_coverage"
        ),
        "lineage_cycle_count": ledger_summary.get("lineage_cycle_count"),
        "evaluator_hack_audit_coverage": ledger_summary.get("evaluator_hack_audit_coverage"),
    }
