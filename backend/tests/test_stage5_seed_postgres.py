"""Opt-in fresh owned PG17 cardinality check, not emulator/HTTP/native proof."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text

from scripts.run_stage5_capacity import migrate
from scripts.stage5_capacity import Plan
from scripts.stage5_first_minute import Read, order_id, owned_response, seed_pending, upload_id
from scripts.stage5_resources import Resources, private_json
from scripts.stage5_seed import owned_opportunity_ids, seed, synthetic_email
from scripts.stage5_skill_roi import checked_workspace, expected_roi


@pytest.mark.parametrize("buyers_percent", [20, 25])
def test_owned_pg17_migrated_seed_and_real_factory_account_ownership(tmp_path, monkeypatch, buyers_percent):
    configured = os.getenv("HIREWIZ_STAGE5_PG_BIN")
    if not configured:
        if os.getenv("HIREWIZ_STAGE5_PG_REQUIRED") == "true":
            pytest.fail("Stage5 requires explicit fresh owned PG17 binaries")
        pytest.skip("Set HIREWIZ_STAGE5_PG_BIN for the separate owned PG17 seed check")
    from app import database
    from app.domains.career import service as career
    from app.domains.career import workspace
    from app.domains.career.evidence_write_fence import SETTING
    from app.domains.credits import service as credits
    from app.domains.resume_uploads import service as uploads
    from app.routers import credits as credit_routes
    from tests import test_candidate_production_factory as fixture

    resource = Resources(tmp_path / "owned", Path(configured), Path("/unused-for-this-sql-check"))
    generator = engine = None
    evidence = None
    try:
        resource.start_postgres()
        monkeypatch.setattr(database, "DATABASE_URL", resource.dsn)
        identity = migrate(resource)
        assert identity["schema_oid"] > 0 and identity["server_version"] >= 170000
        engine = create_engine(resource.dsn, hide_parameters=True, pool_size=4, max_overflow=2)
        monkeypatch.setattr(fixture, "create_engine", lambda *args, **kwargs: engine)
        generator = fixture.configured.__wrapped__(monkeypatch, resource.directory)
        case = next(generator)
        for uid in range(1, 9):
            assert case.accounts.register(email=synthetic_email(uid), password=fixture.PASSWORD,
                policy_version="2026-10-08") == uid
        data = seed(engine, Plan(buyers_percent=buyers_percent))
        assert data["opportunities"] == 100490 and data["candidate_password_accounts"] == 8
        assert data["credit_accounts"] == 10000 * buyers_percent // 100
        assert data["evidence_items"] == 30000
        pending = seed_pending(engine, Plan(buyers_percent=buyers_percent))
        assert pending == {"first_minute_pending_payment_orders": 1 if buyers_percent == 20 else 2,
                           "first_minute_queued_uploads": 2}
        monkeypatch.setenv("RESUME_DIRECT_UPLOAD_ENABLED", "true")
        with case.accounts.sessions() as db:
            assert db.scalar(text("SELECT current_setting(:setting, true)"), {"setting": SETTING}) in (None, "")
            assert career.list_opportunities(db, 8, limit=50)[1] == 500
            assert career.list_opportunities(db, 7, limit=50)[1] == 10
            for uid in (1, 8):
                role = owned_opportunity_ids(uid, limit=1)[0]
                original = workspace.bootstrap(db, uid, role, tab="learning").model_dump(mode="json")
                combined = workspace.bootstrap(db, uid, role, tab="learning",
                    include_skill_roi=True).model_dump(mode="json")
                assert checked_workspace(original, uid) == checked_workspace(combined, uid) is not None
                assert original["skill_roi"] is None
                assert combined["skill_roi"] == career.calculate_skill_roi(db, uid).model_dump(mode="json") == expected_roi(uid)
            buyer = 5 if buyers_percent == 20 else 4
            order = credit_routes._owned_order(db, buyer, order_id(buyer))
            assert owned_response(Read("payment", buyer, 0), credit_routes._status(db, order, synthetic_email(buyer)),
                                  buyers_percent)
            assert owned_response(Read("upload", 2, 5), uploads.status(db, 2, upload_id(2)).model_dump(mode="json"),
                                  buyers_percent)
            for read_foreign in (lambda: credit_routes._owned_order(db, 1, order_id(buyer)),
                                 lambda: uploads.status(db, 1, upload_id(2))):
                with pytest.raises(HTTPException) as denied:
                    read_foreign()
                assert denied.value.status_code == 404
            assert credits.wallet(db, user_id=buyer).available_credits == 90
            assert credits.wallet(db, user_id=1).available_credits == 0
            assert db.execute(text("SELECT count(*) FROM users WHERE password_hash<>''")).scalar_one() == 8
        evidence = {"scope": "owned_pg17_seed_and_production_factory_intercepted_native_only",
                    "buyers_percent": buyers_percent, "identity": identity, "data": data}
    finally:
        if generator is not None:
            generator.close()
        if engine is not None:
            engine.dispose()
        resource.close()
        assert resource.cleanup[0].get("verified_stopped") is True
        assert resource.cleanup[-1].get("pg_scratch_removed") is True
        assert not (resource.directory / "pg-data").exists()
        if evidence is not None and os.getenv("HIREWIZ_STAGE5_SEED_PROOF_DIR"):
            output = Path(os.environ["HIREWIZ_STAGE5_SEED_PROOF_DIR"])
            assert output.is_dir() and not output.is_symlink() and output.stat().st_uid == os.getuid()
            assert output.stat().st_mode & 0o777 == 0o700
            private_json(output / f"seed-{buyers_percent}.json", {**evidence, "cleanup": resource.cleanup})
