#!/usr/bin/env python3
"""Read-only verification of an externally frozen Forge V3 study bundle."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forge.study_verifier import verify_bundle  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify a frozen Forge V3 bundle without writing to it."
    )
    parser.add_argument("bundle", help="read-only frozen bundle directory")
    parser.add_argument(
        "--repository-protocol",
        default=str(ROOT / "protocol" / "forge_research_v3.json"),
        help="protocol used to detect post-freeze drift",
    )
    args = parser.parse_args(argv)
    report = verify_bundle(args.bundle, repository_protocol=args.repository_protocol)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["research_finished"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
