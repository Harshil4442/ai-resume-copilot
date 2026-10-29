"""Owned-local Stage5 HTTP/native-auth experiment. Never production qualification.

Use --plan without dependencies. --execute creates only fresh local dependencies;
there is no external DSN, Redis URI, credential, project or destination argument.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import platform
import resource
import ssl
import sys
import threading
import time
import traceback
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from scripts.stage5_capacity import (  # noqa: E402
    PATHS,
    VERSION,
    Plan,
    finish_owned_http,
    scheduled_phase,
    summarize,
)
from scripts.stage5_resources import Resources, private_json  # noqa: E402


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--config", type=Path, default=ROOT / "backend/capacity/stage5-owned-http-v1.json")
    result.add_argument("--execute", action="store_true")
    result.add_argument("--output", type=Path)
    result.add_argument("--pg-bin", type=Path)
    result.add_argument("--redis-server", type=Path)
    return result


def application_sources_sha256():
    # Actual source bytes as well as HEAD; dirty local diagnostics must not be
    # misidentified as an immutable deployment or native protocol acceptance.
    paths = sorted((ROOT / "backend/app").rglob("*.py"))
    return hashlib.sha256(json.dumps([
        [str(path.relative_to(ROOT)), hashlib.sha256(path.read_bytes()).hexdigest()]
        for path in paths], separators=(",", ":")).encode()).hexdigest()


def local_environment(resources):
    # No .env loading, ADC, telemetry, inherited vendor keys, proxy or database URI.
    if any(path.is_file() and not path.name.endswith((".example", ".sample", ".template"))
           for part in (".", "backend") for path in (ROOT / part).glob(".env*")):
        raise RuntimeError("Credential-free source checkout required")
    os.environ.clear()
    os.environ.update(resources.env)
    os.environ.update(APP_ENV="test", DATABASE_URL=resources.dsn,
        JWT_SECRET="stage5-synthetic-only-never-a-deployment-secret",
        OPTIONAL_AI_GENERATION_ENABLED="false", UNIFIED_CREDITS_ENABLED="false",
        UNIFIED_CREDIT_EXECUTION_ENABLED="false", FREE_CORE_ENABLED="false",
        BILLING_CHECKOUT_ENABLED="false", CANDIDATE_ACCOUNT_LIST_IDENTITY_ENABLED="false",
        CANDIDATE_METADATA_ADMISSION_ENABLED="false", CANDIDATE_METADATA_CALLS_PER_MINUTE="40",
        GOOGLE_CLOUD_PROJECT="hirewiz-local-authority",
        DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA="true",
        SENTRY_DSN="", FRONTEND_ORIGINS=f"https://127.0.0.1:{resources.api_port}",
        HIREWIZ_OWNED_REDIS_SERVER=str(resources.redis_binary))
    sys.path.insert(0, str(ROOT / "backend/tests"))


def migrate(resources):
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    from alembic import command

    # Only fresh, invocation-owned PostgreSQL. The native identity is retained.
    bootstrap = create_engine(resources.dsn, hide_parameters=True, pool_size=1, max_overflow=0)
    try:
        with bootstrap.begin() as connection:
            identity = dict(connection.execute(text("""SELECT current_database() AS database,
                (SELECT system_identifier::text FROM pg_control_system()) AS system_identifier,
                (SELECT oid::bigint FROM pg_database WHERE datname=current_database()) AS database_oid,
                current_setting('server_version_num')::int AS server_version""")).mappings().one())
            if identity["database"] != "stage5_owned" or not 170000 <= identity["server_version"] < 180000:
                raise RuntimeError("Owned PostgreSQL identity differs")
            connection.exec_driver_sql("CREATE SCHEMA stage5_owned AUTHORIZATION stage5_owned")
        config = Config(str(ROOT / "backend/alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "backend/alembic"))
        with bootstrap.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        with bootstrap.connect() as connection:
            identity["schema_oid"] = connection.execute(text("SELECT oid::bigint FROM pg_namespace WHERE nspname='stage5_owned'")).scalar_one()
            identity["alembic_head"] = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        private_json(resources.directory / "database-identity.json", identity)
        return identity
    finally:
        bootstrap.dispose()


class TmpFactory:
    def __init__(self, directory):
        self.directory = directory

    def mktemp(self, name):
        target = self.directory / name
        target.mkdir(mode=0o700)
        return target


async def workload(plan, resources, tokens, storage, counters, sql, cert):
    import httpx

    from tests.fixtures.stage5_native import check_body

    results = []
    start = time.perf_counter()
    deadline = start + plan.measurement_seconds
    async with httpx.AsyncClient(base_url=f"https://127.0.0.1:{resources.api_port}",
            verify=ssl.create_default_context(cafile=str(cert)), trust_env=False,
            timeout=httpx.Timeout(10), limits=httpx.Limits(max_connections=25, max_keepalive_connections=25)) as client:
        # No counter reset: all List phases share one actual project governor.
        for mode in ("get_unpaced_diagnostic", "list_governed_40"):
            os.environ["CANDIDATE_ACCOUNT_LIST_IDENTITY_ENABLED"] = "true" if mode.startswith("list") else "false"
            os.environ["CANDIDATE_METADATA_ADMISSION_ENABLED"] = "true" if mode.startswith("list") else "false"
            for concurrency in plan.concurrency:
                before = counters.snapshot()
                redis_before = storage.storage.info("stats")["total_commands_processed"]
                pool_before = len(sql["acquire_ms"])
                active = peak = 0
                async def request(index):
                    nonlocal active, peak
                    uid = index % 8 + 1
                    path = PATHS[index % len(PATHS)]
                    active += 1
                    peak = max(peak, active)
                    try:
                        response = await client.get(path, headers={"Authorization": "Bearer " + tokens[uid]})
                    finally:
                        active -= 1
                    valid = False
                    if response.status_code == 200:
                        try:
                            valid = check_body(path, response.json(), user_id=uid,
                                buyer=uid % (5 if plan.buyers_percent == 20 else 4) == 0, heavy=uid == 8)
                        except (ValueError, TypeError, KeyError, AttributeError):
                            valid = False
                    return {"status": response.status_code, "bytes": len(response.content),
                            "path": path, "actor": uid, "contract_valid": valid,
                            "retry_after": response.headers.get("retry-after")}
                phase_start = time.perf_counter()
                rows = await scheduled_phase(count=plan.requests_per_phase, concurrency=concurrency,
                    arrivals_per_second=plan.arrivals_per_second, deadline=deadline, request=request)
                after = counters.snapshot()
                redis_after = storage.storage.info("stats")["total_commands_processed"]
                metric = {key: after.get(key, 0) - before.get(key, 0) for key in set(after) | set(before)}
                acquisitions = sql["acquire_ms"][pool_before:]
                from scripts.stage5_capacity import percentile
                phase_elapsed = time.perf_counter() - phase_start
                summary = summarize(rows)
                results.append({"mode": mode, "concurrency": concurrency,
                    "actual_peak_inflight_http": peak,
                    "elapsed_seconds": phase_elapsed, **summary,
                    "successful_owned_responses_per_second": summary["successful_owned_responses"] / phase_elapsed,
                    "wire_and_sql": metric,
                    "redis_server_command_delta_including_one_stats_observation": redis_after - redis_before,
                    "pool_acquisition_composite_p95_ms": percentile(acquisitions, .95),
                    "pool_wait_only_ms": None, "requests": rows})
                print(json.dumps({"phase": mode, "concurrency": concurrency,
                                  **summarize(rows)}), flush=True)
                if time.perf_counter() >= deadline:
                    break
            if time.perf_counter() >= deadline:
                break
    return results, time.perf_counter() - start


def execute(plan, resources):
    import pytest
    import uvicorn

    from scripts.run_cold_browser_journey import _create_owned_tls

    resources.start_postgres()
    resources.start_emulator()
    local_environment(resources)
    identity = migrate(resources)
    from sqlalchemy import event, text

    from app import database, security
    from app.domains.recovery import native_metadata_admission
    from scripts.stage5_seed import seed
    from tests.fixtures.stage5_native import (
        Counters,
        RequestGuardScope,
        configured_accounts,
        install_egress_guard,
    )
    from tests.test_native_metadata_admission_redis import owned_tls_redis

    counters = Counters()
    sql: dict[str, list[float]] = {"acquire_ms": []}
    source = database.engine
    source.hide_parameters = True
    instrumented_pool = source.pool
    actual_connect = instrumented_pool.connect
    def acquire():
        started = time.perf_counter()
        try:
            return actual_connect()
        finally:
            sql["acquire_ms"].append((time.perf_counter() - started) * 1000)
    source.pool.connect = acquire
    def statement(_conn, _cursor, value, _parameters, _context, _many):
        verb = value.lstrip().split(None, 1)[0].upper()
        counters.add("sql_" + (verb if verb in {"SELECT", "INSERT", "UPDATE", "DELETE"} else "OTHER"))
    event.listen(source, "before_cursor_execute", statement)
    redis_generator = owned_tls_redis.__wrapped__(TmpFactory(resources.directory))
    storage_factory = next(redis_generator)
    storage = storage_factory()
    redis_port = storage.storage.connection_pool.connection_kwargs["port"]
    guards = pytest.MonkeyPatch()
    server = thread = None
    key, cert = resources.directory / "http-key.pem", resources.directory / "http-cert.pem"
    try:
        _create_owned_tls(key, cert)
        # The data gRPC channel is explicitly loopback and the Docker network is
        # internal. Other Python HTTP/socket/credential paths deny before send.
        install_egress_guard(guards, {resources.pg_port, resources.emulator_port,
                                     resources.api_port, redis_port}, counters)
        with configured_accounts(engine=source, directory=resources.directory,
                endpoint=f"127.0.0.1:{resources.emulator_port}", counters=counters) as native:
            actors = {}
            from tests.test_candidate_production_factory import PASSWORD
            for uid in range(1, 9):
                actual = native.case.accounts.register(email=f"capacity-{uid}@example.test",
                    password=PASSWORD, policy_version="2026-10-08")
                if actual != uid:
                    raise RuntimeError("Fresh synthetic account sequence differs")
                session = native.case.accounts.login(email=f"capacity-{uid}@example.test", password=PASSWORD)
                actors[uid] = security.create_access_token(subject=str(uid), candidate_lifetime={
                    "version": 1, "context": session.context.model_dump(mode="json"), "auth_generation": 1})
            data = seed(source, plan)
            guards.setattr(native_metadata_admission, "_shared_storage", lambda: storage)
            from app.main import app
            if app.dependency_overrides:
                raise RuntimeError("No application auth/database dependency override is allowed")
            # Counter instrumentation observes the real validate method, not a grant.
            actual_validate = native.case.accounts.validate
            def validated(*args, **kwargs):
                counters.add("native_account_validate")
                return actual_validate(*args, **kwargs)
            guards.setattr(native.case.accounts, "validate", validated)
            scoped_app = RequestGuardScope(app, [native.case.sql_active, native.native.views], counters)
            config = uvicorn.Config(scoped_app, host="127.0.0.1", port=resources.api_port,
                ssl_keyfile=str(key), ssl_certfile=str(cert), access_log=False, log_config=None,
                lifespan="off", workers=1)
            server = uvicorn.Server(config)
            cold_start = time.perf_counter()
            thread = threading.Thread(target=server.run, name="owned-stage5-http", daemon=True)
            thread.start()
            ready_deadline = time.monotonic() + 15
            while not server.started and time.monotonic() < ready_deadline:
                if not thread.is_alive():
                    raise RuntimeError("Owned HTTP server exited")
                time.sleep(.05)
            if not server.started:
                raise RuntimeError("Owned HTTP readiness expired")
            startup_ms = (time.perf_counter() - cold_start) * 1000
            # Cross-owner and missing bearer controls traverse actual JWT/native
            # auth and actual owned SQL, before the measured phases.
            import httpx
            context = ssl.create_default_context(cafile=str(cert))
            with httpx.Client(base_url=f"https://127.0.0.1:{resources.api_port}",
                              verify=context, trust_env=False, timeout=10) as client:
                assert client.get(PATHS[0]).status_code == 401
                denied = client.get("/api/resume/2", headers={"Authorization": "Bearer " + actors[1]})
                assert denied.status_code == 404
                first_start = time.perf_counter()
                first = client.get(PATHS[0], headers={"Authorization": "Bearer " + actors[1]})
                first_ms = (time.perf_counter() - first_start) * 1000
                assert first.status_code == 200
            # Enforce read-only PostgreSQL at the server as well as observing SQL.
            with source.begin() as connection:
                connection.exec_driver_sql("ALTER ROLE stage5_owned SET default_transaction_read_only=on")
            source.dispose()  # New measured connections inherit the read-only role default.
            instrumented_pool = source.pool
            actual_connect = instrumented_pool.connect
            source.pool.connect = acquire
            cpu_before = resource.getrusage(resource.RUSAGE_SELF)
            results, elapsed = asyncio.run(workload(plan, resources, actors, storage, counters, sql, cert))
            cpu_after = resource.getrusage(resource.RUSAGE_SELF)
            with source.connect() as connection:
                assert connection.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
                observed = connection.execute(text("SELECT system_identifier::text FROM pg_control_system()")).scalar_one()
                assert observed == identity["system_identifier"]
            assert counters.snapshot().get("blocked_external_attempts", 0) == 0
            assert counters.snapshot().get("request_transaction_guard_leaks", 0) == 0
            return {"version": VERSION, "scope": "owned_local_direct_https_and_native_data_emulator",
                "plan": asdict(plan), "postgres_identity": identity, "data": data,
                "emulator": resources.emulator_identity, "pool": {"size": 4, "overflow": 2, "timeout_seconds": 10},
                "server_start_to_listen_ms": startup_ms, "first_local_owned_bootstrap_http_ms": first_ms,
                "measurement_seconds": elapsed, "results": results,
                "measurement_process_cpu_seconds": cpu_after.ru_utime + cpu_after.ru_stime - cpu_before.ru_utime - cpu_before.ru_stime,
                "process_peak_rss_bytes_including_setup": cpu_after.ru_maxrss * (1 if sys.platform == "darwin" else 1024),
                "external_attempts_blocked": 0, "provider_calls": 0,
                "source": resources.command(["git", "-C", str(ROOT), "rev-parse", "HEAD"]).strip(),
                "actual_application_python_sources_sha256": application_sources_sha256(),
                "python": platform.python_version(), "machine": platform.machine(),
                "redis_binary_sha256": hashlib.sha256(resources.redis_binary.read_bytes()).hexdigest(),
                "limitations": [
                    "Local synthetic accounts only; native Admin UID and GCS SDK transports are synthetic, not provider/IAM proof.",
                    "Authority fixture clock is fixed; no session TTL/expiry qualification.",
                    "Direct HTTPS only; BFF/browser, network geography, Cloud Run cold starts and provider latency excluded.",
                    "Get/unpaced mode is diagnostic only; List mode preserves one shared 40-unit moving window, including refusals.",
                    "500-opportunity actor is deliberately oversampled; buyer data is synthetic existing balances, not invoices/payments.",
                    "Connection acquisition includes pre-ping/connect/wait; pure pool wait is unknown.",
                    "Redis INFO counter includes observation/setup protocol commands; it is not provider billing metering.",
                    "Local wall/CPU/RSS and response bytes do not establish native CU, autoscaling, invoice cost or 10,000-user peaks.",
                    "1M authenticated requests/month remains required; 40 units/min and two Lists yields at most 864,000/30 days before other use.",
                    "No Stage5 completion, production flag activation, retail qualification or cost guarantee follows from this report."]}
    finally:
        def observers(terminal):
            if terminal:
                instrumented_pool.connect = actual_connect
                event.remove(source, "before_cursor_execute", statement)
        finish_owned_http(server=server, thread=thread, guards=guards, finalizers=[
            observers, lambda _: source.dispose(), lambda _: storage.storage.close(),
            lambda _: redis_generator.close(), lambda _: key.unlink(missing_ok=True),
            lambda _: cert.unlink(missing_ok=True)])


def main():
    args = parser().parse_args()
    try:
        plan = Plan.parse(json.loads(args.config.read_text()))
        if not args.execute:
            print(json.dumps({"version": VERSION, "plan": asdict(plan), "maximum_requests_including_controls": plan.requests_per_phase * 6 + 3,
                              "external_calls": 0, "production_qualified": False}))
            return 0
        if not args.output or not args.pg_bin or not args.redis_server:
            raise ValueError("Execute requires a new output directory and explicit PG17/TLS Redis binaries")
        resources = Resources(args.output.resolve(), args.pg_bin, args.redis_server)
        try:
            # Fixed diagnostic labels only. No auth body, token, SQL parameter or
            # exception message is emitted by the runner or its private report.
            logging.disable(logging.CRITICAL)
            result = execute(plan, resources)
        finally:
            try:
                resources.close()
            finally:
                private_json(resources.directory / "cleanup.json", resources.cleanup)
        result["cleanup_verified"] = True
        private_json(resources.directory / "report.json", result)
        print(json.dumps({"report": str(resources.directory / "report.json"), "cleanup_verified": True}))
        return 0
    except Exception as error:
        if args.execute and args.output and args.output.is_dir():
            path = args.output / "failure.json"
            if not path.exists():
                private_json(path, {"class": type(error).__name__, "frames": [
                    {"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
                    for frame in traceback.extract_tb(error.__traceback__)[-10:]]})
        print(json.dumps({"error": "Owned Stage5 experiment refused or failed", "class": type(error).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
