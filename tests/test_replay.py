import json

from forge.ledger import EventLedger
from forge.replay import (
    replay_decision_hash,
    replay_decision_records,
    replay_result_hash,
    replay_summary,
)


def test_replay_hash_is_stable_and_does_not_execute_code(tmp_path):
    path = tmp_path / "events.jsonl"
    ledger = EventLedger(path, run_id="replay-run")
    attempt = ledger.start_attempt(generation=1, slot=0)
    ledger.finish_attempt(attempt, status="valid_candidate", score=3.0)
    ledger.record_event("incumbent_selected", {
        "attempt_id": attempt,
        "generation": 1,
        "after_attempt": 1,
        "candidate_sha256": "a" * 64,
        "score": 3.0,
    })
    first = replay_summary(path)
    second = replay_summary(path)
    assert first == second
    assert first["attempt_count"] == 1
    assert len(first["decision_hash"]) == 64
    assert replay_decision_hash(path) == first["decision_hash"]
    assert replay_result_hash(path) == first["result_recomputation_hash"]


def test_replay_hash_commits_controller_decision_metadata(tmp_path):
    path = tmp_path / "events.jsonl"
    ledger = EventLedger(path, run_id="controller-replay")
    attempt = ledger.start_attempt(
        generation=1,
        slot=0,
        metadata={"controller_action": {"generator_model": "SMALL"}},
    )
    ledger.finish_attempt(attempt, status="empty_response")
    ledger.record_event("incumbent_selected", {
        "attempt_id": attempt,
        "after_attempt": 1,
        "candidate_sha256": "a" * 64,
        "score": 0.0,
    })
    first = replay_decision_hash(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    import json
    import hashlib
    from forge.protocol import canonical_json
    event = json.loads(lines[0])
    event["payload"]["metadata"]["controller_action"]["generator_model"] = "STRONG"
    body = {key: value for key, value in event.items() if key != "event_hash"}
    event["event_hash"] = hashlib.sha256(canonical_json(body)).hexdigest()
    lines[0] = json.dumps(event, ensure_ascii=False, sort_keys=True)
    # Re-chain every following event because the first event is part of the
    # hash chain, including the finish and incumbent checkpoint records.
    previous_hash = event["event_hash"]
    for index in range(1, len(lines)):
        following = json.loads(lines[index])
        following["prev_hash"] = previous_hash
        body = {key: value for key, value in following.items() if key != "event_hash"}
        following["event_hash"] = hashlib.sha256(canonical_json(body)).hexdigest()
        lines[index] = json.dumps(following, ensure_ascii=False, sort_keys=True)
        previous_hash = following["event_hash"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert replay_decision_hash(path) != first


def test_replay_decision_stream_excludes_evaluator_wall_clock_usage(tmp_path):
    path = tmp_path / "events.jsonl"
    ledger = EventLedger(path, run_id="replay-resource-separation")
    attempt = ledger.start_attempt(generation=1, slot=0)
    ledger.finish_attempt(
        attempt,
        status="empty_response",
        evaluator_resource_usage={
            "evaluator_calls": 1,
            "evaluator_cost": 0.25,
            "wall_time_ms": 250.0,
        },
    )
    ledger.record_event("incumbent_selected", {
        "attempt_id": attempt,
        "after_attempt": 1,
        "candidate_sha256": "a" * 64,
        "score": 0.0,
    })

    finished = [
        record for record in replay_decision_records(path)
        if record["event_type"] == "attempt_finished"
    ][0]
    assert "evaluator_resource_usage" not in finished["payload"]


def test_replay_rejects_missing_incumbent_checkpoint(tmp_path):
    path = tmp_path / "events.jsonl"
    ledger = EventLedger(path, run_id="missing-checkpoint")
    attempt = ledger.start_attempt(generation=1, slot=0)
    ledger.finish_attempt(attempt, status="empty_response")

    import pytest
    from forge.ledger import LedgerError

    with pytest.raises(LedgerError, match="missing incumbent checkpoints"):
        replay_summary(path)


def test_ledger_rejects_duplicate_incumbent_checkpoint(tmp_path):
    path = tmp_path / "events.jsonl"
    ledger = EventLedger(path, run_id="duplicate-checkpoint")
    attempt = ledger.start_attempt(generation=1, slot=0)
    ledger.finish_attempt(attempt, status="empty_response")
    checkpoint = {
        "attempt_id": attempt,
        "after_attempt": 1,
        "candidate_sha256": "b" * 64,
        "score": 0.0,
    }
    ledger.record_event("incumbent_selected", checkpoint)

    import pytest
    from forge.ledger import LedgerError

    with pytest.raises(LedgerError, match="duplicate incumbent checkpoint"):
        ledger.record_event("incumbent_selected", checkpoint)
    # Failed appends are transactional: the rejected duplicate is not left in
    # the JSONL stream and the valid prefix remains replayable.
    assert EventLedger(path, run_id="duplicate-checkpoint").finished_attempt_count == 1
