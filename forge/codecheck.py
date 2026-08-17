"""候補Pythonコードの静的ゲート（AST検査）。

サンドボックス(forge.sandbox)は「親インタプリタを汚さない・暴走を殺す」ための隔離であって
セキュリティ境界ではない。実際に危険な構文を止めるのはこのゲートである。
部分文字列マッチ("import" in cand 等)は 'important' のような正常識別子で誤爆し、
逆に getattr(x, chr(95)*2 + 'class') のような迂回を素通しするため、ASTで判定する。
"""
from __future__ import annotations

import ast
import re

# ベンチマーク問題パックが必要とする数値ライブラリのみ許可する。
DEFAULT_ALLOWED_MODULES = frozenset({"numpy", "math"})

# ``numpy`` is allowed for numerical candidate code, but its file-backed
# helpers would let a candidate read hidden scores/instances or write outside
# the evaluator's intended output.  The external container remains the final
# security boundary; this AST gate makes the common direct escape explicit.
NUMPY_FILE_IO_NAMES = frozenset({
    "fromfile", "genfromtxt", "load", "loadtxt", "memmap", "open_memmap",
    "save", "savez", "savez_compressed", "savetxt", "tofile",
})

# 名前空間から辿れてしまうと隔離の意味が無くなる組み込み。
FORBIDDEN_NAMES = frozenset({
    "breakpoint", "compile", "delattr", "eval", "exec", "getattr", "globals",
    "input", "locals", "memoryview", "open", "setattr", "vars",
})


class CodeRejected(ValueError):
    """候補コードが静的ゲートで棄却された（＝候補の死であって計測器の故障ではない）。"""


_HACK_WORD_RE = re.compile(r"(?:hidden|secret|answer|score[_ -]?file|test[_ -]?data)", re.I)


def audit_candidate(source: str) -> dict[str, object]:
    """Return deterministic evaluator-hack signals without executing code.

    This is an audit taxonomy, not a security boundary. The V3 sandbox and an
    external container remain responsible for isolation; this function only
    records explicit hidden-data/file signals so they cannot disappear into a
    generic runtime error.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, TypeError) as exc:
        return {"parseable": False, "suspected_hack": False, "findings": [],
                "parse_error": type(exc).__name__}
    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and _HACK_WORD_RE.search(node.id):
            findings.append(f"name:{node.id}")
        elif isinstance(node, ast.Attribute) and _HACK_WORD_RE.search(node.attr):
            findings.append(f"attribute:{node.attr}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _HACK_WORD_RE.search(node.value):
                findings.append("string:hidden-data-signal")
    return {
        "parseable": True,
        "suspected_hack": bool(findings),
        "findings": sorted(set(findings)),
    }


def check_candidate(
    source: str,
    *,
    allowed_modules: frozenset[str] = DEFAULT_ALLOWED_MODULES,
    required_defs: tuple[str, ...] = (),
) -> ast.Module:
    """`source` を検査し、通ればASTを返す。通らなければ CodeRejected。

    LLM呼び出しもプロセス起動も伴わない親プロセス内の検査なので、
    棄却できる候補はここで棄却してサンドボックス起動コストを払わない。
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:  # ValueError: source中のnullバイト等
        raise CodeRejected(f"parse failed: {exc}") from None

    numpy_aliases: set[str] = set()
    numpy_imported_file_io_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "numpy":
                    numpy_aliases.add(alias.asname or "numpy")
        elif isinstance(node, ast.ImportFrom) and node.module == "numpy":
            for alias in node.names:
                if alias.name == "*" or alias.name in NUMPY_FILE_IO_NAMES:
                    numpy_imported_file_io_names.add(alias.asname or alias.name)

    def _attribute_chain(node: ast.Attribute) -> tuple[str, ...] | None:
        chain: list[str] = [node.attr]
        value = node.value
        while isinstance(value, ast.Attribute):
            chain.append(value.attr)
            value = value.value
        if isinstance(value, ast.Name):
            chain.append(value.id)
            return tuple(reversed(chain))
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # Only the explicitly allow-listed module objects are
                # available; ``import numpy.lib`` would expose file-backed
                # helpers despite sharing the numpy root.
                if alias.name not in allowed_modules:
                    raise CodeRejected(f"import not allowed: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if node.level or node.module not in allowed_modules:
                raise CodeRejected(f"import not allowed: {node.module!r}")
            if node.module == "numpy" and any(
                alias.name == "*" or alias.name in NUMPY_FILE_IO_NAMES
                for alias in node.names
            ):
                raise CodeRejected("numpy file I/O import is not allowed")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__"):
                raise CodeRejected(f"dunder attribute not allowed: {node.attr}")
            chain = _attribute_chain(node)
            if chain and chain[0] in numpy_aliases and any(
                part in NUMPY_FILE_IO_NAMES for part in chain[1:]
            ):
                raise CodeRejected("numpy file I/O attribute is not allowed")
            if node.attr in NUMPY_FILE_IO_NAMES:
                raise CodeRejected("file I/O attribute is not allowed")
        elif isinstance(node, ast.Name):
            if node.id in FORBIDDEN_NAMES:
                raise CodeRejected(f"name not allowed: {node.id}")
            if node.id.startswith("__"):
                raise CodeRejected(f"dunder name not allowed: {node.id}")
            if node.id in numpy_imported_file_io_names:
                raise CodeRejected("numpy file I/O name is not allowed")

    if required_defs:
        defined = {
            n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for name in required_defs:
            if name not in defined:
                raise CodeRejected(f"missing required def: {name}")

    return tree
