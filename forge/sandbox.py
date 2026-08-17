"""Fresh-interpreter helpers for executing candidate Python code.

The goal is to run untrusted candidate code in a spawned child process so any
module-level mutation stays out of the parent interpreter.
"""
from __future__ import annotations

import importlib
import multiprocessing as mp
from builtins import __import__ as _builtin_import

from .codecheck import CodeRejected, check_candidate


class SandboxError(RuntimeError):
    """Raised when candidate execution fails inside the sandbox."""


class SandboxTimeout(SandboxError):
    """Raised when candidate execution exceeds the sandbox timeout."""


V3_ALLOWED_MODULES = frozenset({"numpy", "math"})
V3_BUILTINS = {
    "__import__": None,
    "__name__": "__candidate__",
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "Exception": Exception,
    "float": float,
    "int": int,
    "isinstance": isinstance,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "range": range,
    "reversed": reversed,
    "round": round,
    "set": set,
    "sorted": sorted,
    "sum": sum,
    "tuple": tuple,
    "ValueError": ValueError,
    "zip": zip,
}


def _restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = str(name).split(".", 1)[0]
    if level or root not in V3_ALLOWED_MODULES:
        raise ImportError(f"module import denied by V3 sandbox: {name}")
    return _builtin_import(name, globals, locals, fromlist, level)


def _child_main(
    conn,
    source: str,
    entrypoint: str,
    args: tuple,
    kwargs: dict,
    globals_module: str | None,
    globals_factory: str | None,
    policy: str | None,
):
    try:
        if policy == "v3":
            try:
                check_candidate(source, allowed_modules=V3_ALLOWED_MODULES,
                                required_defs=(entrypoint,))
            except CodeRejected as exc:
                raise SandboxError(f"V3 static policy rejected candidate: {exc}") from exc
            restricted_builtins = dict(V3_BUILTINS)
            restricted_builtins["__import__"] = _restricted_import
            ns = {"__builtins__": restricted_builtins}
        else:
            ns = {}
        if globals_module and globals_factory:
            mod = importlib.import_module(globals_module)
            factory = getattr(mod, globals_factory)
            extra = factory()
            if not isinstance(extra, dict):
                raise SandboxError(
                    f"{globals_module}.{globals_factory}() must return dict"
                )
            ns.update(extra)
        exec(compile(source, "<cand>", "exec"), ns, ns)
        fn = ns.get(entrypoint)
        if not callable(fn):
            raise SandboxError(f"missing callable entrypoint: {entrypoint}")
        result = fn(*args, **kwargs)
    except BaseException as e:
        try:
            conn.send(("error", e.__class__.__name__, str(e)))
        except BaseException:
            pass
    else:
        conn.send(("ok", result))
    finally:
        try:
            conn.close()
        except BaseException:
            pass


def run_python_candidate(
    source: str,
    entrypoint: str,
    *,
    args: tuple = (),
    kwargs: dict | None = None,
    timeout: float = 5.0,
    globals_module: str | None = None,
    globals_factory: str | None = None,
    policy: str | None = None,
):
    """Execute `source` in a spawned child interpreter and return the result.

    `globals_module`/`globals_factory` let the child import a module-local
    factory that builds the sandbox globals without serializing module objects
    through the pipe.
    """
    kwargs = {} if kwargs is None else dict(kwargs)
    ctx = mp.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(
        target=_child_main,
        args=(
            child_conn,
            source,
            entrypoint,
            args,
            kwargs,
            globals_module,
            globals_factory,
            policy,
        ),
    )
    proc.start()
    child_conn.close()
    try:
        if not parent_conn.poll(timeout):
            proc.terminate()
            proc.join()
            raise SandboxTimeout(f"timeout after {timeout:.1f}s")
        try:
            status, *payload = parent_conn.recv()
        except EOFError as e:
            proc.join()
            raise SandboxError(f"child exited early: {proc.exitcode}") from e
        proc.join()
    finally:
        if proc.is_alive():
            proc.terminate()
            proc.join()
        try:
            parent_conn.close()
        except BaseException:
            pass

    if status == "ok":
        return payload[0]

    kind, message = payload
    raise SandboxError(f"{kind}: {message}")
