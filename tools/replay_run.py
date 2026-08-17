#!/usr/bin/env python3
"""Verify and summarize a recorded V3 event ledger without executing code."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forge.replay import replay_summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay a Forge V3 event ledger.")
    parser.add_argument("ledger")
    args = parser.parse_args()
    print(json.dumps(replay_summary(args.ledger), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
