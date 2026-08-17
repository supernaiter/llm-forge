import json

from forge.ledger import EventLedger, candidate_sha256
from forge.lineage import lineage_metadata
from forge.resources import generation_usage
from tools.verify_counterfactual_ablation import audit_run


def _write_problem(path):
    path.mkdir()
    (path / "problem.py").write_text(
        """
class Problem:
    def score_with_status(self, source):
        return float(source.split('=')[1]), True, 'valid_candidate', None
""",
        encoding="utf-8",
    )


def _make_run(tmp_path, *, child="value=2"):
    problem_dir = tmp_path / "problem"
    _write_problem(problem_dir)
    events = tmp_path / "events.jsonl"
    archive = tmp_path / "archive.jsonl"
    parent = "value=1"
    archive.write_text(
        "\n".join([
            json.dumps({"text": parent, "score": 1.0, "gen": 0}),
            json.dumps({"text": child, "score": 2.0 if child != parent else 1.0, "gen": 1}),
        ]) + "\n",
        encoding="utf-8",
    )
    ledger = EventLedger(events, run_id="cf-run", max_attempts=1)
    attempt = ledger.start_attempt(
        generation=1,
        slot=0,
        model="MOCK",
        track="SAME_MODEL",
        metadata={
            "controller_action": {"generator_model": "MOCK"},
            "generation_mode": "llm",
            "mock_execution": True,
        },
    )
    ledger.finish_attempt(
        attempt,
        status="valid_candidate",
        candidate_hash=candidate_sha256(child),
        score=2.0 if child != parent else 1.0,
        resource_usage=generation_usage(
            input_tokens=1,
            output_tokens=1,
            wall_time_ms=1.0,
            model_identity="MOCK",
            sampling_profile={"interface": "test"},
        ),
        metadata=lineage_metadata(child, [parent]),
    )
    ledger.record_event("incumbent_selected", {
        "attempt_id": attempt,
        "after_attempt": 1,
        "candidate_sha256": candidate_sha256(parent),
        "score": 1.0,
    })
    return events, archive, problem_dir


def test_counterfactual_ablation_proves_ast_child_beats_parent(tmp_path):
    events, archive, problem_dir = _make_run(tmp_path)
    report = audit_run(
        events,
        archive,
        problem_dir,
        target_problem_id="fixture",
        mechanism="TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V2",
        seed=0,
    )
    assert report["improvement_candidate_count"] == 1
    assert report["causal_pass_count"] == 1
    assert report["causal_coverage"] == 1.0
    assert report["causal_gate"] is True
    assert report["candidates"][0]["child_evaluation"]["score"] == 2.0


def test_counterfactual_ablation_rejects_no_ast_improvement(tmp_path):
    events, archive, problem_dir = _make_run(tmp_path, child="value=1")
    report = audit_run(
        events,
        archive,
        problem_dir,
        target_problem_id="fixture",
        mechanism="TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V2",
        seed=0,
    )
    assert report["improvement_candidate_count"] == 0
    assert report["causal_pass_count"] == 0
    assert report["causal_gate"] is False
    assert report["candidates"][0]["causal_ablation_pass"] is False
