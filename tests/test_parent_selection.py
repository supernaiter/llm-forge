"""sample_parents の score_spread（2026-07-25 追加）の検証。

背景。同日のA/B実測で2つの事実が確定した。

1. 親プールが同点複製で埋まると探索が閉ループに入る
   （bench_obp: 親プールのスコアが1〜3種類に潰れた2シードは20世代160呼び出しで改善ゼロ）。
2. かといってプールの構成をいじると質の下限が消える
   （Archive.max_per_score=3 を試したら空いた席に-5000.0級の壊滅的候補が入り、
   親プールの4〜5割を占めて3シード全敗した）。

よって score_spread は「プールは触らず、見せ方だけを多様化し、
野生枠にも質の下限を置く」という設計になっている。ここを固定する。
"""
import random

from forge.operators import sample_parents


def _clones_then_stragglers() -> list[dict]:
    """スコア降順のアーカイブ。上位が同点複製、末尾に壊滅的候補。"""
    archive = [{"text": f"clone {i}", "score": -2091.8} for i in range(20)]
    archive += [{"text": "second behaviour", "score": -2093.8}]
    archive += [{"text": "third behaviour", "score": -2099.4}]
    archive += [{"text": f"disaster {i}", "score": -5000.0} for i in range(18)]
    return archive


def test_legacy_default_is_unchanged():
    """既定は従来動作。実測で確認するまで既定を変えない。"""
    archive = _clones_then_stragglers()
    legacy = sample_parents(archive, 3, random.Random(0))
    assert legacy[0] is archive[0]
    assert legacy[1] is archive[1]
    assert len(legacy) == 3


def test_legacy_collapses_to_one_behaviour():
    """崩壊の再現: エリート枠が同点複製で埋まる。"""
    archive = _clones_then_stragglers()
    parents = sample_parents(archive, 3, random.Random(0), score_spread=False)
    assert len({p["score"] for p in parents[:2]}) == 1


def test_score_spread_picks_distinct_scores_from_the_top():
    archive = _clones_then_stragglers()
    parents = sample_parents(archive, 3, random.Random(0), score_spread=True)
    assert [p["score"] for p in parents[:2]] == [-2091.8, -2093.8]
    assert len({p["score"] for p in parents}) >= 2


def test_score_spread_never_shows_the_bottom_half_as_a_parent():
    """壊滅的候補を手本として見せないこと(前回の失敗の直接の再発防止)。"""
    archive = _clones_then_stragglers()
    for seed in range(50):
        parents = sample_parents(archive, 3, random.Random(seed), score_spread=True)
        assert all(p["score"] > -5000.0 for p in parents), parents


def test_score_spread_keeps_the_best_candidate_first():
    archive = _clones_then_stragglers()
    parents = sample_parents(archive, 3, random.Random(7), score_spread=True)
    assert parents[0] is archive[0]


def test_score_spread_fills_seats_when_scores_are_few():
    """スコアの種類が足りなくても親の数は減らさない(手本が痩せる)。"""
    archive = [{"text": f"clone {i}", "score": 1.0} for i in range(5)]
    parents = sample_parents(archive, 3, random.Random(0), score_spread=True)
    assert len(parents) == 3


def test_score_spread_handles_tiny_archive():
    assert sample_parents([], 3, random.Random(0), score_spread=True) == []
    single = [{"text": "only", "score": 1.0}]
    assert sample_parents(single, 3, random.Random(0), score_spread=True) == single
