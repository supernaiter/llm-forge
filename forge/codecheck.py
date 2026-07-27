"""候補Pythonコードの静的ゲート（AST検査）。

サンドボックス(forge.sandbox)は「親インタプリタを汚さない・暴走を殺す」ための隔離であって
セキュリティ境界ではない。実際に危険な構文を止めるのはこのゲートである。
部分文字列マッチ("import" in cand 等)は 'important' のような正常識別子で誤爆し、
逆に getattr(x, chr(95)*2 + 'class') のような迂回を素通しするため、ASTで判定する。
"""
from __future__ import annotations

import ast

# ベンチマーク問題パックが必要とする数値ライブラリのみ許可する。
DEFAULT_ALLOWED_MODULES = frozenset({"numpy", "math"})

# 名前空間から辿れてしまうと隔離の意味が無くなる組み込み。
FORBIDDEN_NAMES = frozenset({
    "breakpoint", "compile", "delattr", "eval", "exec", "getattr", "globals",
    "input", "locals", "memoryview", "open", "setattr", "vars",
})


class CodeRejected(ValueError):
    """候補コードが静的ゲートで棄却された（＝候補の死であって計測器の故障ではない）。"""


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

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in allowed_modules:
                    raise CodeRejected(f"import not allowed: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if node.level or root not in allowed_modules:
                raise CodeRejected(f"import not allowed: {node.module!r}")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__"):
                raise CodeRejected(f"dunder attribute not allowed: {node.attr}")
        elif isinstance(node, ast.Name):
            if node.id in FORBIDDEN_NAMES:
                raise CodeRejected(f"name not allowed: {node.id}")
            if node.id.startswith("__"):
                raise CodeRejected(f"dunder name not allowed: {node.id}")

    if required_defs:
        defined = {
            n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for name in required_defs:
            if name not in defined:
                raise CodeRejected(f"missing required def: {name}")

    return tree
