import json
import time

from forge.loop import run


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
