"""Print a private aggregate snapshot without mutations or provider calls."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.database import SessionLocal  # noqa: E402
from app.domains.credits.runtime import RuntimeConfig  # noqa: E402
from app.domains.credits.shadow import ReportingReview, report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    review = parser.add_mutually_exclusive_group(required=True)
    review.add_argument("--runtime-review", type=Path)
    review.add_argument("--reporting-review", type=Path)
    parser.add_argument("--start", type=datetime.fromisoformat, required=True)
    parser.add_argument("--end", type=datetime.fromisoformat, required=True)
    args = parser.parse_args()
    try:
        config = (
            RuntimeConfig.model_validate_json(args.runtime_review.read_text())
            if args.runtime_review is not None
            else ReportingReview.model_validate_json(args.reporting_review.read_text())
        )
        with SessionLocal() as db:
            if db.get_bind().dialect.name == "postgresql":
                db.connection().exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            result = report(db, config=config, start=args.start, end=args.end)
            db.rollback()
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    except Exception:
        # Never reflect a DB URL, private input or configuration in an operator log.
        print(json.dumps({"error": "credit_shadow_observation_unavailable"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
