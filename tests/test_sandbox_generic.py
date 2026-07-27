"""forge.sandbox のエンジン単体テスト(ドメイン問題に非依存)。

旧 test_sandbox_subprocess.py / test_verify.py はドメイン固有パック向けの
検証だったため、そのパックと共に別リポジトリへ移動した。本ファイルが
「サンドボックス最低1本」の要件を汎用形で引き継ぐ。
"""
import pytest

from forge.sandbox import SandboxError, SandboxTimeout, run_python_candidate


def test_result_roundtrip():
    out = run_python_candidate("def f(x):\n    return x * 2\n", "f", args=(21,))
    assert out == 42


def test_infinite_loop_times_out():
    with pytest.raises(SandboxTimeout):
        run_python_candidate(
            "def f():\n    while True:\n        pass\n", "f", timeout=1.0
        )


def test_candidate_exception_becomes_sandbox_error():
    with pytest.raises(SandboxError):
        run_python_candidate("def f():\n    raise ValueError('boom')\n", "f")


def test_missing_entrypoint_is_error():
    with pytest.raises(SandboxError):
        run_python_candidate("x = 1\n", "f")


def test_child_module_mutation_does_not_leak_to_parent():
    import forge.sandbox as sb

    src = (
        "def f():\n"
        "    import forge.sandbox as sb\n"
        "    sb.SandboxError.leaked = True\n"
        "    return True\n"
    )
    assert run_python_candidate(src, "f") is True
    assert not hasattr(sb.SandboxError, "leaked")
