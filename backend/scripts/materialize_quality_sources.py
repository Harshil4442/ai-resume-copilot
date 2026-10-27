#!/usr/bin/env python3
"""Materialize frozen synthetic sources offline; never render, infer or approve."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.domains.analysis.quality_materialization import materialize  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, default=ROOT / "evals/unified_credit_v1")
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--fonts", type=Path, help="JSON object: exact font family to local TTF path; no downloads")
    args = parser.parse_args()
    try:
        supplied = json.loads(args.fonts.read_text()) if args.fonts else {}
        if not isinstance(supplied, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in supplied.items()):
            raise ValueError("Fonts must map family names to local file paths")
        result = materialize(args.packet, args.destination, fonts={name: Path(path) for name, path in supplied.items()}, case_ids=args.case_ids)
        print(json.dumps({key: result[key] for key in ("state", "case_count", "materialized_count", "quality_status", "authorizes_release")}))
        return 0 if result["case_count"] == result["materialized_count"] else 2
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc), "authorizes_release": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
