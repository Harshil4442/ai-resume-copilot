#!/usr/bin/env python3
"""Create a new frozen synthetic corpus without changing the v1 packet."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.domains.analysis.quality_supported_v2 import build, verify_lineage  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "evals/unified_credit_v1")
    parser.add_argument("--destination", type=Path, default=ROOT / "evals/unified_credit_v2")
    args = parser.parse_args()
    try:
        build(args.source, args.destination)
        result = verify_lineage(args.source, args.destination)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["source_lineage_valid"] else 2
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error_type": type(exc).__name__, "authorizes_release": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
