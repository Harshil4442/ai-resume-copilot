"""Keep the opt-in checkout lab separate from provider-free acceptance runs."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from backend.app.domains.credits.models import CreditCampaign
from backend.app.domains.credits.pricing import public_catalog
from backend.scripts.run_razorpay_test_checkout import (
    LAUNCH_PRICES,
    ROOT,
    CheckoutLabError,
    allowed_provider_request,
    load_wallet_state,
    lock_wallet_directory,
    provider_environment,
    runtime_environment,
    save_wallet_state,
    synthetic_campaign,
    wallet_identity,
    webhook_environment,
)
from sqlalchemy import create_engine, text
from test_mail_budget_postgres import owned_pg_url as owned_pg_url

TEST_AUTH = ("rzp_test_synthetic123", "synthetic-checkout-test-key")
TEST_WEBHOOK_SECRET = "a-separate-synthetic-webhook-secret"
API = "https://api.razorpay.com"
RECEIPT = "hw_ord_" + "a" * 32


def test_synthetic_launch_prices_expire_without_claiming_a_reference_price():
    now = datetime(2026, 11, 14, 9, tzinfo=UTC)
    campaign = CreditCampaign(**synthetic_campaign(now))
    offers = public_catalog(campaign=campaign, now=now).products
    assert {(row.sku, row.amount_minor, row.credits) for row in offers} == LAUNCH_PRICES
    assert all(row.campaign and not row.campaign.reference_price_verified for row in offers)
    assert campaign.ends_at - campaign.starts_at == timedelta(days=30)
    standard = public_catalog(campaign=campaign, now=campaign.ends_at).products
    assert [row.amount_minor for row in standard] == [7900, 22900, 59900]


def test_only_selected_environment_escapes_parent_process(tmp_path):
    parent = {"HOME": "/production", "PATH": "/bin", "DATABASE_URL": "production",
              "RAZORPAY_KEY_ID": TEST_AUTH[0], "RAZORPAY_CHECKOUT_ENABLED": "true",
              "HTTPS_PROXY": "http://proxy.example.invalid", "GEMINI_API_KEY": "synthetic"}
    assert runtime_environment(tmp_path, parent) == {"HOME": str(tmp_path), "PATH": "/bin"}
    supplied = provider_environment(*TEST_AUTH, TEST_WEBHOOK_SECRET)
    assert supplied["RAZORPAY_KEY_ID"] == TEST_AUTH[0]
    assert supplied["RAZORPAY_WEBHOOK_SECRET"] == TEST_WEBHOOK_SECRET


def test_test_refund_resume_only_loads_the_signing_secret():
    assert webhook_environment(TEST_WEBHOOK_SECRET) == {"RAZORPAY_WEBHOOK_SECRET": TEST_WEBHOOK_SECRET}
    with pytest.raises(ValueError):
        webhook_environment(" " + TEST_WEBHOOK_SECRET)


def test_retained_test_wallet_round_trip_matches_owned_cluster_and_schema(owned_pg_url, tmp_path):
    folder = tmp_path / "wallet"
    folder.mkdir(mode=0o700)
    record = folder / "wallet.json"
    schema = "razorpay_test_" + uuid4().hex
    revision = ScriptDirectory.from_config(Config(str(ROOT / "backend" / "alembic.ini"))).get_current_head()
    engine = create_engine(owned_pg_url, hide_parameters=True)
    try:
        with engine.begin() as db:
            db.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            db.exec_driver_sql(f'CREATE TABLE "{schema}".alembic_version (version_num varchar NOT NULL)')
            db.execute(text(f'INSERT INTO "{schema}".alembic_version VALUES (:revision)'), {"revision": revision})
        with lock_wallet_directory(folder), engine.begin() as db:
            db.execute(text("SET TRANSACTION READ ONLY"))
            identity = wallet_identity(db, schema)
            save_wallet_state(record, identity)
            with pytest.raises(BlockingIOError), lock_wallet_directory(folder):
                pytest.fail("Another process acquired the same Test wallet")
        with lock_wallet_directory(folder), engine.begin() as db:
            assert load_wallet_state(record, db) == schema
            other = folder / "other.json"
            save_wallet_state(other, identity | {"cluster": "0"})
            with pytest.raises(CheckoutLabError, match="different database"):
                load_wallet_state(other, db)
            with pytest.raises(FileExistsError):
                save_wallet_state(record, identity)
            record.chmod(0o644)
            with pytest.raises(CheckoutLabError, match="private regular file"):
                load_wallet_state(record, db)
    finally:
        with engine.begin() as db:
            db.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        engine.dispose()


@pytest.mark.parametrize(("key", "secret", "hook"), [
    ("rzp_live_synthetic123", TEST_AUTH[1], TEST_WEBHOOK_SECRET),
    ("rzp_test_", TEST_AUTH[1], TEST_WEBHOOK_SECRET),
    (TEST_AUTH[0], "", TEST_WEBHOOK_SECRET),
    (TEST_AUTH[0], " " + TEST_AUTH[1], TEST_WEBHOOK_SECRET),
    (*TEST_AUTH, "too-short"),
    (TEST_AUTH[0], TEST_WEBHOOK_SECRET, TEST_WEBHOOK_SECRET),
])
def test_interactive_configuration_rejects_live_or_incomplete_credentials(key, secret, hook):
    with pytest.raises(ValueError):
        provider_environment(key, secret, hook)


@pytest.mark.parametrize(("method", "path", "permitted"), [
    ("POST", "/v1/orders", True),
    ("GET", f"/v1/orders?receipt={RECEIPT}&count=100&skip=0", True),
    ("GET", f"/v1/orders?count=100&receipt={RECEIPT}&skip=100", True),
    ("GET", "/v1/orders/order_123", True),
    ("GET", "/v1/orders/order_123/payments", True),
    ("GET", "/v1/payments/pay_123", True),
    ("GET", "/v1/payments/pay_123/refunds?count=100&skip=100", True),
    ("POST", "/v1/orders?mode=live", False),
    ("GET", "/v1/orders", False),
    ("GET", "/v1/orders?receipt=hw_not_original&count=100&skip=0", False),
    ("GET", f"/v1/orders?receipt={RECEIPT}&count=100&skip=0&expand[]=payment", False),
    ("GET", f"/v1/orders?receipt={RECEIPT}&count=100&skip=0&skip=100", False),
    ("GET", f"/v1/orders?receipt={RECEIPT}&count=100&skip=200", False),
    ("GET", "/v1/orders/order_123/payments?count=100", False),
    ("GET", "/v1/payments/pay_123/refunds?count=100&skip=0&payment_id=foreign", False),
    ("GET", "/v1/payments/pay_123%2Frefunds?count=100&skip=0", False),
    ("POST", "/v1/payments/pay_123/capture", False),
    ("POST", "/v1/payments/pay_123/refund", False),
    ("DELETE", "/v1/orders/order_123", False),
])
def test_allowlist_matches_create_and_bounded_read_contract(method, path, permitted):
    assert allowed_provider_request(httpx.Request(method, API + path), TEST_AUTH, TEST_AUTH) is permitted


@pytest.mark.parametrize("url", [
    "http://api.razorpay.com/v1/orders",
    "https://api.razorpay.com.example.invalid/v1/orders",
    "https://api.razorpay.com:8443/v1/orders",
    "https://user@api.razorpay.com/v1/orders",
    "https://api.razorpay.com/v1/orders#other",
])
def test_provider_host_and_tls_are_exact(url):
    assert not allowed_provider_request(httpx.Request("POST", url), TEST_AUTH, TEST_AUTH)


@pytest.mark.parametrize("auth", [None, (TEST_AUTH[0], "wrong"), ("rzp_live_unknown", TEST_AUTH[1]),
                                  httpx.BasicAuth(*TEST_AUTH)])
def test_provider_auth_must_be_the_explicit_test_credentials(auth):
    assert not allowed_provider_request(httpx.Request("POST", API + "/v1/orders"), auth, TEST_AUTH)


def test_prebuilt_authorization_header_cannot_override_approved_auth():
    request = httpx.Request("POST", API + "/v1/orders", headers={"Authorization": "Basic synthetic"})
    assert not allowed_provider_request(request, TEST_AUTH, TEST_AUTH)
