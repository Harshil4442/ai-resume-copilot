#!/usr/bin/env python3
"""Bounded internal calibration; execute is explicit, dry-run never opens a DB."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.domains.analysis import development_runner as runner  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("dry-run", "prepare", "execute", "export"))
    parser.add_argument("--packet", type=Path, default=ROOT / "evals/unified_credit_v1")
    parser.add_argument("--native", type=Path)
    parser.add_argument("--native-receipts", type=Path)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--side", choices=runner.SIDES)
    parser.add_argument("--maximum-operations", type=int, default=1)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    factory = None
    try:
        auth = runner.load_authorization(args.authorization)
        root = args.authorization.parent
        if args.command != "export" and args.native is None:
            parser.error("--native is required")
        if args.command == "dry-run":
            result = runner.preflight(args.packet, args.native, auth, root, args.native_receipts)
        else:
            factory = runner.local_factory(os.environ.get("HIREWIZ_EVALUATION_DATABASE_URL", ""), auth)
            if args.command == "export":
                if args.destination is None:
                    parser.error("--destination must be a new directory")
                result = runner.export_results(factory, auth, args.destination, root)
            else:
                if args.native_receipts is None:
                    parser.error("--native-receipts is required before creating any runs")
                if args.command == "prepare":
                    result = runner.prepare(factory, args.packet, args.native, auth, root, args.native_receipts)
                else:
                    if args.side is None:
                        parser.error("--side is required")
                    result = runner.execute(factory, args.packet, args.native, auth, root, args.native_receipts,
                                            side=args.side, maximum_operations=args.maximum_operations)
        print(json.dumps(result, indent=2))
        return 2 if result.get("state") == "blocked" or "failed" in result.get("outcomes", {}).values() else 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        # Configuration/database exceptions may contain credentials. Never echo them.
        print(json.dumps({"error": "Calibration preflight or operation failed; no automatic retry was performed.",
                          "authorizes_sales": False}), file=sys.stderr)
        return 2
    finally:
        if factory:
            factory.kw["bind"].dispose()


if __name__ == "__main__":
    raise SystemExit(main())
