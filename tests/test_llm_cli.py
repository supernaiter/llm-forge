"""サブスク契約CLI経由の呼び出し(FORGE_{TIER}_CLI)の検証。

Claude Max / ChatGPT Pro の枠は従量課金APIキーでは通らず、CLI経由でしか使えない。
smart層は1走行数回しか呼ばないので、HTTPより遅くても契約枠で賄えるほうが得になる。
実プロセスを起動する形で固定する(モックではCLI引数の組み立てミスを捕まえられない)。
"""
import json
import sys

import pytest

from forge.llm import _cli_chat, make_caller


def _echo_argv(*extra: str) -> list[str]:
    """標準出力にプロンプトをそのまま返す最小のCLI。"""
    return [sys.executable, "-c", "import sys; print(sys.argv[1])", *extra]


def test_prompt_placeholder_is_substituted():
    out = _cli_chat([sys.executable, "-c", "import sys; print(sys.argv[1])", "{prompt}"],
                    "hello judge", timeout=30)
    assert out == "hello judge"


def test_prompt_is_appended_when_placeholder_absent():
    out = _cli_chat([sys.executable, "-c", "import sys; print(sys.argv[1])"],
                    "appended prompt", timeout=30)
    assert out == "appended prompt"


def test_nonzero_exit_becomes_error():
    with pytest.raises(RuntimeError, match="cli exit"):
        _cli_chat([sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"],
                  "x", timeout=30)


def test_empty_output_is_an_error():
    """空応答を通すと、候補ゼロの世代を「成功」として予算計上してしまう。"""
    with pytest.raises(RuntimeError, match="empty output"):
        _cli_chat([sys.executable, "-c", "pass"], "x", timeout=30)


def test_timeout_is_enforced():
    with pytest.raises(RuntimeError, match="cli timeout"):
        _cli_chat([sys.executable, "-c", "import time; time.sleep(30)"], "x", timeout=1)


def test_missing_binary_becomes_error():
    with pytest.raises(RuntimeError, match="cli launch failed"):
        _cli_chat(["forge-no-such-binary-12345"], "x", timeout=5)


def test_make_caller_prefers_cli_over_http(monkeypatch):
    monkeypatch.delenv("FORGE_MOCK", raising=False)
    monkeypatch.setenv("FORGE_SMART_CLI", json.dumps(
        [sys.executable, "-c", "import sys; print('judged: ' + sys.argv[1])", "{prompt}"]))
    # HTTP側も設定しておき、CLIが優先されることを確かめる
    monkeypatch.setenv("FORGE_SMART_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("FORGE_SMART_MODEL", "unused")

    caller = make_caller("smart")
    assert caller("top solutions", temperature=0.3) == "judged: top solutions"


def test_mock_still_wins_over_cli(monkeypatch):
    """--mock走行がサブスクCLIを叩き始めたら事故。"""
    monkeypatch.setenv("FORGE_MOCK", "1")
    monkeypatch.setenv("FORGE_SMART_CLI", json.dumps(["forge-no-such-binary-12345"]))
    caller = make_caller("smart")
    assert isinstance(caller("```\nseed\n```", temperature=0.5), str)
