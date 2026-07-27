from forge.archive import Archive


def test_archive_reloads_top_k(tmp_path):
    path = tmp_path / "archive.jsonl"

    archive = Archive(str(path), capacity=2)
    archive.add({"text": "low", "score": 1.0, "gen": 0})
    archive.add({"text": "high", "score": 5.0, "gen": 1})
    archive.add({"text": "mid", "score": 3.0, "gen": 2})

    assert [item["score"] for item in archive.items] == [5.0, 3.0]

    reloaded = Archive(str(path), capacity=2)
    assert [item["score"] for item in reloaded.items] == [5.0, 3.0]
    assert reloaded.best["text"] == "high"


def test_archive_tie_break_prefers_shorter_text(tmp_path):
    path = tmp_path / "archive.jsonl"

    archive = Archive(str(path), capacity=5, max_per_score=0)
    archive.add({"text": "a much longer implementation", "score": 5.0, "gen": 0})
    archive.add({"text": "short", "score": 5.0, "gen": 1})

    assert archive.items[0]["text"] == "short"
    assert archive.best["text"] == "short"


def test_same_score_clones_are_capped(tmp_path):
    path = tmp_path / "archive.jsonl"

    archive = Archive(str(path), capacity=20, max_per_score=3)
    for i in range(5):
        archive.add({"text": f"clone {i}", "score": 5.0, "gen": i})

    assert len(archive.items) == 3
    # 上限で残るのは短い順(_trimのtie-break)。全候補はJSONLに残っている。
    assert len(path.read_text(encoding="utf-8").splitlines()) == 5


def test_cap_frees_seats_for_lower_but_distinct_scores(tmp_path):
    """今回の崩壊の核心: 同点複製が親プールを占拠すると、劣るが別挙動の解が消える。"""
    path = tmp_path / "archive.jsonl"

    archive = Archive(str(path), capacity=4, max_per_score=2)
    for i in range(6):
        archive.add({"text": f"clone {i}", "score": 5.0, "gen": i})
    archive.add({"text": "different behaviour", "score": 1.0, "gen": 6})
    archive.add({"text": "another behaviour", "score": 2.0, "gen": 7})

    assert [item["score"] for item in archive.items] == [5.0, 5.0, 2.0, 1.0]
    assert archive.distinct_scores == 3


def test_capacity_still_applies_after_cap(tmp_path):
    path = tmp_path / "archive.jsonl"

    archive = Archive(str(path), capacity=2, max_per_score=3)
    for score in (5.0, 4.0, 3.0):
        archive.add({"text": f"cand {score}", "score": score, "gen": 0})

    assert [item["score"] for item in archive.items] == [5.0, 4.0]


def test_max_per_score_zero_keeps_legacy_behaviour(tmp_path):
    path = tmp_path / "archive.jsonl"

    archive = Archive(str(path), capacity=20, max_per_score=0)
    for i in range(5):
        archive.add({"text": f"clone {i}", "score": 5.0, "gen": i})

    assert len(archive.items) == 5
    assert archive.distinct_scores == 1


def test_cap_survives_reload(tmp_path):
    path = tmp_path / "archive.jsonl"

    archive = Archive(str(path), capacity=20, max_per_score=3)
    for i in range(5):
        archive.add({"text": f"clone {i}", "score": 5.0, "gen": i})

    reloaded = Archive(str(path), capacity=20, max_per_score=3)
    assert len(reloaded.items) == 3
