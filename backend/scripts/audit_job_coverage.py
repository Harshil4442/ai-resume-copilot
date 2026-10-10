"""Offline JSON audit: python scripts/audit_job_coverage.py INPUT [--output PATH]."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domains.coverage_audit.audit import audit  # noqa: E402
from app.domains.coverage_audit.contracts import AuditInput, draw_sample  # noqa: E402


def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def reject_nonfinite_number(_value: str) -> None:
    raise ValueError("Nonfinite JSON number")


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline independent-reference coverage audit")
    parser.add_argument("input", type=Path, nargs="?")
    parser.add_argument("--schema", action="store_true", help="Print the strict input JSON Schema")
    parser.add_argument("--draw-only", action="store_true", help="Print the deterministic whole-employer draw")
    parser.add_argument("--output", type=Path, help="Write JSON to a local file instead of stdout")
    args = parser.parse_args()
    try:
        if args.schema:
            result = AuditInput.model_json_schema()
        else:
            if args.input is None:
                parser.error("input is required unless --schema is used")
            raw = args.input.read_bytes()
            if len(raw) > 20 * 1024 * 1024:
                raise ValueError("Audit input exceeds the 20 MiB offline bound")
            decoded = json.loads(raw, object_pairs_hook=reject_duplicate_keys,
                                 parse_constant=reject_nonfinite_number)
            data = AuditInput.model_validate(decoded)
            result = draw_sample(data.employers, data.strata, data.seed) if args.draw_only else audit(data)
        encoded = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
        if args.output:
            args.output.write_text(encoded, encoding="utf-8")
        else:
            sys.stdout.write(encoded)
        return 0
    except ValidationError as error:
        sys.stderr.write(json.dumps({"error": "invalid_audit_input", "issues": error.errors(
            include_input=False, include_url=False, include_context=False,
        )}) + "\n")
    except (OSError, ValueError):
        sys.stderr.write('{"error":"invalid_audit_input_or_local_file"}\n')
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
