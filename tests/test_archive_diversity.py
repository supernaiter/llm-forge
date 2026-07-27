"""2026-07-25 bench_obp で観測した探索崩壊の回帰テスト。

崩壊の機序:
  SimHash(dedup.py)は文面しか見ないので、同じ挙動のプログラムが別の書き方で何度も通る
  → Archive._trim がスコア順に top-K を残すため、top-K が同点複製で埋まる
  → sample_parents がそこから親を引くので、LLMには毎世代ほぼ同一の親しか見えない
  → 同じものが返る閉ループ。dup率は低いまま(文面は毎回違う)なので既存の警報は鳴らない。

実測: 生きたアーカイブ40枠のスコアが1〜3種類に潰れた2シードは改善ゼロ、
      10種類だった1シードだけが人手のbest-fitを超えた。

LLMを一切呼ばない決定論テストとして固定する。
"""
import random

from forge.archive import Archive
from forge.operators import sample_parents


def _fill_with_clones_and_stragglers(archive: Archive) -> None:
    """best-fit相当の同点複製50件と、劣るが挙動の違う解を数件入れる。"""
    for i in range(50):
        archive.add({"text": f"best fit rewritten {i}", "score": -2091.8, "gen": i})
    for i, score in enumerate((-2093.8, -2099.4, -2104.4)):
        archive.add({"text": f"different behaviour {i}", "score": score, "gen": 50 + i})


def test_without_cap_the_parent_pool_collapses_to_one_behaviour(tmp_path):
    archive = Archive(str(tmp_path / "a.jsonl"), capacity=40, max_per_score=0)
    _fill_with_clones_and_stragglers(archive)

    assert archive.distinct_scores == 1, "崩壊の再現: 親プールが単一挙動で埋まる"

    rng = random.Random(0)
    parents = sample_parents(archive.items, 3, rng)
    assert len({p["score"] for p in parents}) == 1


def test_cap_keeps_more_than_one_behaviour_in_the_parent_pool(tmp_path):
    archive = Archive(str(tmp_path / "b.jsonl"), capacity=40, max_per_score=3)
    _fill_with_clones_and_stragglers(archive)

    assert archive.distinct_scores == 4
    assert archive.best["score"] == -2091.8, "最良解は必ず残る"

    rng = random.Random(0)
    parents = sample_parents(archive.items, 3, rng)
    assert len({p["score"] for p in parents}) >= 2


def test_cap_never_drops_the_best_candidate(tmp_path):
    """多様性のために最良解を捨てたら本末転倒。上限は最良解より下から効く。"""
    archive = Archive(str(tmp_path / "c.jsonl"), capacity=5, max_per_score=1)
    archive.add({"text": "winner", "score": 10.0, "gen": 0})
    for i in range(20):
        archive.add({"text": f"clone {i}", "score": 1.0, "gen": i + 1})

    assert archive.best["text"] == "winner"
    assert [item["score"] for item in archive.items] == [10.0, 1.0]
