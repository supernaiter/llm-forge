#!/usr/bin/env python3
"""Fit a V3 controller on development traces and emit a frozen policy manifest.

This tool is deliberately a development-only operation.  It accepts JSONL
traces marked ``split=dev`` and refuses holdout rows.  The resulting manifest
contains the exact action space, aggregate utilities, training problem IDs,
source-trace hash, and self-hash used by holdout runs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forge.controller import (  # noqa: E402
    SearchAction,
    controller_for_mechanism,
    write_controller_manifest,
)
from forge.protocol import ProtocolError, strict_json_loads  # noqa: E402


def _load_actions(path: Path) -> list[SearchAction]:
    try:
        value = strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProtocolError(f"cannot read actions JSON: {path}") from exc
    if not isinstance(value, list) or not value:
        raise ProtocolError("actions JSON must be a non-empty list")
    actions = []
    for raw in value:
        if not isinstance(raw, dict):
            raise ProtocolError("each action must be an object")
        try:
            actions.append(SearchAction(**{
                field: raw[field] for field in SearchAction.__dataclass_fields__
            }))
        except KeyError as exc:
            raise ProtocolError(f"action missing field: {exc.args[0]}") from exc
    return actions


def _load_traces(path: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ProtocolError(f"cannot read development traces: {path}") from exc
    traces = []
    for line_no, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = strict_json_loads(line)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProtocolError(f"invalid trace JSON at line {line_no}: {exc}") from exc
        if not isinstance(value, dict):
            raise ProtocolError(f"trace at line {line_no} is not an object")
        if value.get("split") != "dev":
            raise ProtocolError(f"trace at line {line_no} is not development-only")
        traces.append(value)
    if not traces:
        raise ProtocolError("development trace file is empty")
    return traces


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--traces",
        type=Path,
        action="append",
        required=True,
        help="development JSONL trace file; may be supplied more than once",
    )
    parser.add_argument("--actions", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--exclude-problem",
        action="append",
        default=[],
        help="exclude a problem ID from the development traces before fitting",
    )
    parser.add_argument(
        "--mechanism",
        default="TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V1",
        choices=(
            "TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V1",
            "TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V2",
            "FIXED_DEV_BEST",
            "NO_TRANSFER_PRIOR",
            "COST_UNAWARE_CONTROLLER",
        ),
    )
    parser.add_argument("--manifest-id", default="FORGE_CONTROLLER_POLICY_V3_1")
    args = parser.parse_args(argv)

    try:
        actions = _load_actions(args.actions)
        excluded = {value.strip() for value in args.exclude_problem if value.strip()}
        loaded_traces = []
        for trace_path in args.traces:
            loaded_traces.extend(_load_traces(trace_path))
        traces = [
            trace for trace in loaded_traces
            if trace.get("problem_id") not in excluded
        ]
        if not traces:
            raise ProtocolError("no development traces remain after problem exclusion")
        controller = controller_for_mechanism(args.mechanism, actions)
        controller.fit(traces)
        controller.freeze()
        source_digest = hashlib.sha256()
        for trace_path in args.traces:
            source_digest.update(trace_path.read_bytes())
            source_digest.update(b"\n")
        source_hash = source_digest.hexdigest()
        manifest = write_controller_manifest(
            controller,
            args.out,
            source_traces_sha256=source_hash,
            manifest_id=args.manifest_id,
        )
    except (OSError, ProtocolError, ValueError) as exc:
        print(f"DENIED: {exc}", file=sys.stderr)
        return 2

    print(json.dumps({
        "manifest": str(args.out),
        "manifest_sha256": manifest["manifest_sha256"],
        "policy_sha256": manifest["policy_sha256"],
        "mechanism_id": manifest["mechanism_id"],
        "training_problem_ids": manifest["controller_training_problem_ids"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
