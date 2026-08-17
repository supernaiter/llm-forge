import pytest

from forge.sandbox import SandboxError, run_python_candidate


def test_v3_policy_allows_math_and_rejects_network_filesystem_and_process_imports():
    source = "import math\ndef f(x):\n    return math.sqrt(x)\n"
    assert run_python_candidate(source, "f", args=(9,), policy="v3") == 3.0
    for module in ("os", "socket", "subprocess", "urllib.request"):
        source = f"import {module}\ndef f():\n    return 1\n"
        with pytest.raises(SandboxError):
            run_python_candidate(source, "f", policy="v3")


def test_v3_policy_rejects_open_and_dunder_escape_attempts():
    with pytest.raises(SandboxError):
        run_python_candidate("def f():\n    return open('secret')\n", "f", policy="v3")
    with pytest.raises(SandboxError):
        run_python_candidate("def f(x):\n    return x.__class__\n", "f", args=(1,), policy="v3")


def test_v3_policy_rejects_numpy_file_backed_access():
    with pytest.raises(SandboxError):
        run_python_candidate(
            "import numpy as np\ndef f(path):\n    return np.load(path)\n",
            "f",
            args=("hidden.npy",),
            policy="v3",
        )


def test_legacy_policy_remains_available_only_for_legacy_callers():
    source = "def f(x):\n    return x * 2\n"
    assert run_python_candidate(source, "f", args=(21,)) == 42
