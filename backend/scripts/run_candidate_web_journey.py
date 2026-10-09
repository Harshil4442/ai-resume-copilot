"""Own local PG schema/processes; actual production NextAuth + native/V3 fixture."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
from alembic.config import Config
from run_cold_browser_journey import _owned_status, _port, _run_bounded, _spawn_owned, _stop
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from alembic import command

ROOT = Path(__file__).resolve().parents[2]


def wait(url, process):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if _owned_status(process) is not None:
            raise AssertionError("Owned candidate server exited before readiness")
        try:
            if httpx.get(url, verify=False, timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise AssertionError("Owned candidate server readiness deadline exceeded")


def main():
    url = make_url(Path("/tmp/hirewiz-pairing-local-test-db-url-20261009.txt").read_text().strip()).set(database="hirewiz_admission_test")
    if url.host != "127.0.0.1" or url.port != 55433 or url.database != "hirewiz_admission_test":
        raise AssertionError("Only the owned loopback PostgreSQL database is permitted")
    extension = os.environ.get("HIREWIZ_TEST_EXTENSION_ID", "synthetic_owned_extension")
    revision = os.environ.get("HIREWIZ_TEST_EXTENSION_REVISION", "synthetic_owned_revision")
    hook = os.environ.get("HIREWIZ_CANDIDATE_MV3_JOURNEY_MODULE")
    identity = os.environ.get("HIREWIZ_CANDIDATE_MV3_MANIFEST_IDENTITY")
    if hook and (not identity or re.fullmatch(r"[a-p]{32}", extension) is None):
        raise AssertionError("An optional MV3 join requires an exact public manifest identity and extension ID")
    identifier = uuid4().hex
    schema = "candidate_web_" + identifier
    directory = Path("/tmp") / ("hirewiz-candidate-web-browser-" + identifier)
    directory.mkdir(mode=0o700)
    backend_port, frontend_port = _port(), _port()
    origin, backend = f"https://127.0.0.1:{frontend_port}", f"https://127.0.0.1:{backend_port}"
    admin = create_engine(url, hide_parameters=True)
    with admin.begin() as db:
        db.execute(text(f'CREATE SCHEMA "{schema}"'))
    options = f"-csearch_path={schema} -clock_timeout=4000 -cstatement_timeout=10000"
    scoped_url = url.update_query_dict({"options": options})
    os.environ["APP_ENV"] = "test"
    os.environ["DATABASE_URL"] = scoped_url.render_as_string(hide_password=False)
    engine = create_engine(scoped_url, hide_parameters=True)
    cfg = Config(str(ROOT / "backend/alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "backend/alembic"))
    try:
        with engine.begin() as db:
            cfg.attributes["connection"] = db
            command.upgrade(cfg, "head")
    except Exception:
        engine.dispose()
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
        (directory / "summary.json").write_text(json.dumps({"status": "FAIL", "checkpoint": "migration",
            "schema": schema, "schema_cleaned": True}))
        print(json.dumps({"status": "FAIL", "evidence_directory": str(directory), "schema_cleaned": True}))
        raise
    key, cert = directory / "key.pem", directory / "cert.pem"
    try:
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-keyout", str(key), "-out", str(cert), "-subj", "/CN=127.0.0.1",
        "-addext", "subjectAltName=IP:127.0.0.1", "-addext", "basicConstraints=critical,CA:TRUE"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except Exception:
        engine.dispose()
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
        key.unlink(missing_ok=True)
        (directory / "summary.json").write_text(json.dumps({"status": "FAIL", "checkpoint": "owned TLS setup", "schema_cleaned": True}))
        raise
    env = {**os.environ, "APP_ENV": "test", "DATABASE_URL": scoped_url.render_as_string(hide_password=False),
        "HIREWIZ_CANDIDATE_SOCKET_FIXTURE": "1", "HIREWIZ_CANDIDATE_FIXTURE_DIRECTORY": str(directory),
        "HIREWIZ_AUTHORITY_EMULATOR_PORT": "58882", "BROWSER_PAIRING_WEBSITE_ORIGIN": origin,
        "CANDIDATE_AUTH_TRANSPORT_SECRET": (b"n" * 32).hex(), "CANDIDATE_AUTH_TRANSPORT_KEY_ID": "fixture_ingress_v1",
        "BROWSER_PAIRING_GATEWAY_SECRET": "a" * 64, "HIREWIZ_TEST_EXTENSION_ID": extension,
        "HIREWIZ_TEST_EXTENSION_REVISION": revision, "JWT_SECRET": "synthetic-native-web-jwt-secret-20261009-only",
        "NEXTAUTH_SECRET": "synthetic-nextauth-candidate-web-secret-20261009-only",
        "NEXTAUTH_URL": origin, "BACKEND_URL": backend, "NODE_EXTRA_CA_CERTS": str(cert),
        "HIREWIZ_TEST_TLS_CERT": str(cert), "HIREWIZ_TEST_TLS_KEY": str(key), "PORT": str(frontend_port),
        "HIREWIZ_CANDIDATE_TEST_ID": identifier, "PYTHONPATH": f"{ROOT / 'backend'}:{ROOT}",
        "CANDIDATE_ACCOUNT_LIFECYCLE_ENABLED": "false", "AUTH_RATE_LIMIT": "1000/minute",
        "SENTRY_DSN": "", "NEXT_PUBLIC_SENTRY_DSN": "", "NEXT_PUBLIC_POSTHOG_KEY": "", "NEXT_TELEMETRY_DISABLED": "1"}
    processes, cleanup = [], []
    status = "FAIL"
    try:
        with (directory / "backend.log").open("wb") as backend_log, (directory / "frontend.log").open("wb") as frontend_log:
            api = _spawn_owned([sys.executable, "-m", "uvicorn", "backend.tests.fixtures.candidate_browser_app:app",
                "--host", "127.0.0.1", "--port", str(backend_port), "--no-access-log",
                "--ssl-keyfile", str(key), "--ssl-certfile", str(cert)], cwd=ROOT, env=env, stdout=backend_log, stderr=backend_log)
            processes.append(api)
            wait(backend + "/api/health", api)
            site = _spawn_owned(["node", "tests/candidate-https-server.mjs"], cwd=ROOT / "frontend",
                env=env, stdout=frontend_log, stderr=frontend_log)
            processes.append(site)
            wait(origin + "/api/candidate-account/availability", site)
            result = _run_bounded(["node", os.environ.get("HIREWIZ_CANDIDATE_BROWSER_PROBE", "tests/candidate-web-journey.mjs")], cwd=ROOT / "frontend", env=env, timeout=180)
            if result.returncode:
                raise AssertionError("Actual candidate browser checkpoint failed; inspect bounded browser report")
            if hook:
                # Explicit later joint gate; this source never supplies fake
                # admit/consume authority to make an extension proof pass.
                joined = _run_bounded(["node", "--input-type=module", "-e",
                    "const module = await import(process.env.HIREWIZ_CANDIDATE_MV3_JOURNEY_MODULE); "
                    "await module.nativeConnectionJourney({origin: process.env.BROWSER_PAIRING_WEBSITE_ORIGIN, "
                    "fixtureDirectory: process.env.HIREWIZ_CANDIDATE_FIXTURE_DIRECTORY, "
                    "manifestIdentity: JSON.parse(process.env.HIREWIZ_CANDIDATE_MV3_MANIFEST_IDENTITY), "
                    "executorRevision: process.env.HIREWIZ_TEST_EXTENSION_REVISION});"],
                    cwd=ROOT / "frontend", env=env, timeout=180)
                if joined.returncode:
                    raise AssertionError("Actual MV3 joint checkpoint failed; no authority is substituted")
            status = "PASS"
    finally:
        for process in reversed(processes):
            cleanup.append(_stop(process))
        with engine.connect() as db:
            states = [list(row) for row in db.execute(text("SELECT state, count(*) FROM candidate_password_accounts GROUP BY state"))]
            legacy = db.execute(text("SELECT count(*) FROM users u LEFT JOIN candidate_password_accounts a ON a.user_id=u.id WHERE a.user_id IS NULL")).scalar_one()
        engine.dispose()
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
        key.unlink(missing_ok=True)
        (directory / "summary.json").write_text(json.dumps({"status": status, "schema": schema,
            "schema_cleaned": True, "actual_candidate_account_states": states, "legacy_accounts": legacy,
            "process_cleanup": cleanup, "scope": "actual web credentials/NextAuth/native V3; pairing admission remains gated"}, indent=2))
        print(json.dumps({"status": status, "evidence_directory": str(directory), "schema_cleaned": True}))


if __name__ == "__main__":
    main()
