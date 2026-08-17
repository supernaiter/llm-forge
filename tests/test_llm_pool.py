"""FORGE_{TIER}_POOL（呼び出しごとにモデルを散らす多様性オペレータ）の検証。

温度ジッタ・プロンプト摂動・親サンプリング・SSoTは、いずれも同じモデルの中で散らす手法で、
モデル族の書き癖という相関だけは残る。POOLはそこを切るために毎回起点を回す。

FALLBACKS（失敗時に先頭から順に代替）とは意味が違うので、両者の挙動差をここで固定する。
"""
import json

import pytest

from forge import llm


@pytest.fixture
def record_calls(monkeypatch):
    seen = []

    def fake_chat(base_url, api_key, model, prompt, temperature, thinking="", timeout=30):
        seen.append(model)
        if model.startswith("dead"):
            raise RuntimeError("boom")
        return f"from {model}"

    monkeypatch.setattr(llm, "_chat", fake_chat)
    monkeypatch.delenv("FORGE_MOCK", raising=False)
    for suffix in ("POOL", "FALLBACKS", "CLI", "BASE_URL", "MODEL", "API_KEY", "TIMEOUT"):
        monkeypatch.delenv(f"FORGE_CHEAP_{suffix}", raising=False)
    return seen


def _endpoints(*models):
    return json.dumps([{"base_url": f"http://x/{m}", "model": m} for m in models])


def test_pool_rotates_across_models(monkeypatch, record_calls):
    monkeypatch.setenv("FORGE_CHEAP_POOL", _endpoints("a", "b", "c"))
    caller = llm.make_caller("cheap")
    outs = [caller("p", 0.8) for _ in range(6)]
    assert outs == ["from a", "from b", "from c", "from a", "from b", "from c"]


def test_fallbacks_do_not_rotate(monkeypatch, record_calls):
    """FALLBACKSは代替であって分散ではない。常に先頭が使われる。"""
    monkeypatch.setenv("FORGE_CHEAP_FALLBACKS", _endpoints("a", "b", "c"))
    caller = llm.make_caller("cheap")
    assert [caller("p", 0.8) for _ in range(3)] == ["from a"] * 3


def test_pool_still_falls_back_when_one_endpoint_dies(monkeypatch, record_calls):
    """1つ落ちても走行を止めない。分散と冗長は両立する。"""
    monkeypatch.setenv("FORGE_CHEAP_POOL", _endpoints("dead1", "alive"))
    caller = llm.make_caller("cheap")
    assert caller("p", 0.8) == "from alive"
    assert caller("p", 0.8) == "from alive"


def test_pool_raises_when_every_endpoint_is_dead(monkeypatch, record_calls):
    monkeypatch.setenv("FORGE_CHEAP_POOL", _endpoints("dead1", "dead2"))
    caller = llm.make_caller("cheap")
    with pytest.raises(RuntimeError):
        caller("p", 0.8)


def test_pool_beats_other_transports(monkeypatch, record_calls):
    monkeypatch.setenv("FORGE_CHEAP_POOL", _endpoints("pooled"))
    monkeypatch.setenv("FORGE_CHEAP_FALLBACKS", _endpoints("fallback"))
    monkeypatch.setenv("FORGE_CHEAP_BASE_URL", "http://x/plain")
    monkeypatch.setenv("FORGE_CHEAP_MODEL", "plain")
    assert llm.make_caller("cheap")("p", 0.8) == "from pooled"


def test_mock_still_wins_over_pool(monkeypatch, record_calls):
    monkeypatch.setenv("FORGE_MOCK", "1")
    monkeypatch.setenv("FORGE_CHEAP_POOL", _endpoints("a", "b"))
    assert isinstance(llm.make_caller("cheap")("```\nseed\n```", 0.5), str)
    assert record_calls == [], "mock走行が実エンドポイントを叩いた"


def test_mock_is_reproducible_for_a_declared_seed(monkeypatch):
    monkeypatch.setenv("FORGE_MOCK", "1")
    prompt = "```\nseed candidate\n```"
    first = llm.make_caller("cheap", seed=41)
    second = llm.make_caller("cheap", seed=41)
    first_outputs = [first(prompt, 0.8) for _ in range(5)]
    second_outputs = [second(prompt, 0.8) for _ in range(5)]
    assert first_outputs == second_outputs
