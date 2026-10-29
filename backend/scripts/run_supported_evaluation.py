#!/usr/bin/env python3
"""Explicit internal v2 quality execution; default dry-run opens no database.

Funding and native captures require separate operator review. Neither synthetic
tests nor this command qualify a retail route. Errors never echo credentials.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "dry-run", "prepare", "execute", "export"))
    parser.add_argument("--packet", type=Path, default=ROOT / "evals/unified_credit_v2")
    parser.add_argument("--native", type=Path)
    parser.add_argument("--native-receipts", type=Path)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--side", choices=("reference", "candidate"))
    parser.add_argument("--maximum-operations", type=int, default=1)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    factory = None
    try:
        # Application imports/configuration may fail with private DSNs in their
        # exceptions. Keep them inside this fixed sanitization boundary.
        sys.path.insert(0, str(ROOT))
        from app.domains.analysis import supported_evaluation as runner
        from app.domains.analysis import supported_evaluation_plan as inputs

        auth = inputs.Authorization.model_validate_json(args.authorization.read_bytes())
        root = args.authorization.parent
        if args.command == "plan":
            result = inputs.plan(args.packet, auth)
        else:
            if args.command != "export" and args.native is None:
                raise ValueError("Missing native inputs")
            if args.command == "dry-run":
                result = runner.preflight(args.packet, args.native, auth, root, args.native_receipts)
            else:
                factory = runner.local_factory(os.environ.get("HIREWIZ_EVALUATION_DATABASE_URL", ""), auth)
                if args.command == "export":
                    if args.destination is None:
                        raise ValueError("Missing destination")
                    result = runner.export_results(factory, auth, args.destination, root)
                else:
                    if args.native_receipts is None:
                        raise ValueError("Missing native receipts")
                    if args.command == "prepare":
                        result = runner.prepare(factory, args.packet, args.native, auth, root, args.native_receipts)
                    else:
                        result = runner.execute(factory, args.packet, args.native, auth, root, args.native_receipts,
                                                side=args.side, maximum_operations=args.maximum_operations)
        print(json.dumps(result, indent=2))
        return 2 if result.get("state") == "blocked" or "failed" in result.get("outcomes", {}).values() else 0
    except Exception:
        print(json.dumps({"error": "Evaluation preflight or operation failed; no automatic retry was performed.",
                          "authorizes_retail": False}), file=sys.stderr)
        return 2
    finally:
        if factory is not None:
            factory.kw["bind"].dispose()


if __name__ == "__main__":
    raise SystemExit(main())
