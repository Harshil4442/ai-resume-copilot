"""Exact27 catalog/obligation readback on a newly created owned PostgreSQL17 DB."""
from __future__ import annotations

import pytest
from infra.gcp import schema27_app_successor as app
from sqlalchemy import text
from test_mail_budget_postgres import owned_pg_url as owned_pg_url
from test_notification_schema27_step_postgres import FLAGS, ROOT, snapshots
from test_notification_schema27_step_postgres import owned26 as owned26
from test_schema27_continuity_catalog_postgres import NONCE
from test_schema27_continuity_catalog_postgres import connection_scope as connection_scope


def test_owned_exact27_readonly_snapshot_retains_old_balances_and_unknown_expense(owned26, connection_scope):
    database, identity = connection_scope
    with pytest.raises(app.scope.neon.retained.NeonIdentityDenied, match="exact_schema_mismatch"):
        app.observe_catalog27(database, NONCE)
    with owned26.engine.connect() as db:
        before = snapshots(db)
        liability = app.catalog.financial.liability_pins(db)
        acl = app.catalog.retained.old_acls(db, identity)
    # Existing migration is fixture preparation only; the new app controller has
    # no migration port, bootstrap credential, DDL command or issuance journal.
    with owned26.engine.begin() as db:
        app.catalog.retained.upgrade_transaction(db, identity, root=ROOT, flags=FLAGS)
    result = app.observe_catalog27(database, NONCE, expected_liabilities=liability)
    assert result.retained_acl_sha256 == app.rolling.sha(acl)
    assert result.protected_liabilities == liability and len(liability) == 1
    with owned26.engine.connect() as db:
        assert snapshots(db) == before
        assert app.catalog.retained.old_acls(db, identity) == acl
    with app.catalog.authenticated_observer(database, NONCE, app.scope.neon.Purpose.AFTER_SCHEMA27,
                                            recovery=True) as db:
        assert db.scalar(text("SHOW transaction_read_only")) == "on"
        assert app.read_catalog27(db, expected_liabilities=liability) == result
        db.rollback()
        with pytest.raises(app.rolling.RollingDenied, match="catalog_session_unverified"):
            app.read_catalog27(db)
    with pytest.raises(app.rolling.RollingDenied, match="protected_liability_changed"):
        app.observe_catalog27(database, NONCE, expected_liabilities=())
    # A forged observed-role pin is refused by the stock physical role check.
    original = app.catalog.neon.OBSERVER_OID
    app.catalog.neon.OBSERVER_OID = identity.runtime_oid
    try:
        with pytest.raises(app.scope.neon.retained.NeonIdentityDenied):
            app.observe_catalog27(database, NONCE)
    finally:
        app.catalog.neon.OBSERVER_OID = original
