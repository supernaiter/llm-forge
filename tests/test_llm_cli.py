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


def test_json_cli_extracts_final_message_and_observed_usage():
    script = (
        "import json; "
        "print(json.dumps({'type':'item.completed','item':"
        "{'type':'agent_message','text':'```\\nanswer\\n```'}})); "
        "print(json.dumps({'type':'turn.completed','usage':"
        "{'input_tokens':123,'output_tokens':17}}))"
    )
    result = _cli_chat(
        [sys.executable, "-c", script, "--json", "{prompt}"],
        "request",
        timeout=30,
        return_metadata=True,
    )
    assert result["text"] == "```\nanswer\n```"
    usage = result["resource_usage"]
    assert usage["input_tokens"] == 123
    assert usage["output_tokens"] == 17
    assert usage["model_identity"] is None
    assert usage["sampling_profile"]["event_protocol"] == "jsonl"
    assert "cli_json_observed_token_usage" in usage["telemetry_notes"]


def test_route_identity_keeps_provider_identity_in_cli_telemetry(monkeypatch):
    script = (
        "import json; "
        "print(json.dumps({'type':'item.completed','item':"
        "{'type':'agent_message','text':'```\\nanswer\\n```'}})); "
        "print(json.dumps({'type':'turn.completed','usage':"
        "{'input_tokens':11,'output_tokens':7}}))"
    )
    monkeypatch.delenv("FORGE_MOCK", raising=False)
    monkeypatch.setenv(
        "FORGE_STRONG_CLI",
        json.dumps([sys.executable, "-c", script, "--json", "-m", "gpt-5.5", "{prompt}"]),
    )
    caller = make_caller("STRONG", model_identity="STRONG")
    result = caller.with_metadata("request", temperature=0.3)
    usage = result["resource_usage"]
    assert usage["model_identity"] == "STRONG"
    assert usage["sampling_profile"]["adapter_model_identity"] == "gpt-5.5"
    assert usage["sampling_profile"]["controller_route_identity"] == "STRONG"


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


def test_mock_structural_generic_ast_generation_is_deterministic_and_valid(monkeypatch):
    monkeypatch.setenv("FORGE_MOCK", "1")
    prompt = """Write a single function:
    def choose(current, values):
Registered mutation operator: structural
```python
import numpy as np

def choose(current, values):
    return -(values - current)

def choose(current, values):
    return -np.arange(len(values), dtype=float)
```"""
    first = make_caller("cheap", seed=17)(prompt, temperature=1.4)
    second = make_caller("cheap", seed=17)(prompt, temperature=0.1)
    assert first == second
    assert "** 2" in first
    assert "priority" not in first
    assert "select_next_node" not in first

    from forge.verify import extract_block
    from forge.codecheck import check_candidate

    candidate = extract_block(first)
    check_candidate(candidate, required_defs=("choose",))


def test_mock_structural_single_parent_keeps_a_valid_generic_baseline(monkeypatch):
    monkeypatch.setenv("FORGE_MOCK", "1")
    prompt = """def choose(current, values):
Registered mutation operator: structural
```python
import numpy as np

def choose(current, values):
    return -(values - current)
```"""
    output = make_caller("cheap", seed=17)(prompt, temperature=1.4)
    assert "* 0" in output
    assert "priority" not in output


def test_mock_global_generation_is_sensitive_to_vector_and_matrix_shapes(monkeypatch):
    monkeypatch.setenv("FORGE_MOCK", "1")
    priority_prompt = """Write a single function:
def priority(item, bins):
Registered mutation operator: global
```
import numpy as np

def priority(item, bins):
    return -(bins - item)
```
"""
    route_prompt = """Write a single function:
def select_next_node(current_node, destination_node, unvisited_nodes, distance_matrix):
Registered mutation operator: global
```
import numpy as np

def select_next_node(current_node, destination_node, unvisited_nodes, distance_matrix):
    return unvisited_nodes[np.argmin(
        distance_matrix[current_node][unvisited_nodes]
    )]
```
"""
    from forge.codecheck import check_candidate
    from forge.verify import extract_block

    priority = extract_block(make_caller("cheap", seed=3)(priority_prompt, 0.8))
    route = extract_block(make_caller("cheap", seed=3)(route_prompt, 0.8))
    check_candidate(priority, required_defs=("priority",))
    check_candidate(route, required_defs=("select_next_node",))
    assert "score[1:]" in priority
    assert "_forge_onward" in route


def test_mock_structural_matrix_shape_mutation_is_problem_name_independent(monkeypatch):
    monkeypatch.setenv("FORGE_MOCK", "1")
    prompt = """Write a single function:
def choose_step(a, b, c, d):
Registered mutation operator: structural
```
import numpy as np

def choose_step(a, b, c, d):
    return c[np.argmin(d[a][c])]
def choose_step(a, b, c, d):
    return c[np.argmin(d[b][c])]
```
"""
    output = make_caller("cheap", seed=3)(prompt, temperature=0.8)
    assert "_ast_plan_matrix" in output
    assert "select_next_node" not in output
    assert "problem_id" not in output
    assert "trace" not in output.lower()

    from forge.verify import extract_block
    from forge.codecheck import check_candidate

    check_candidate(extract_block(output), required_defs=("choose_step",))


def test_mock_detailed_usage_has_explicit_fixture_token_counts(monkeypatch):
    monkeypatch.setenv("FORGE_MOCK", "1")
    caller = make_caller("cheap")
    detailed = caller.with_metadata("```\nseed words\n```", temperature=0.5)
    usage = detailed["resource_usage"]
    assert usage["input_tokens"] == 4
    assert usage["output_tokens"] >= 1
    assert usage["sampling_profile"]["tokenizer_id"] == "MOCK_WHITESPACE_V1"
    assert "input_tokens" not in usage["missing"]
    assert "output_tokens" not in usage["missing"]
