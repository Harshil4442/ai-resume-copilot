"""Start the isolated local Razorpay checkout lab. Default mode has no provider access."""
from __future__ import annotations

import argparse
import fcntl
import getpass
import json
import os
import re
import secrets
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from alembic.config import Config
from alembic.script import ScriptDirectory
from backend.app.billing.reconciliation import MAX_PAGES, PAGE_SIZE
from backend.scripts.razorpay_test_webhook_ingress import WEBHOOK_PATH
from backend.scripts.run_cold_browser_journey import (
    ROOT,
    _create_owned_tls,
    _disposable_postgres,
    _owned_status,
    _port,
    _run_bounded,
    _spawn_owned,
    _stop,
)
from backend.tests.fixtures.cold_browser_credit_policy import environment as credit_policy
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

LAUNCH_CATALOG_VERSION = "inr-unified-2026-10-27-v1"
LAUNCH_PRICES = frozenset({
    ("starter_pass", 5900, 40),
    ("growth_pass", 17900, 130),
    ("scale_pass", 44900, 360),
})


class CheckoutLabError(RuntimeError):
    """A safe, fixed operator message."""


@contextmanager
def lock_wallet_directory(directory: Path):
    require(directory.is_absolute() and directory.resolve() == directory,
            "Use an absolute path to the private Test wallet directory")
    fd = os.open(directory, os.O_DIRECTORY | os.O_NOFOLLOW | os.O_RDONLY)
    try:
        owner = os.fstat(fd)
        require(owner.st_uid == os.getuid() and stat.S_IMODE(owner.st_mode) == 0o700,
                "The Test wallet directory must be owner-only")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def wallet_identity(db, schema: str) -> dict[str, str]:
    require(bool(re.fullmatch(r"razorpay_test_[a-f0-9]{32}", schema)), "Invalid Test wallet schema")
    owner = db.scalar(text("SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = :schema AND nspowner = "
                           "(SELECT oid FROM pg_catalog.pg_roles WHERE rolname = current_user)"), {"schema": schema})
    require(owner == 1, "The retained Test wallet is missing or has a different owner")
    revision = db.execute(text(f'SELECT version_num FROM "{schema}".alembic_version')).scalar_one()
    expected = ScriptDirectory.from_config(Config(str(ROOT / "backend" / "alembic.ini"))).get_current_head()
    require(revision == expected, "The retained Test wallet requires its original migration revision")
    database, cluster = db.execute(text("SELECT pg_catalog.current_database(), system_identifier::text "
                                        "FROM pg_catalog.pg_control_system()")).one()
    return {"schema": schema, "database": database, "cluster": cluster, "revision": revision}


def save_wallet_state(path: Path, identity: dict[str, str]) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_EXCL | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(identity, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def load_wallet_state(path: Path, db) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        status = os.fstat(stream.fileno())
        require(stat.S_ISREG(status.st_mode) and status.st_uid == os.getuid()
                and stat.S_IMODE(status.st_mode) == 0o600 and status.st_size <= 1024,
                "The Test wallet record must be a private regular file")
        record = json.loads(stream.read(1025))
    require(type(record) is dict and type(record.get("schema")) is str, "Invalid Test wallet record")
    schema = record["schema"]
    require(wallet_identity(db, schema) == record, "The retained Test wallet belongs to a different database")
    return schema


def webhook_environment(webhook_secret: str) -> dict[str, str]:
    if len(webhook_secret) < 32 or webhook_secret != webhook_secret.strip():
        raise ValueError("Enter a separate webhook secret of at least 32 characters")
    return {"RAZORPAY_WEBHOOK_SECRET": webhook_secret}


def provider_environment(key_id: str, key_secret: str, webhook_secret: str) -> dict[str, str]:
    if (not re.fullmatch(r"rzp_test_[A-Za-z0-9]{8,64}", key_id)
            or not key_secret or key_secret != key_secret.strip()
            or len(webhook_secret) < 32 or webhook_secret != webhook_secret.strip()
            or webhook_secret == key_secret):
        raise ValueError("Enter a Test key and a separate webhook secret of at least 32 characters")
    return {"RAZORPAY_KEY_ID": key_id, "RAZORPAY_KEY_SECRET": key_secret,
            "RAZORPAY_WEBHOOK_SECRET": webhook_secret, "RAZORPAY_CHECKOUT_ENABLED": "true",
            "RAZORPAY_ACCOUNT_APPROVED": "true", "PAYMENTS_GO_LIVE_REVIEW_COMPLETE": "true"}


def runtime_environment(home: Path, parent: Mapping[str, str]) -> dict[str, str]:
    return {key: parent[key] for key in ("PATH", "TMPDIR", "LANG") if key in parent} | {"HOME": str(home)}


def allowed_provider_request(request: httpx.Request, auth, expected_auth: tuple[str, str]) -> bool:
    # httpx installs Basic auth after calling Client.send. Require the explicit
    # tuple at that boundary, with neither a prebuilt header nor client defaults.
    if (not isinstance(auth, tuple) or len(auth) != 2 or not all(isinstance(value, str) for value in auth)
            or not all(secrets.compare_digest(value.encode(), expected.encode())
                       for value, expected in zip(auth, expected_auth, strict=True))
            or "Authorization" in request.headers):
        return False
    url = request.url
    if (url.scheme != "https" or url.host != "api.razorpay.com" or url.port is not None
            or url.userinfo or url.fragment):
        return False
    path = url.raw_path.split(b"?", 1)[0]
    if request.method == "POST":
        return path == b"/v1/orders" and not url.query
    if request.method != "GET":
        return False
    pairs = url.params.multi_items()
    params = dict(pairs)
    if len(params) != len(pairs):
        return False
    if path == b"/v1/orders" or re.fullmatch(br"/v1/payments/pay_[A-Za-z0-9]{1,116}/refunds", path):
        if params.pop("count", None) != str(PAGE_SIZE):
            return False
        if params.pop("skip", None) not in {str(PAGE_SIZE * page) for page in range(MAX_PAGES)}:
            return False
        if path == b"/v1/orders" and not re.fullmatch(r"hw_ord_[a-f0-9]{32}", params.pop("receipt", "")):
            return False
        return not params
    return not url.query and bool(
        re.fullmatch(br"/v1/(?:orders/order_[A-Za-z0-9]{1,114}(?:/payments)?|payments/pay_[A-Za-z0-9]{1,116})", path)
    )


def serve_api(port: int, postgres_port: int) -> None:
    """Keep this lab API confined to its SQL listener and optional payment calls."""
    from contextvars import ContextVar

    import uvicorn
    from backend.app.billing.razorpay import RazorpaySettings

    provider = RazorpaySettings.from_env()
    if provider.app_env != "test" or provider.mode != "test" or (provider.key_id and not provider.key_matches_mode):
        raise RuntimeError("The checkout lab API requires explicit Test mode")
    provider_call = ContextVar("razorpay_test_request", default=False)
    original_send = httpx.Client.send
    local_sql = ("127.0.0.1", postgres_port)
    local_api = ("127.0.0.1", port)

    def guarded_send(client, request, *args, **kwargs):
        if not provider.checkout_enabled or not allowed_provider_request(
            request, kwargs.get("auth"), (provider.key_id, provider.key_secret)
        ):
            raise httpx.ConnectError("Provider request outside the checkout lab", request=request)
        token = provider_call.set(True)
        try:
            return original_send(client, request, *args, **(kwargs | {"follow_redirects": False}))
        finally:
            provider_call.reset(token)

    def audit(event, args):
        if provider_call.get():
            return
        if (event == "socket.connect" and (not isinstance(args[1], tuple) or args[1][:2] != local_sql)
                or event == "socket.getaddrinfo" and tuple(args[:2]) not in {local_api, local_sql}):
            raise PermissionError("Outbound connection outside the checkout lab")

    httpx.Client.send = guarded_send  # type: ignore[method-assign,assignment]
    sys.addaudithook(audit)
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=port, access_log=False)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CheckoutLabError(message)


def synthetic_campaign(now: datetime) -> dict[str, object]:
    start = now - timedelta(hours=1)
    return {
        "id": "razorpay-test-lab",
        "version": "razorpay-test-lab-v1",
        "catalog_version": LAUNCH_CATALOG_VERSION,
        "enabled": True,
        "starts_at": start,
        "ends_at": start + timedelta(days=30),
        "timezone": "Asia/Kolkata",
        "reference_price_verified": False,
        "created_at": now,
    }


def wait_loopback(url: str, process, *, tls=True, status: int = 200) -> None:
    deadline = time.monotonic() + 90
    with httpx.Client(verify=tls, trust_env=False, timeout=2) as client:
        while time.monotonic() < deadline:
            require(_owned_status(process) is None, "A local checkout process exited before readiness")
            try:
                if client.get(url).status_code == status:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.2)
    raise CheckoutLabError("A local checkout process did not become ready")


def verify_closed_checkout(frontend: str, ingress: str, tls) -> None:
    """Exercise the same auth, BFF, catalog and webhook routes used by the UI."""
    with httpx.Client(base_url=frontend, verify=tls, trust_env=False, timeout=10) as client:
        email, password = f"lab-{uuid4().hex}@example.com", secrets.token_urlsafe(24)
        origin = {"Origin": frontend}
        csrf = client.get("/api/auth/csrf").json()["csrfToken"]
        response = client.post("/api/account/register", headers=origin | {"X-Hirewiz-Account-CSRF": csrf},
                               json={"email": email, "password": password, "accepted_terms": True,
                                     "confirmed_age_18": True})
        require(response.status_code == 200, "Production BFF registration failed")
        response = client.post("/api/auth/callback/credentials", headers=origin,
                               data={"csrfToken": csrf, "email": email, "password": password,
                                     "json": "true", "callbackUrl": frontend + "/billing"})
        require(response.status_code == 200 and "__Secure-next-auth.session-token" in client.cookies,
                "Production NextAuth login failed")
        catalog = client.get("/api/backend/v1/credits/catalog")
        require(catalog.status_code == 200 and catalog.json().get("checkout_enabled") is False,
                "Default catalog must keep checkout closed")
        wallet = client.get("/api/backend/v1/credits/wallet")
        require(wallet.status_code == 200 and wallet.json().get("available_credits") == 0,
                "Default owned wallet must start empty")
        quote = client.post("/api/backend/v1/credits/purchase-quotes", headers=origin,
                            json={"sku": "starter_pass", "idempotency_key": uuid4().hex})
        require(quote.status_code == 503, "Default checkout must refuse purchase quotes")
        order = client.post("/api/backend/v1/credits/orders", headers=origin,
                            json={"quote_id": "synthetic", "billing_country": "IN", "idempotency_key": uuid4().hex})
        require(order.status_code == 503, "Default checkout must refuse order creation")
    with httpx.Client(base_url=ingress, trust_env=False, timeout=5) as client:
        require(client.get("/api/health").status_code == 405, "Receiver exposed API health")
        require(client.post("/unrelated", json={}).status_code == 404, "Receiver exposed an unrelated route")
        require(client.post(WEBHOOK_PATH, json={}).status_code == 503, "Default webhook must be disabled")


def run(credentials: dict[str, str], duration: int, *, wallet_directory: Path | None = None,
        resume: bool = False) -> None:
    for location in (ROOT, ROOT / "backend", ROOT / "frontend"):
        require(not any(location.glob(".env*")), "Use a clean checkout without dotenv files")
    postgres = _disposable_postgres()
    backend_port, frontend_port, ingress_port = _port(), _port(), _port()
    require(len({backend_port, frontend_port, ingress_port}) == 3, "Loopback port allocation collided")
    backend, frontend, ingress = (f"http://127.0.0.1:{backend_port}", f"https://127.0.0.1:{frontend_port}",
                                  f"http://127.0.0.1:{ingress_port}")

    with tempfile.TemporaryDirectory(prefix="hirewiz-razorpay-test-") as temporary, ExitStack() as cleanup:
        home = Path(temporary)
        wallet_path = None
        if wallet_directory is not None:
            require(postgres.port == 55434 and bool(credentials), "Use the dedicated Razorpay Test PostgreSQL on port 55434")
            cleanup.enter_context(lock_wallet_directory(wallet_directory))
            wallet_path = wallet_directory / "wallet.json"
        require(not resume or (wallet_path is not None and set(credentials) == {"RAZORPAY_WEBHOOK_SECRET"}),
                "Test wallet resume requires only the Test signing secret")
        admin = create_engine(postgres, hide_parameters=True, connect_args={
            "hostaddr": "127.0.0.1", "options": "", "sslmode": "disable", "connect_timeout": 5,
        })
        cleanup.callback(admin.dispose)
        schema = "razorpay_test_" + uuid4().hex
        if resume:
            assert wallet_path is not None
            with admin.begin() as db:
                db.execute(text("SET TRANSACTION READ ONLY"))
                schema = load_wallet_state(wallet_path, db)
        elif wallet_path is not None:
            require(not os.path.lexists(wallet_path), "This directory already has a retained Test wallet")
        options = f"-csearch_path={schema} -clock_timeout=4000 -cstatement_timeout=10000"
        database = postgres.update_query_dict({"options": options})

        key, cert = home / "loopback-key.pem", home / "loopback-cert.pem"
        tls = _create_owned_tls(key, cert) if not resume else None
        process_env = runtime_environment(home, os.environ)
        python_env = process_env | {"PYTHONPATH": str(ROOT)}
        api_env = python_env | credit_policy() | {
            "APP_ENV": "test", "DATABASE_URL": database.render_as_string(hide_password=False),
            "JWT_SECRET": secrets.token_urlsafe(32), "FRONTEND_ORIGINS": frontend, "RAZORPAY_MODE": "test",
            "EMPLOYER_DISCOVERY_ENABLED": "true", "EMPLOYER_AUTO_SUBMIT_ENABLED": "false",
            "LIFECYCLE_EMAILS_ENABLED": "false", "OPTIONAL_AI_GENERATION_ENABLED": "false",
        }
        next_env = process_env | {
            "NEXTAUTH_SECRET": secrets.token_urlsafe(32), "NEXTAUTH_URL": frontend, "BACKEND_URL": backend,
            "NEXT_TELEMETRY_DISABLED": "1", "PORT": str(frontend_port), "HIREWIZ_TEST_TLS_KEY": str(key),
            "HIREWIZ_TEST_TLS_CERT": str(cert),
        }
        retain = False

        def drop_schema() -> None:
            if not retain:
                with admin.begin() as db:
                    db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))

        if not resume:
            with admin.begin() as db:
                db.execute(text(f'CREATE SCHEMA "{schema}"'))
            cleanup.callback(drop_schema)
            print("Migrating the isolated checkout database and building the frontend.", flush=True)
            migration = _run_bounded([sys.executable, "-m", "alembic", "upgrade", "head"],
                                     cwd=ROOT / "backend", env=api_env, timeout=90)
            require(migration.returncode == 0, "Local database migration failed")
            if credentials:
                with admin.begin() as db:
                    db.execute(text(f'INSERT INTO "{schema}".credit_campaigns '
                        '(id, version, catalog_version, enabled, starts_at, ends_at, timezone, '
                        'reference_price_verified, created_at) VALUES '
                        '(:id, :version, :catalog_version, :enabled, :starts_at, :ends_at, :timezone, '
                        ':reference_price_verified, :created_at)'), synthetic_campaign(datetime.now(UTC)))
            build = _run_bounded(["npm", "run", "build"], cwd=ROOT / "frontend", env=next_env, timeout=240)
            require(build.returncode == 0, "Local production frontend build failed")

        def start(command, env, cwd):
            process = _spawn_owned(command, cwd=cwd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            cleanup.callback(_stop, process)
            return process

        api = start([sys.executable, "-c",
                     "import sys; from backend.scripts.run_razorpay_test_checkout import serve_api;"
                     "serve_api(int(sys.argv[1]), int(sys.argv[2]))", str(backend_port), str(postgres.port)],
                    api_env | credentials, ROOT)
        wait_loopback(backend + "/api/health", api)
        processes = [api]
        if not resume:
            assert tls is not None
            next_app = start(["node", "tests/candidate-https-server.mjs"], next_env, ROOT / "frontend")
            wait_loopback(frontend + "/register", next_app, tls=tls)
            processes.append(next_app)
        receiver_command = [sys.executable, "-m", "backend.scripts.razorpay_test_webhook_ingress",
                            "--listen-port", str(ingress_port), "--upstream-port", str(backend_port)]
        receiver_env = python_env
        control_dir = home / "test-webhook-control"
        if credentials:
            control_dir.mkdir(mode=0o700)
            receiver_command.extend(["--test-control-dir", str(control_dir)])
            receiver_env = python_env | {"HIREWIZ_TEST_WEBHOOK_SECRET": credentials["RAZORPAY_WEBHOOK_SECRET"]}
        receiver = start(receiver_command, receiver_env, ROOT)
        wait_loopback(ingress + "/api/health", receiver, status=405)
        processes.append(receiver)

        if not credentials:
            verify_closed_checkout(frontend, ingress, tls)
            with admin.connect() as db:
                counts = db.execute(text(f'SELECT (SELECT count(*) FROM "{schema}".payment_orders), '
                                         f'(SELECT count(*) FROM "{schema}".credit_grants)')).one()
                require(tuple(counts) == (0, 0), "Default run unexpectedly created payment state")
            print("Local auth, catalog, wallet, purchase denial and callback isolation passed.")
            return

        if resume:
            with httpx.Client(base_url=backend, trust_env=False, timeout=5) as client:
                catalog = client.get("/api/public/credits/catalog")
                require(catalog.status_code == 200 and catalog.json().get("checkout_enabled") is False,
                        "A resumed Test wallet must keep checkout closed")
            print("Test refund receiver is available. Checkout and provider API access are closed.", flush=True)
        else:
            assert tls is not None
            with httpx.Client(base_url=frontend, verify=tls, trust_env=False, timeout=10) as client:
                catalog = client.get("/api/backend/public/credits/catalog")
                require(catalog.status_code == 200, "Test checkout catalog is unavailable")
                payload = catalog.json()
                require(payload.get("checkout_enabled") is True, "Test checkout catalog is closed")
                offers = {(row["sku"], row["amount_minor"], row["credits"]) for row in payload["products"]
                          if isinstance(row.get("campaign"), dict) and row["campaign"]["status"] == "active"
                          and row["campaign"]["reference_price_verified"] is False}
                require(offers == LAUNCH_PRICES, "Test catalog did not return the expected launch terms")
            if wallet_path is not None:
                with admin.begin() as db:
                    db.execute(text("SET TRANSACTION READ ONLY"))
                    identity = wallet_identity(db, schema)
                save_wallet_state(wallet_path, identity)
                retain = True
                print(f"The disposable Test wallet will be retained in its cluster. Record at {wallet_path}", flush=True)
            print(f"Checkout lab is available at {frontend}/register", flush=True)
            print(f"Use the lab's loopback certificate at {cert}", flush=True)
        print(f"Point the temporary Test webhook tunnel only to {ingress}{WEBHOOK_PATH}", flush=True)
        print(f"Optional signed Test delivery hold file: {control_dir / 'hold.json'}", flush=True)
        print(f"The lab ends after {duration} minutes or Ctrl+C.", flush=True)
        deadline = time.monotonic() + duration * 60
        while time.monotonic() < deadline:
            require(all(_owned_status(process) is None for process in processes),
                    "A checkout lab process exited")
            time.sleep(0.5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-disposable-postgres", action="store_true", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--razorpay-test", action="store_true", help="Interactively enable Razorpay Test checkout")
    mode.add_argument("--resume-test-wallet", action="store_true", help="Receive signed Test callbacks with checkout closed")
    parser.add_argument("--wallet-directory", type=Path, help="Owner-only directory for the disposable Test wallet record")
    parser.add_argument("--minutes", type=int, default=30, choices=range(5, 61), metavar="5-60")
    args = parser.parse_args()
    credentials = {}
    if (args.resume_test_wallet and args.wallet_directory is None
            or args.wallet_directory is not None and not (args.razorpay_test or args.resume_test_wallet)):
        parser.error("Supply a private wallet directory for retention or resume in Test mode")
    if args.razorpay_test or args.resume_test_wallet:
        if not sys.stdin.isatty() or not sys.stderr.isatty():
            parser.error("Razorpay Test credentials require an interactive terminal")
        try:
            if args.razorpay_test:
                credentials = provider_environment(getpass.getpass("Razorpay Test key ID: "),
                    getpass.getpass("Razorpay Test key secret: "), getpass.getpass("Separate Test webhook secret: "))
            else:
                credentials = webhook_environment(getpass.getpass("Separate Test webhook secret: "))
        except ValueError as exc:
            parser.error(str(exc))

    def stop(*_args):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        run(credentials, args.minutes, wallet_directory=args.wallet_directory, resume=args.resume_test_wallet)
    except KeyboardInterrupt:
        print("Checkout lab stopped.")
    except CheckoutLabError as exc:
        parser.exit(1, f"{exc}\n")
    except (AssertionError, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError,
            httpx.HTTPError, SQLAlchemyError):
        # SDK and SQL errors can include credentials or request fields.
        parser.exit(1, "Local checkout lab failed. Check the disposable database and locked dependencies.\n")


if __name__ == "__main__":
    main()
