from forge.dedup import DedupIndex, ExactDedup


def test_same_text_is_not_novel_twice():
    idx = DedupIndex()

    assert idx.is_novel("same text")
    assert not idx.is_novel("same text")


def test_exact_dedup_only_rejects_identical_text():
    idx = ExactDedup()

    assert idx.is_novel("x=0.501")
    assert idx.is_novel("x=0.499")  # numerically close but not identical -> still novel
    assert not idx.is_novel("x=0.501")  # exact repeat -> not novel
