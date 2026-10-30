"""Retrieve, verify or continue only the completed 41108319542 native candidate."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend"), str(ROOT / "infra/gcp")]


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--mode", choices=("retrieve", "verify", "resume", "select-history"), required=True)
    parser.add_argument("--original-directory", type=Path)
    parser.add_argument("--output-directory", type=Path)
    parser.add_argument("--consumed-history-bundle", type=Path)
    parser.add_argument("--history-directory", type=Path)
    args = parser.parse_args()
    try:
        from infra.gcp import document_source_readback_resume as resume

        if args.mode in {"retrieve", "verify", "resume"}:
            resume.need(args.original_directory is not None, "arguments_refused")
        if args.mode == "retrieve":
            resume.need(args.output_directory is None and args.consumed_history_bundle is None
                        and args.history_directory is None, "arguments_refused")
            resume.retrieve(args.original_directory)
        elif args.mode == "verify":
            resume.need(args.output_directory is None and args.consumed_history_bundle is None
                        and args.history_directory is None, "arguments_refused")
            resume.verify_local(resume.read_tree(args.original_directory), ROOT)
        elif args.mode == "resume":
            resume.need(args.output_directory is not None and args.history_directory is not None, "arguments_refused")
            resume.resume(args.original_directory, args.output_directory, args.consumed_history_bundle,
                          args.history_directory, root=ROOT)
        else:
            resume.need(args.original_directory is None and args.consumed_history_bundle is None
                        and args.history_directory is not None and args.output_directory is not None, "arguments_refused")
            result = resume.select_history(args.history_directory, args.output_directory, root=ROOT)
            print(json.dumps(result))
            return 0
    except Exception as error:  # noqa: BLE001 - do not format SDK errors, credentials or raw responses.
        value = str(error)
        code = value if re.fullmatch(r"document_[a-z0-9_]{1,150}", value) else "document_source_readback_unavailable"
        failure = {"status": "read_only_reconciliation_required", "fixed_code": code,
                   "automatic_replay": False, "exception_class": type(error).__name__}
        if args.output_directory is not None and args.output_directory.is_dir():
            target = args.output_directory / "failure-diagnostic.json"
            if not target.exists():
                with target.open("x", encoding="utf-8") as stream:
                    json.dump(failure, stream, sort_keys=True)
        print(json.dumps(failure), file=sys.stderr)
        return 1
    print(json.dumps({"status": "completed", "mode": args.mode, "automatic_replay": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
