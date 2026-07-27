"""島モデル(FunSearch中核機構)の検証。

単一集団では、一度上位が特定の挙動で埋まると抜け出せない張り付きが起きる
(2026-07-26実測: 60世代440呼び出しを使って1度も改善しなかった走行があった)。
島モデルは独立した部分集団をN個並行に育て、定期的に弱い島を強い島の解で作り直す。

islands=1 で従来と完全に同じ挙動になること(後方互換)を必ず固定する。
"""
import json

import pytest

from forge.archive import Archive
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


def _cfg(**over):
    cfg = {
        "generations": 1, "batch_size": 1, "max_cheap_calls": 1, "max_smart_calls": 0,
        "archive_capacity": 10, "parents": 1, "workers": 1, "seed": 0,
    }
    cfg.update(over)
    return cfg


# --- Archive の島分離 ---

def test_islands_share_one_jsonl_but_keep_separate_pools(tmp_path):
    path = str(tmp_path / "a.jsonl")
    a0, a1 = Archive(path, island=0), Archive(path, island=1)
    a0.add({"text": "from-zero", "score": 1.0, "gen": 1})
    a1.add({"text": "from-one", "score": 9.0, "gen": 1})

    assert [c["text"] for c in a0.items] == ["from-zero"]
    assert [c["text"] for c in a1.items] == ["from-one"]
    assert len(open(path, encoding="utf-8").read().splitlines()) == 2


def test_reload_filters_by_island(tmp_path):
    path = str(tmp_path / "a.jsonl")
    Archive(path, island=0).add({"text": "zero", "score": 1.0, "gen": 1})
    Archive(path, island=1).add({"text": "one", "score": 2.0, "gen": 1})

    assert [c["text"] for c in Archive(path, island=1).items] == ["one"]
    assert [c["text"] for c in Archive(path, island=0).items] == ["zero"]


def test_legacy_rows_without_island_belong_to_island_zero(tmp_path):
    """島モデル導入前のarchive.jsonlを読み込んでも壊れないこと。"""
    path = tmp_path / "a.jsonl"
    path.write_text(json.dumps({"text": "old", "score": 1.0, "gen": 1}) + "\n",
                    encoding="utf-8")
    assert [c["text"] for c in Archive(str(path), island=0).items] == ["old"]
    assert Archive(str(path), island=1).items == []


def test_reset_to_keeps_the_jsonl_history(tmp_path):
    path = str(tmp_path / "a.jsonl")
    a = Archive(path, island=2)
    a.add({"text": "old-a", "score": 1.0, "gen": 1})
    a.add({"text": "old-b", "score": 2.0, "gen": 1})
    a.reset_to({"text": "donor", "score": 9.0, "gen": 5})

    assert [c["text"] for c in a.items] == ["donor"]
    assert a.items[0]["island"] == 2, "移住先の島番号で保存されること"
    # 旧住民2行 + 境界行 + 新住民1行。追記型なので過去は消さない。
    assert len(open(path, encoding="utf-8").read().splitlines()) == 4


def test_reset_survives_a_reload(tmp_path):
    """再開時に移住前の集団が復活したら島モデルが無効化される。"""
    path = str(tmp_path / "a.jsonl")
    a = Archive(path, island=1)
    a.add({"text": "old-a", "score": 1.0, "gen": 1})
    a.add({"text": "old-b", "score": 2.0, "gen": 1})
    a.reset_to({"text": "donor", "score": 9.0, "gen": 5})
    a.add({"text": "after", "score": 3.0, "gen": 6})

    reloaded = Archive(path, island=1)
    assert [c["text"] for c in reloaded.items] == ["donor", "after"]


# --- ループ側 ---

def test_single_island_is_backwards_compatible(tmp_path, monkeypatch):
    cand = "a longer novel candidate phrase with distinct tokens"
    monkeypatch.setattr("forge.loop.make_caller", lambda tier: (lambda p, temperature: f"```\n{cand}\n```"))
    monkeypatch.setattr("forge.loop.DedupIndex", _ExactDedup)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    best = run(_Problem(), _cfg(), str(run_dir))

    rows = [json.loads(l) for l in (run_dir / "archive.jsonl").read_text().splitlines() if l.strip()]
    assert best["text"] == cand
    assert {r["island"] for r in rows} == {0}
    assert json.loads((run_dir / "result.json").read_text())["islands"] == 1


def test_seeds_are_planted_on_every_island(tmp_path, monkeypatch):
    monkeypatch.setattr("forge.loop.make_caller", lambda tier: (lambda p, temperature: ""))
    monkeypatch.setattr("forge.loop.DedupIndex", _ExactDedup)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    run(_Problem(), _cfg(islands=4, generations=1, batch_size=4, max_cheap_calls=4),
        str(run_dir))

    rows = [json.loads(l) for l in (run_dir / "archive.jsonl").read_text().splitlines() if l.strip()]
    seeds = [r for r in rows if r["gen"] == 0]
    assert {r["island"] for r in seeds} == {0, 1, 2, 3}


def test_candidates_are_filed_under_their_home_island(tmp_path, monkeypatch):
    calls = {"n": 0}

    def caller(prompt, temperature):
        calls["n"] += 1
        return f"```\ncandidate number {calls['n']} distinct\n```"

    monkeypatch.setattr("forge.loop.make_caller", lambda tier: caller)
    monkeypatch.setattr("forge.loop.DedupIndex", _ExactDedup)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    run(_Problem(), _cfg(islands=3, generations=1, batch_size=3, max_cheap_calls=3),
        str(run_dir))

    rows = [json.loads(l) for l in (run_dir / "archive.jsonl").read_text().splitlines() if l.strip()]
    born = [r for r in rows if r["gen"] == 1]
    assert {r["island"] for r in born} == {0, 1, 2}, "候補が全島に散っていない"


def test_migration_rebuilds_the_weak_islands(tmp_path, monkeypatch):
    """弱い島が強い島の解で作り直されること。長い候補ほど高スコアなので、
    島0だけに長い候補を与え、他島には短い候補を与える。"""
    slot = {"i": 0}

    def caller(prompt, temperature):
        i = slot["i"] % 2
        slot["i"] += 1
        return "```\n" + ("L" * 60 if i == 0 else "s" * 5) + f"{slot['i']}\n```"

    monkeypatch.setattr("forge.loop.make_caller", lambda tier: caller)
    monkeypatch.setattr("forge.loop.DedupIndex", _ExactDedup)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    run(_Problem(), _cfg(islands=2, generations=2, batch_size=2, max_cheap_calls=4,
                         migrate_every=2), str(run_dir))

    a1 = Archive(str(run_dir / "archive.jsonl"), island=1)
    assert len(a1.items) == 1, "移住後の島は1体だけになる"
    assert a1.items[0]["score"] > 50, "強い島の解で作り直されていない"


def test_migration_is_off_by_default(tmp_path, monkeypatch):
    slot = {"i": 0}

    def caller(prompt, temperature):
        slot["i"] += 1
        return "```\n" + ("L" * 60 if slot["i"] % 2 else "s" * 5) + f"{slot['i']}\n```"

    monkeypatch.setattr("forge.loop.make_caller", lambda tier: caller)
    monkeypatch.setattr("forge.loop.DedupIndex", _ExactDedup)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    run(_Problem(), _cfg(islands=2, generations=2, batch_size=2, max_cheap_calls=4),
        str(run_dir))

    a1 = Archive(str(run_dir / "archive.jsonl"), island=1)
    assert len(a1.items) > 1, "migrate_every未指定で島が作り直されている"
