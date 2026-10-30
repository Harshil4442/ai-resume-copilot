#!/usr/bin/env python3
"""Explicit one-send native SERVICE capture; plan/dry-run are inert.

Development40 only. No Job, model, database, native approval or retail authority.
Never print provider exceptions, candidate text, credentials or raw responses.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "dry-run", "execute", "status", "export"))
    parser.add_argument("--packet", type=Path, default=ROOT / "evals/unified_credit_v2")
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument("--maximum-requests", type=int, default=1)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    try:
        sys.path.insert(0, str(ROOT))
        from app.domains.analysis import native_input_acquisition as acquisition

        auth = acquisition.Authorization.model_validate_json(args.authorization.read_bytes())
        common = (args.packet, args.native, auth, args.authorization.parent)
        if args.command in {"plan", "dry-run"}:
            acquisition.need(not args.initialize, "initialize_only_execute")
            result = acquisition.plan(*common)
        else:
            acquisition.need(args.journal is not None, "journal_required")
            if args.command == "execute":
                result = acquisition.execute(*common, args.journal, maximum_requests=args.maximum_requests,
                    initialize=args.initialize)
            elif args.command == "status":
                acquisition.need(not args.initialize, "initialize_only_execute")
                result = acquisition.inspect_local(*common, args.journal)
            else:
                acquisition.need(not args.initialize and args.destination is not None, "fresh_export_destination_required")
                result = acquisition.export_unreviewed(*common, args.journal, args.destination)
        print(json.dumps(result, indent=2))
        return 0
    except (Exception, KeyboardInterrupt):
        print(json.dumps({"error": "Native acquisition refused or outcome unknown. Inspect the same retained journal; never resend an existing intent.",
                          "replay_authorized": False, "authorizes_retail": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
