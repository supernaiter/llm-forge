import json
import random
import time

import pytest

from forge.loop import _select_archive, run
from forge.controller import ComputeAwareController, SearchAction
from forge.ledger import LedgerError
from forge.resources import generation_usage


class _Problem:
    DESCRIPTION = "test problem"

    def seed(self):
        return ["seed"]

    def score(self, cand: str):
        if not cand:
            return float("-inf"), False
        return float(len(cand)), True


class _ExactDedup:
    def __init__(self, threshold: int = 3):
        self.seen = set()

    def is_novel(self, text: str) -> bool:
        if text in self.seen:
            return False
        self.seen.add(text)
        return True


class _VisibleArchive:
    def __init__(self, items):
        self.items = items

    @property
    def best(self):
        return max(self.items, key=lambda item: item["score"])


def test_controller_archive_sampling_policies_use_search_side_state_only():
    archives = [
        _VisibleArchive([
            {"text": "a1", "score": 5.0},
            {"text": "a2", "score": 4.0},
        ]),
        _VisibleArchive([
            {"text": "b1", "score": 3.0},
            {"text": "b2", "score": 2.0},
            {"text": "b3", "score": 1.0},
            {"text": "b4", "score": 0.0},
        ]),
    ]
    assert _select_archive(archives, 0, "best", random.Random(0)) is archives[0]
    assert _select_archive(archives, 0, "diverse", random.Random(0)) is archives[1]
    assert _select_archive(archives, 1, "uniform", random.Random(0)) is archives[1]
    with pytest.raises(LedgerError, match="unsupported archive sampling policy"):
        _select_archive(archives, 0, "unknown", random.Random(0))


def test_run_adds_a_new_archive_item(tmp_path, monkeypatch):
    candidate = "a longer novel candidate phrase with distinct tokens"

    def fake_caller(prompt: str, temperature: float) -> str:
        return f"```\n{candidate}\n```"

    monkeypatch.setattr("forge.loop.make_caller", lambda tier: fake_caller)
    monkeypatch.setattr("forge.loop.DedupIndex", _ExactDedup)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    best = run(
        _Problem(),
        {
            "generations": 1,
            "batch_size": 1,
            "max_cheap_calls": 1,
            "max_smart_calls": 0,
            "archive_capacity": 10,
            "parents": 1,
            "workers": 1,
            "seed": 0,
        },
        str(run_dir),
    )

    archive = run_dir / "archive.jsonl"
    assert archive.exists()
    lines = archive.read_text().splitlines()
    assert len(lines) == 2
    assert candidate in archive.read_text()
    assert best["text"] == candidate


def test_failed_calls_are_dropped_not_archived(tmp_path, monkeypatch):
    survivor = "a distinct valid survivor phrase with many tokens"
    seen = {"n": 0}

    def fake_caller(prompt: str, temperature: float) -> str:
        seen["n"] += 1
        if seen["n"] == 1:
            raise RuntimeError("boom")
        return f"```\n{survivor}\n```"

    monkeypatch.setattr("forge.loop.make_caller", lambda tier: fake_caller)
    monkeypatch.setattr("forge.loop.DedupIndex", _ExactDedup)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    run(
        _Problem(),
        {
            "generations": 1,
            "batch_size": 2,
            "max_cheap_calls": 2,
            "max_smart_calls": 0,
            "archive_capacity": 10,
            "parents": 1,
            "workers": 1,
            "seed": 0,
        },
        str(run_dir),
    )

    text = (run_dir / "archive.jsonl").read_text()
    assert "call failed" not in text
    assert "<!--" not in text
    assert survivor in text


def test_run_writes_result_json(tmp_path, monkeypatch):
    candidate = "another unique candidate used for result json test"

    def fake_caller(prompt: str, temperature: float) -> str:
        return f"```\n{candidate}\n```"

    monkeypatch.setattr("forge.loop.make_caller", lambda tier: fake_caller)
    monkeypatch.setattr("forge.loop.DedupIndex", _ExactDedup)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    run(
        _Problem(),
        {
            "generations": 1,
            "batch_size": 1,
            "max_cheap_calls": 1,
            "max_smart_calls": 0,
            "archive_capacity": 10,
            "parents": 1,
            "workers": 1,
            "seed": 0,
        },
        str(run_dir),
    )

    result = json.loads((run_dir / "result.json").read_text())
    assert result["stopped_by"] == "generations_complete"
    assert result["generations_done"] == 1
    assert result["cheap_used"] == 1
    assert result["smart_used"] == 0
    assert result["best_score"] > 0
    assert result["wall_secs"] >= 0
    assert result["metrics"]["best_score"] == result["best_score"]
    assert result["metrics"]["generation_calls"] == result["cheap_used"]
    assert result["metrics"]["archive_path"] == "archive.jsonl"


def test_run_writes_budget_exhausted_stopped_by(tmp_path, monkeypatch):
    candidate = "yet another unique candidate for budget exhaustion test"

    def fake_caller(prompt: str, temperature: float) -> str:
        return f"```\n{candidate}\n```"

    monkeypatch.setattr("forge.loop.make_caller", lambda tier: fake_caller)
    monkeypatch.setattr("forge.loop.DedupIndex", _ExactDedup)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    run(
        _Problem(),
        {
            "generations": 5,
            "batch_size": 1,
            "max_cheap_calls": 1,
            "max_smart_calls": 0,
            "archive_capacity": 10,
            "parents": 1,
            "workers": 1,
            "seed": 0,
        },
        str(run_dir),
    )

    result = json.loads((run_dir / "result.json").read_text())
    assert result["stopped_by"] == "budget_exhausted"
    assert result["generations_done"] == 1


def _base_cfg(**overrides):
    cfg = {
        "generations": 1,
        "batch_size": 1,
        "max_cheap_calls": 1,
        "max_smart_calls": 0,
        "archive_capacity": 10,
        "parents": 1,
        "workers": 1,
        "seed": 0,
    }
    cfg.update(overrides)
    return cfg


def test_failed_calls_do_not_consume_budget(tmp_path, monkeypatch):
    """429で応答が返らなかった呼び出しは候補を生まないので予算から除外する。"""
    survivor = "a distinct valid survivor phrase with many tokens"
    calls = {"n": 0}

    def fake_caller(prompt: str, temperature: float) -> str:
        calls["n"] += 1
        if calls["n"] <= 4:
            raise RuntimeError("HTTP 429: rate limited")
        return f"```\n{survivor} {calls['n']}\n```"

    monkeypatch.setattr("forge.loop.make_caller", lambda tier: fake_caller)
    monkeypatch.setattr("forge.loop.DedupIndex", _ExactDedup)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    run(_Problem(), _base_cfg(generations=3, batch_size=2, max_cheap_calls=4), str(run_dir))

    result = json.loads((run_dir / "result.json").read_text())
    assert result["cheap_failed"] == 4
    assert result["cheap_used"] == 2, "成功した2件だけが予算を消費する"
    assert calls["n"] == 6, "失敗分の予算が残るので3世代とも回りきる"


def test_all_calls_failing_stops_the_run(tmp_path, monkeypatch):
    def fake_caller(prompt: str, temperature: float) -> str:
        raise RuntimeError("connection error")

    monkeypatch.setattr("forge.loop.make_caller", lambda tier: fake_caller)
    monkeypatch.setattr("forge.loop.DedupIndex", _ExactDedup)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    run(_Problem(), _base_cfg(generations=20, batch_size=2, max_cheap_calls=40), str(run_dir))

    result = json.loads((run_dir / "result.json").read_text())
    assert result["stopped_by"] == "llm_unavailable"
    assert result["cheap_used"] == 0
    assert result["cheap_failed"] == 6, "3世代 × batch 2 で打ち切る"


def test_protocol_v3_records_failed_and_successful_attempts(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_caller(prompt: str, temperature: float) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("HTTP 429")
        return "```\na protocol v3 survivor\n```"

    monkeypatch.setattr("forge.loop.make_caller", lambda tier: fake_caller)
    monkeypatch.setattr("forge.loop.DedupIndex", _ExactDedup)
    run_dir = tmp_path / "run-v3"
    run_dir.mkdir()
    run(_Problem(), _base_cfg(
        protocol_v3=True,
        generations=1,
        batch_size=2,
        max_cheap_calls=2,
        max_attempts=2,
    ), str(run_dir))

    result = json.loads((run_dir / "result.json").read_text())
    assert result["attempt_count"] == 2
    assert result["event_ledger_status_counts"] == {
        "model_error": 1,
        "valid_candidate": 1,
    }
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
    assert len(events) == 6
    assert {event["event_type"] for event in events} == {
        "attempt_started", "attempt_finished", "incumbent_selected",
    }
    assert result["cheap_used"] == 2, "V3ではモデル失敗もattempt予算を消費する"
    assert result["metrics"]["generation_calls"] == 2
    assert result["metrics"]["cheap_failure_rate"] == 0.5


def test_protocol_v3_scopes_restricted_sandbox_environment(tmp_path, monkeypatch):
    """Direct loop callers receive V3 pack policy without leaking it afterward."""
    observed = []

    def fake_caller(prompt: str, temperature: float) -> str:
        observed.append(__import__("os").environ.get("FORGE_PROTOCOL_V3"))
        return "```v3 scoped candidate```"

    monkeypatch.setattr("forge.loop.make_caller", lambda tier: fake_caller)
    monkeypatch.setattr("forge.loop.DedupIndex", _ExactDedup)
    monkeypatch.delenv("FORGE_PROTOCOL_V3", raising=False)
    run_dir = tmp_path / "run-v3-env"
    run_dir.mkdir()
    run(_Problem(), _base_cfg(protocol_v3=True), str(run_dir))
    assert observed == ["1"]
    assert "FORGE_PROTOCOL_V3" not in __import__("os").environ


def test_protocol_v3_all_failures_are_counted_before_stop(tmp_path, monkeypatch):
    def fake_caller(prompt: str, temperature: float) -> str:
        raise RuntimeError("connection error")

    monkeypatch.setattr("forge.loop.make_caller", lambda tier: fake_caller)
    monkeypatch.setattr("forge.loop.DedupIndex", _ExactDedup)
    run_dir = tmp_path / "run-v3-fail"
    run_dir.mkdir()
    run(_Problem(), _base_cfg(
        protocol_v3=True,
        generations=20,
        batch_size=2,
        max_cheap_calls=40,
        max_attempts=40,
    ), str(run_dir))

    result = json.loads((run_dir / "result.json").read_text())
    assert result["stopped_by"] == "llm_unavailable"
    assert result["attempt_count"] == 6
    assert result["event_ledger_status_counts"] == {"model_error": 6}
    assert result["metrics"]["generation_calls"] == 6
    assert result["metrics"]["cheap_failure_rate"] == 1.0


def test_protocol_v3_controller_controls_offspring_and_is_recorded(tmp_path, monkeypatch):
    candidate = "controller selected candidate"
    prompts = []

    def fake_caller(prompt: str, temperature: float) -> str:
        prompts.append(prompt)
        return f"```\n{candidate} {temperature}\n```"

    monkeypatch.setattr("forge.loop.make_caller", lambda tier: fake_caller)
    monkeypatch.setattr("forge.loop.DedupIndex", _ExactDedup)
    action = SearchAction("SMALL", "elite", "local", 2, 0, "uniform")
    controller = ComputeAwareController([action])
    controller.fit([{
        "split": "dev", "problem_id": "obp_dev_v1", "action": action,
        "quality_gain": 1.0, "cost": 1.0,
    }])
    controller.freeze()
    run_dir = tmp_path / "run-v3-controller"
    run_dir.mkdir()
    run(_Problem(), _base_cfg(
        protocol_v3=True,
        generations=1,
        batch_size=1,
        max_cheap_calls=2,
        max_attempts=2,
        controller_model_callers={action.generator_model: fake_caller},
    ), str(run_dir), controller=controller)

    result = json.loads((run_dir / "result.json").read_text())
    assert result["attempt_count"] == 2
    assert result["controller_policy_sha256"] == controller.policy_sha256
    assert result["controller_mechanism_id"] == controller.mechanism_id
    assert result["controller_training_problem_ids"] == ["obp_dev_v1"]
    assert result["controller_actions"][0]["action"]["number_of_offspring"] == 2
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
    starts = [event for event in events if event["event_type"] == "attempt_started"]
    assert starts[0]["payload"]["metadata"]["controller_action"]["generator_model"] == "SMALL"
    assert starts[0]["payload"]["metadata"]["generation_baseline_score"] == 4.0
    assert all("Registered mutation operator: local" in prompt for prompt in prompts)
    assert result["controller_actions"][0]["state"]["remaining_budget"] == 2
    assert result["controller_actions"][0]["state"]["time_since_last_improvement"] == 1


def test_protocol_v3_controller_can_route_to_a_pinned_model_caller(tmp_path, monkeypatch):
    calls = {"default": 0, "mapped": 0}

    def default_caller(prompt: str, temperature: float) -> str:
        calls["default"] += 1
        return "```default candidate```"

    def mapped_caller(prompt: str, temperature: float) -> str:
        calls["mapped"] += 1
        return "```mapped candidate```"

    monkeypatch.setattr("forge.loop.make_caller", lambda tier: default_caller)
    monkeypatch.setattr("forge.loop.DedupIndex", _ExactDedup)
    action = SearchAction("STRONG@sha256:" + "a" * 64, "elite", "local", 1, 0, "uniform")
    controller = ComputeAwareController([action])
    controller.fit([{
        "split": "dev", "problem_id": "obp_dev_v1", "action": action,
        "quality_gain": 1.0, "cost": 1.0,
    }])
    controller.freeze()
    run_dir = tmp_path / "run-v3-model-router"
    run_dir.mkdir()
    run(_Problem(), _base_cfg(
        protocol_v3=True,
        generations=1,
        max_cheap_calls=1,
        max_attempts=1,
        controller_model_callers={action.generator_model: mapped_caller},
    ), str(run_dir), controller=controller)

    assert calls == {"default": 0, "mapped": 1}


def test_protocol_v3_nonmock_rejects_unmapped_controller_model(tmp_path, monkeypatch):
    monkeypatch.delenv("FORGE_MOCK", raising=False)

    def fake_caller(prompt: str, temperature: float) -> str:
        return "```unmapped candidate```"

    monkeypatch.setattr("forge.loop.make_caller", lambda tier: fake_caller)
    monkeypatch.setattr("forge.loop.DedupIndex", _ExactDedup)
    action = SearchAction("STRONG@sha256:" + "b" * 64, "elite", "local", 1, 0, "uniform")
    controller = ComputeAwareController([action])
    controller.fit([{
        "split": "dev", "problem_id": "obp_dev_v1", "action": action,
        "quality_gain": 1.0, "cost": 1.0,
    }])
    controller.freeze()
    run_dir = tmp_path / "run-v3-unmapped-model"
    run_dir.mkdir()

    with pytest.raises(
        LedgerError,
        match="no pinned callable adapter",
    ):
        run(_Problem(), _base_cfg(
            protocol_v3=True,
            generations=1,
            max_cheap_calls=1,
            max_attempts=1,
            mock="true",
        ), str(run_dir), controller=controller)


def test_cheap_workers_limits_llm_concurrency_independently(tmp_path, monkeypatch):
    """cheap_workersはLLM呼び出しの同時発火数だけを絞り、V0採点の並列度は変えない。"""
    import threading

    peak = {"llm": 0, "now": 0}
    lock = threading.Lock()

    def fake_caller(prompt: str, temperature: float) -> str:
        with lock:
            peak["now"] += 1
            peak["llm"] = max(peak["llm"], peak["now"])
        time.sleep(0.02)
        with lock:
            peak["now"] -= 1
        return f"```\ncandidate {threading.get_ident()} {time.time()}\n```"

    monkeypatch.setattr("forge.loop.make_caller", lambda tier: fake_caller)
    monkeypatch.setattr("forge.loop.DedupIndex", _ExactDedup)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    run(
        _Problem(),
        _base_cfg(generations=1, batch_size=8, max_cheap_calls=8, workers=8, cheap_workers=2),
        str(run_dir),
    )
    assert peak["llm"] <= 2, f"cheap_workers=2 なのに同時 {peak['llm']} 件発火した"


def test_cheap_workers_defaults_to_workers(tmp_path, monkeypatch):
    import threading

    peak = {"llm": 0, "now": 0}
    lock = threading.Lock()

    def fake_caller(prompt: str, temperature: float) -> str:
        with lock:
            peak["now"] += 1
            peak["llm"] = max(peak["llm"], peak["now"])
        time.sleep(0.02)
        with lock:
            peak["now"] -= 1
        return f"```\ncandidate {threading.get_ident()} {time.time()}\n```"

    monkeypatch.setattr("forge.loop.make_caller", lambda tier: fake_caller)
    monkeypatch.setattr("forge.loop.DedupIndex", _ExactDedup)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    run(
        _Problem(),
        _base_cfg(generations=1, batch_size=4, max_cheap_calls=4, workers=4),
        str(run_dir),
    )
    assert peak["llm"] > 1, "cheap_workers未指定ならworkersの並列度がそのまま出る"


def test_result_json_reports_archive_diversity(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_caller(prompt: str, temperature: float) -> str:
        calls["n"] += 1
        return f"```\ncandidate number {calls['n']} with distinct length\n```"

    monkeypatch.setattr("forge.loop.make_caller", lambda tier: fake_caller)
    monkeypatch.setattr("forge.loop.DedupIndex", _ExactDedup)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    run(_Problem(), _base_cfg(generations=3, max_cheap_calls=3), str(run_dir))

    result = json.loads((run_dir / "result.json").read_text())
    assert result["archive_distinct_scores"] >= 2


def test_reflect_convergence_stops_smart_calls(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_cheap(prompt: str, temperature: float) -> str:
        return "```\nseed\n```"

    def fake_smart(prompt: str, temperature: float) -> str:
        calls["n"] += 1
        return "same guidance every time"

    tiers = {"cheap": fake_cheap, "smart": fake_smart}
    monkeypatch.setattr("forge.loop.make_caller", lambda tier: tiers[tier])
    monkeypatch.setattr("forge.loop.DedupIndex", _ExactDedup)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    run(
        _Problem(),
        {
            "generations": 4,
            "batch_size": 1,
            "max_cheap_calls": 4,
            "max_smart_calls": 10,
            "archive_capacity": 10,
            "parents": 1,
            "workers": 1,
            "reflect_every": 1,
            "seed": 0,
        },
        str(run_dir),
    )

    assert calls["n"] == 2


def test_protocol_v3_ledger_keeps_diagnostic_failure_status(tmp_path, monkeypatch):
    class DiagnosticProblem(_Problem):
        def score_with_status(self, cand: str):
            if cand == "bad candidate":
                return float("-inf"), False, "invalid_syntax", "SyntaxError"
            return float(len(cand)), True, "valid_candidate", None

    def fake_caller(prompt: str, temperature: float) -> str:
        return "```\nbad candidate\n```"

    monkeypatch.setattr("forge.loop.make_caller", lambda tier: fake_caller)
    monkeypatch.setattr("forge.loop.DedupIndex", _ExactDedup)
    run_dir = tmp_path / "run-v3-diagnostic"
    run_dir.mkdir()
    run(DiagnosticProblem(), _base_cfg(
        protocol_v3=True,
        generations=1,
        batch_size=1,
        max_cheap_calls=1,
        max_attempts=1,
    ), str(run_dir))
    result = json.loads((run_dir / "result.json").read_text())
    assert result["event_ledger_status_counts"] == {"invalid_syntax": 1}


def test_protocol_v3_records_evaluator_hack_signal_as_own_status(tmp_path, monkeypatch):
    def fake_caller(prompt: str, temperature: float) -> str:
        return "```\ndef f(x):\n    return open('hidden_score_file')\n```"

    monkeypatch.setattr("forge.loop.make_caller", lambda tier: fake_caller)
    monkeypatch.setattr("forge.loop.DedupIndex", _ExactDedup)
    run_dir = tmp_path / "run-v3-hack"
    run_dir.mkdir()
    run(_Problem(), _base_cfg(
        protocol_v3=True,
        generations=1,
        batch_size=1,
        max_cheap_calls=1,
        max_attempts=1,
    ), str(run_dir))
    result = json.loads((run_dir / "result.json").read_text())
    assert result["event_ledger_status_counts"] == {"evaluation_hack": 1}


def test_native_track_persists_immutable_gpu_cap(tmp_path, monkeypatch):
    candidate = "native candidate"

    def fake_caller(prompt: str, temperature: float) -> str:
        return f"```\n{candidate}\n```"

    def detailed(prompt: str, temperature: float) -> dict:
        return {
            "text": fake_caller(prompt, temperature),
            "resource_usage": generation_usage(
                input_tokens=1,
                output_tokens=1,
                model_identity="mock-native",
                sampling_profile={"temperature": temperature},
                wall_time_ms=1.0,
                gpu_allocation={"device_type": "A100", "count": 1, "seconds": 2.0},
                model_forward_time_ms=0.5,
            ),
        }

    fake_caller.with_metadata = detailed
    monkeypatch.setattr("forge.loop.make_caller", lambda tier: fake_caller)
    monkeypatch.setattr("forge.loop.DedupIndex", _ExactDedup)
    run_dir = tmp_path / "run-native-cap"
    run_dir.mkdir()
    run(_Problem(), _base_cfg(
        protocol_v3=True,
        track="NATIVE_COMPUTE",
        generations=1,
        batch_size=1,
        max_cheap_calls=1,
        max_attempts=1,
        resource_budgets={"generation": {"gpu_seconds": 999999}},
    ), str(run_dir))
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
    start = next(event for event in events if event["event_type"] == "attempt_started")
    assert start["payload"]["resource_budgets"]["generation"]["gpu_seconds"] == 3600


def test_v3_rejects_unknown_track_before_starting_ledger(tmp_path):
    with pytest.raises(LedgerError, match="unknown V3 track"):
        run(_Problem(), _base_cfg(protocol_v3=True, track="UNKNOWN"), str(tmp_path / "run"))


def test_v3_resource_caps_cannot_be_raised_by_run_config(tmp_path, monkeypatch):
    candidate = "capped candidate"

    def fake_caller(prompt: str, temperature: float) -> str:
        return f"```\n{candidate}\n```"

    monkeypatch.setattr("forge.loop.make_caller", lambda tier: fake_caller)
    monkeypatch.setattr("forge.loop.DedupIndex", _ExactDedup)
    run_dir = tmp_path / "run-caps"
    run_dir.mkdir()
    run(_Problem(), _base_cfg(
        protocol_v3=True,
        generations=1,
        batch_size=1,
        max_cheap_calls=1,
        max_attempts=1,
        resource_budgets={
            "generation": {
                "records": 999999,
                "input_tokens": 999999999,
                "output_tokens": 999999999,
            },
            "evaluator": {"calls": 999999},
        },
        max_evaluator_calls=999999,
    ), str(run_dir))
    event = json.loads((run_dir / "events.jsonl").read_text().splitlines()[0])
    budgets = event["payload"]["resource_budgets"]
    assert budgets["generation"]["records"] == 1
    assert budgets["generation"]["input_tokens"] == 4_194_304
    assert budgets["generation"]["output_tokens"] == 524_288
    assert budgets["evaluator"]["calls"] == 512
