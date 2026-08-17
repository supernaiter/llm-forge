#!/usr/bin/env python3
"""Audit whether a comparison bundle supports a paper-level surpass claim."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forge.paper_claim import audit_paper_claim  # noqa: E402
from forge.protocol import ProtocolError, canonical_json  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--strict", action="store_true", help="return 1 unless the native claim gate passes")
    args = parser.parse_args(argv)
    try:
        report = audit_paper_claim(args.bundle)
    except (OSError, ProtocolError, ValueError) as exc:
        print(f"DENIED: {exc}", file=sys.stderr)
        return 2
    payload = canonical_json(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(payload)
    else:
        print(payload.decode("utf-8"), end="")
    if args.strict and report["gates"]["native_surpass_claim_ready"] is not True:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
