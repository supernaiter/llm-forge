"""Fresh-interpreter helpers for executing candidate Python code.

The goal is to run untrusted candidate code in a spawned child process so any
module-level mutation stays out of the parent interpreter.
"""
from __future__ import annotations

import importlib
import multiprocessing as mp


class SandboxError(RuntimeError):
    """Raised when candidate execution fails inside the sandbox."""


class SandboxTimeout(SandboxError):
    """Raised when candidate execution exceeds the sandbox timeout."""


def _child_main(
    conn,
    source: str,
    entrypoint: str,
    args: tuple,
    kwargs: dict,
    globals_module: str | None,
    globals_factory: str | None,
):
    try:
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
