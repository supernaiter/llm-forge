import json

import pytest

from forge.ledger import ATTEMPT_STATUSES, EventLedger, LedgerError, candidate_sha256
from forge.resources import generation_usage


def test_ledger_counts_failed_attempts_and_replays_hash_chain(tmp_path):
    path = tmp_path / "events.jsonl"
    ledger = EventLedger(path, run_id="run-1", max_attempts=2)
    first = ledger.start_attempt(generation=1, slot=0)
    ledger.finish_attempt(first, status="model_error", error_class="RuntimeError")
    second = ledger.start_attempt(generation=1, slot=1)
    ledger.finish_attempt(second, status="valid_candidate",
                          candidate_hash=candidate_sha256("candidate"), score=1.5)
    ledger.assert_invariants()
    assert ledger.attempt_count == 2
    assert ledger.finished_attempt_count == 2
    assert ledger.summary()["status_counts"] == {"model_error": 1, "valid_candidate": 1}

    replay = EventLedger(path, run_id="run-1", max_attempts=2)
    replay.assert_invariants()
    assert replay.summary()["head_hash"] == ledger.summary()["head_hash"]


def test_ledger_rejects_open_attempt_and_cap_overflow(tmp_path):
    path = tmp_path / "events.jsonl"
    ledger = EventLedger(path, run_id="run-2", max_attempts=1)
    attempt = ledger.start_attempt(generation=1, slot=0)
    with pytest.raises(LedgerError):
        ledger.assert_invariants()
    with pytest.raises(LedgerError):
        ledger.start_attempt(generation=1, slot=1)
    ledger.finish_attempt(attempt, status="empty_response")
    ledger.assert_invariants()


def test_ledger_rejects_duplicate_or_invalid_generation_slot(tmp_path):
    path = tmp_path / "events.jsonl"
    ledger = EventLedger(path, run_id="run-slots")
    first = ledger.start_attempt(generation=2, slot=4)
    ledger.finish_attempt(first, status="empty_response")
    with pytest.raises(LedgerError, match="generation slot already recorded"):
        ledger.start_attempt(generation=2, slot=4)
    with pytest.raises(LedgerError, match="non-negative integers"):
        ledger.start_attempt(generation=-1, slot=0)
    with pytest.raises(LedgerError, match="non-negative integers"):
        ledger.start_attempt(generation=2, slot=True)

    # The invariant also applies when loading an externally supplied stream,
    # rather than only to calls through start_attempt().
    raw = path.read_text(encoding="utf-8").splitlines()
    import hashlib
    from forge.protocol import canonical_json
    event = json.loads(raw[0])
    duplicate = dict(event)
    duplicate["seq"] = 3
    duplicate["payload"] = dict(event["payload"])
    duplicate["payload"]["attempt_id"] = "run-slots:attempt:2"
    duplicate["prev_hash"] = json.loads(raw[-1])["event_hash"]
    body = {key: value for key, value in duplicate.items() if key != "event_hash"}
    duplicate["event_hash"] = hashlib.sha256(canonical_json(body)).hexdigest()
    path.write_text("\n".join(raw + [json.dumps(duplicate, sort_keys=True)]) + "\n", encoding="utf-8")
    with pytest.raises(LedgerError, match="duplicate generation slot"):
        EventLedger(path, run_id="run-slots")


def test_ledger_rejects_nonsequential_attempt_id_in_external_stream(tmp_path):
    path = tmp_path / "events.jsonl"
    ledger = EventLedger(path, run_id="run-sequence")
    attempt = ledger.start_attempt(generation=1, slot=0)
    ledger.finish_attempt(attempt, status="empty_response")
    lines = path.read_text(encoding="utf-8").splitlines()
    import hashlib
    event = json.loads(lines[0])
    event["payload"]["attempt_id"] = "run-sequence:attempt:99"
    body = {key: value for key, value in event.items() if key != "event_hash"}
    from forge.protocol import canonical_json
    event["event_hash"] = hashlib.sha256(canonical_json(body)).hexdigest()
    lines[0] = json.dumps(event, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(LedgerError, match="next run sequence"):
        EventLedger(path, run_id="run-sequence")


def test_every_v3_attempt_status_consumes_one_slot(tmp_path):
    path = tmp_path / "events.jsonl"
    statuses = sorted(ATTEMPT_STATUSES - {"started"})
    ledger = EventLedger(path, run_id="run-statuses", max_attempts=len(statuses))
    for slot, status in enumerate(statuses):
        attempt = ledger.start_attempt(generation=1, slot=slot)
        ledger.finish_attempt(attempt, status=status)
    ledger.assert_invariants()
    summary = ledger.summary()
    assert summary["attempt_count"] == len(statuses)
    assert summary["generation_slot_count"] == len(statuses)
    assert set(summary["status_counts"]) == set(statuses)


def test_ledger_rejects_tampering_and_truncated_line(tmp_path):
    path = tmp_path / "events.jsonl"
    ledger = EventLedger(path, run_id="run-3")
    attempt = ledger.start_attempt(generation=1, slot=0)
    ledger.finish_attempt(attempt, status="duplicate_candidate")

    original = path.read_text(encoding="utf-8")
    path.write_text(original.replace("duplicate_candidate", "valid_candidate"), encoding="utf-8")
    with pytest.raises(LedgerError):
        EventLedger(path, run_id="run-3")

    path.write_text(original[:-1], encoding="utf-8")
    with pytest.raises(LedgerError):
        EventLedger(path, run_id="run-3")


def test_ledger_rejects_unknown_track_and_floating_model_alias(tmp_path):
    ledger = EventLedger(tmp_path / "events.jsonl", run_id="run-pinned")
    with pytest.raises(LedgerError, match="unknown V3 track"):
        ledger.start_attempt(generation=1, slot=0, track="UNKNOWN")
    with pytest.raises(LedgerError, match="pinned"):
        ledger.start_attempt(generation=1, slot=0, model="latest")


def test_controller_model_identity_must_match_observed_generation_resource(tmp_path):
    ledger = EventLedger(tmp_path / "events.jsonl", run_id="run-model-binding")
    selected = "STRONG@sha256:" + "a" * 64
    attempt = ledger.start_attempt(
        generation=1,
        slot=0,
        model=selected,
        metadata={"controller_action": {"generator_model": selected}},
    )
    with pytest.raises(LedgerError, match="differs from controller action"):
        ledger.finish_attempt(
            attempt,
            status="model_error",
            resource_usage=generation_usage(
                wall_time_ms=1.0,
                model_identity="OTHER@sha256:" + "b" * 64,
            ),
        )
    assert ledger.finished_attempt_count == 0


def test_mock_controller_identity_substitution_is_explicit(tmp_path):
    ledger = EventLedger(tmp_path / "events.jsonl", run_id="run-mock-model")
    selected = "SMALL@sha256:" + "a" * 64
    attempt = ledger.start_attempt(
        generation=1,
        slot=0,
        model=selected,
        metadata={
            "controller_action": {"generator_model": selected},
            "generation_mode": "llm",
            "mock_execution": True,
        },
    )
    ledger.finish_attempt(
        attempt,
        status="model_error",
        resource_usage=generation_usage(
            wall_time_ms=1.0,
            model_identity="MOCK",
        ),
    )
    ledger.assert_invariants()


def test_ledger_rejects_unknown_events_and_malformed_finished_payloads(tmp_path):
    path = tmp_path / "events.jsonl"
    ledger = EventLedger(path, run_id="run-schema")
    attempt = ledger.start_attempt(generation=1, slot=0)

    with pytest.raises(LedgerError, match="unknown event_type"):
        ledger.record_event("unregistered_event", {})

    with pytest.raises(LedgerError, match="candidate hash"):
        ledger.finish_attempt(attempt, status="valid_candidate", candidate_hash="bad")

    # The failed append must not consume the attempt or leave a partial line.
    assert ledger.finished_attempt_count == 0
    ledger.finish_attempt(attempt, status="valid_candidate", score=1.0)

    # A finite-score check also applies when a stream is supplied externally.
    raw = path.read_text(encoding="utf-8").splitlines()
    import hashlib
    event = json.loads(raw[-1])
    event["payload"]["score"] = float("nan")
    from forge.protocol import canonical_json
    body = {key: value for key, value in event.items() if key != "event_hash"}
    event["event_hash"] = hashlib.sha256(canonical_json(body)).hexdigest()
    path.write_text("\n".join(raw[:-1] + [json.dumps(event, allow_nan=True, sort_keys=True)]) + "\n",
                    encoding="utf-8")
    with pytest.raises(LedgerError, match="non-finite"):
        EventLedger(path, run_id="run-schema")
