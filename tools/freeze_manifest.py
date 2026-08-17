#!/usr/bin/env python3
"""Freeze a draft V3 manifest without changing its scientific contents."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forge.manifest import freeze_manifest  # noqa: E402
from forge.protocol import strict_json_loads  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Freeze a Forge V3 manifest.")
    parser.add_argument("input")
    parser.add_argument("output")
    args = parser.parse_args(argv)
    source = Path(args.input)
    target = Path(args.output)
    try:
        draft = strict_json_loads(source.read_text(encoding="utf-8"))
        frozen = freeze_manifest(draft)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"DENIED: {exc}", file=sys.stderr)
        return 2
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(frozen, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    print(json.dumps({"frozen": True, "manifest_sha256": frozen["manifest_sha256"]},
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
