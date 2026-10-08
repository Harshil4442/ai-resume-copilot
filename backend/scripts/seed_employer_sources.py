"""Load an explicitly reviewed employer registry. Dry run unless --apply.

Example: python scripts/seed_employer_sources.py --reviewer-email operator@example.com
No employer is contacted and no write grant is created by a default dry run.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import models as core  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.domains.common import public_id, utcnow  # noqa: E402
from app.domains.employer.models import EmployerSource  # noqa: E402
from app.domains.employer.schemas import SourceCreate  # noqa: E402


def load_reviewed_sources(path: Path, *, include_submission_grants: bool = False) -> list[SourceCreate]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = raw.get("sources") if isinstance(raw, dict) else raw
    if not isinstance(entries, list) or not 1 <= len(entries) <= 1000:
        raise ValueError("Registry must contain 1–1000 reviewed sources")
    sources = []
    for entry in entries:
        entry = dict(entry)
        if not include_submission_grants:
            entry.update(submission_enabled=False, submission_grant=None, credential_env=None,
                         form_parity_verified=False, receipt_contract=None)
        source = SourceCreate.model_validate(entry)
        # Operator still confirms evidence independently; arbitrary candidate URLs
        # never enroll themselves into the company source registry.
        from urllib.parse import urlsplit
        for url in (source.careers_url, source.verification_url):
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("Reviewed source evidence must use HTTPS")
        if urlsplit(source.careers_url).hostname not in source.allowed_hosts:
            raise ValueError("Employer careers hostname must be explicitly allowed")
        sources.append(source)
    if len({(source.platform, source.region, source.board_token) for source in sources}) != len(sources):
        raise ValueError("Registry includes duplicate employer tenants")
    return sources


def seed(db, sources: list[SourceCreate], *, reviewer_email: str, include_submission_grants: bool = False) -> dict:
    reviewer = db.query(core.User).filter_by(email=reviewer_email.lower()).first()
    result = {"created": 0, "updated": 0}
    write_fields = {"submission_enabled", "submission_grant", "credential_env", "form_parity_verified", "receipt_contract"}
    for reviewed in sources:
        source = db.query(EmployerSource).filter_by(platform=reviewed.platform, region=reviewed.region,
                                                   board_token=reviewed.board_token).with_for_update().first()
        if source:
            values = reviewed.model_dump(exclude=write_fields if not include_submission_grants else set())
            result["updated"] += 1
            for key, value in values.items():
                setattr(source, key, value)
            source.verified_at, source.next_refresh_at = utcnow(), utcnow()
        else:
            source = EmployerSource(id=public_id("source"), verified_by=reviewer.id if reviewer else None,
                                    **reviewed.model_dump())
            db.add(source)
            result["created"] += 1
        db.add(core.AdminAuditEvent(id=public_id("audit"), actor_user_id=reviewer.id if reviewer else None,
            actor_email=reviewer_email.lower(), action="seed_reviewed_employer_source", target_type="employer_source",
            target_id=source.id, reason=reviewed.verification_note,
            after_state={"platform": reviewed.platform, "tenant": reviewed.board_token,
                         "submission_grants_explicitly_included": include_submission_grants}))
    db.commit()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, default=ROOT / "resources/employer_sources.reviewed.json")
    parser.add_argument("--reviewer-email", required=True)
    parser.add_argument("--apply", action="store_true", help="Apply reviewed registry to the configured database")
    parser.add_argument("--include-submission-grants", action="store_true", help="Explicitly process contracted employer grants already supplied in reviewed JSON")
    args = parser.parse_args()
    if "@" not in args.reviewer_email:
        parser.error("A reviewer identity is required for the audit ledger")
    sources = load_reviewed_sources(args.file, include_submission_grants=args.include_submission_grants)
    if not args.apply:
        print(json.dumps({"dry_run": True, "reviewed_sources": len(sources), "submission_grants_included": args.include_submission_grants}))
        return
    with SessionLocal() as db:
        print(json.dumps(seed(db, sources, reviewer_email=args.reviewer_email,
                              include_submission_grants=args.include_submission_grants)))


if __name__ == "__main__":
    main()
