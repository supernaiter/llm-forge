"""forge.codecheck の単体テスト。

ここが素通しすると、候補コードは隔離されていない子プロセスで実行される。
部分文字列マッチでは止まらない/誤爆する形を中心に押さえる。
"""
import pytest

from forge.codecheck import CodeRejected, check_candidate


def test_allows_numpy_and_math():
    check_candidate("import numpy as np\nimport math\nx = np.pi + math.e\n")


def test_rejects_other_imports():
    for source in ("import os\n", "import subprocess\n", "from pathlib import Path\n",
                   "from . import sibling\n", "import numpy.linalg\n",
                   "import numpy.lib.npyio\n", "import numpy.linalg, socket\n"):
        with pytest.raises(CodeRejected):
            check_candidate(source)


def test_rejects_builtin_escapes():
    for source in ("x = eval('1')\n", "x = open('f')\n", "x = getattr(object, 'a')\n",
                   "x = ().__class__\n", "x = __import__('os')\n"):
        with pytest.raises(CodeRejected):
            check_candidate(source)


def test_rejects_numpy_file_io_but_allows_numeric_submodules():
    check_candidate(
        "import numpy as np\n"
        "import math\n"
        "def f(x):\n"
        "    return np.linalg.norm(x) + np.random.rand() + math.e\n"
    )
    for source in (
        "import numpy as np\ndef f(x):\n    return np.load(x)\n",
        "import numpy as np\ndef f(x):\n    return np.lib.format.open_memmap(x)\n",
        "from numpy import load\ndef f(x):\n    return load(x)\n",
        "import numpy as np\ndef f(x):\n    return x.tofile('hidden')\n",
    ):
        with pytest.raises(CodeRejected):
            check_candidate(source)


def test_rejects_syntax_error():
    with pytest.raises(CodeRejected):
        check_candidate("def f(:\n")


def test_identifier_containing_import_is_not_rejected():
    """部分文字列マッチだと誤爆する形。ASTなら通る。"""
    check_candidate("important = 1\nexported_value = important\n")


def test_required_defs_enforced():
    check_candidate("def priority(a, b):\n    return a\n", required_defs=("priority",))
    with pytest.raises(CodeRejected):
        check_candidate("def other(a, b):\n    return a\n", required_defs=("priority",))
