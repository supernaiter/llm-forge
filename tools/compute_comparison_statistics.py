#!/usr/bin/env python3
"""Compute paired descriptive statistics for a matched comparison bundle."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forge.comparison_statistics import write_comparison_statistics  # noqa: E402
from forge.protocol import ProtocolError  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        output = write_comparison_statistics(args.bundle, args.out)
    except (OSError, ProtocolError, ValueError) as exc:
        print(f"DENIED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"output": str(output.resolve())}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
