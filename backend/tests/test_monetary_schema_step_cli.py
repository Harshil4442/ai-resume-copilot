"""The shipping migration CLI chooses its installed driver before any database IO."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import OperationalError

from scripts import monetary_schema_step as step


@pytest.mark.parametrize("driver", ["postgres", "postgresql", "postgresql+psycopg", "postgresql+pg8000"])
def test_driver_selection_preserves_authentication_and_transport_fields(driver):
    source = URL.create(
        driver, username="candidate:@/%é", password="private:@/%?#é",
        host="db.example.invalid", port=5432, database="career_db",
        query={"sslmode": "verify-full", "channel_binding": "require",
               "sslrootcert": "/private certificates/root%20.pem", "options": "-c statement_timeout=5000",
               "application_name": ("migration/one", "migration two")},
    )
    result = step.migration_url(source.render_as_string(hide_password=False))
    expected_driver = "postgresql+psycopg" if driver in {"postgres", "postgresql"} else driver
    assert result == source.set(drivername=expected_driver)
    assert make_url(result.render_as_string(hide_password=False)) == result


CHILD = r'''
import contextlib
import io
import json
import os
import runpy
import socket
import sys

import sqlalchemy
from sqlalchemy.engine import Engine

script, source_url, expected_driver, mode = sys.argv[1:]
assert not any(os.environ.get(key) for key in ("JWT_SECRET", "APP_ENV", "PYTEST_CURRENT_TEST"))
attempts = []
constructed = []
options = []
real_create_engine = sqlalchemy.create_engine
def forbidden(*args, **kwargs):
    attempts.append("forbidden_network_or_sql")
    raise AssertionError("External IO forbidden")
def captured_create_engine(url, **kwargs):
    assert url.drivername == expected_driver
    assert kwargs["echo"] is False and kwargs["hide_parameters"] is True
    assert kwargs["connect_args"] == {"connect_timeout": 5}
    engine = real_create_engine(url, **kwargs)
    assert engine.dialect.driver == "psycopg"
    constructed.append(engine.dialect.driver)
    options.append(kwargs["poolclass"].__name__)
    return engine
def stop_before_connection(self, *args, **kwargs):
    attempts.append("blocked_engine_connect")
    raise RuntimeError("PRIVATE_ERROR_SENTINEL_NEVER_LOG")
socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
socket.create_connection = forbidden
Engine.connect = stop_before_connection
Engine.raw_connection = forbidden
sqlalchemy.create_engine = captured_create_engine
os.environ.update({"DATABASE_URL": source_url,
    "HIREWIZ_CUTOVER_CLUSTER_SHA256": "a" * 64,
    "HIREWIZ_CUTOVER_DATABASE_OID": "16395",
    "HIREWIZ_CUTOVER_NAMESPACE": "public",
    "HIREWIZ_CUTOVER_MIGRATION_ROLE": "inert_migrator",
    "HIREWIZ_CUTOVER_MIGRATION_ROLE_OID": "24578"})
sys.argv = [script, "--expected", "20261008_0009", "--target", "20261009_0010"]
stdout, stderr = io.StringIO(), io.StringIO()
try:
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        runpy.run_path(script, run_name="__main__")
except SystemExit as result:
    assert result.code == 65
else:
    raise AssertionError("Shipping script must exit65")
assert stdout.getvalue() == ""
message = stderr.getvalue()
assert "PRIVATE_ERROR_SENTINEL_NEVER_LOG" not in message and source_url not in message
assert "app.database" not in sys.modules and "app.config" not in sys.modules
assert "app.security" not in sys.modules
if mode == "valid":
    assert constructed == ["psycopg"] and options == ["NullPool"]
    assert attempts == ["blocked_engine_connect"]
    assert message == (
        "Monetary migration refused: observation_or_execution_unavailable; reconcile status before retry.\n"
        "Monetary migration diagnostic: phase=migration_execution exception_class=RuntimeError sqlstate=unavailable\n")
else:
    assert constructed == [] and attempts == []
    assert message == (
        "Monetary migration refused: observation_or_execution_unavailable; reconcile status before retry.\n"
        "Monetary migration diagnostic: phase=engine_creation exception_class=ArgumentError sqlstate=unavailable\n")
print(json.dumps({"constructed": constructed, "attempts": attempts, "exit_code": 65}))
'''


@pytest.mark.parametrize("driver", ["postgres", "postgresql", "postgresql+psycopg"])
def test_fresh_shipping_cli_constructs_psycopg3_before_blocked_connection(tmp_path, driver):
    url = URL.create(driver, username="inert", password="PRIVATE_ERROR_SENTINEL_NEVER_LOG",
                     host="db.example.invalid", database="inert",
                     query={"sslmode": "verify-full", "channel_binding": "prefer"})
    result = run_shipping_cli(tmp_path, url.render_as_string(hide_password=False), "valid")
    assert result == {"constructed": ["psycopg"], "attempts": ["blocked_engine_connect"], "exit_code": 65}


def test_fresh_shipping_cli_malformed_input_never_echoes_credentials(tmp_path):
    result = run_shipping_cli(tmp_path, "PRIVATE_ERROR_SENTINEL_NEVER_LOG malformed URL", "malformed")
    assert result == {"constructed": [], "attempts": [], "exit_code": 65}


def run_shipping_cli(tmp_path, source_url, mode):
    script = Path(__file__).resolve().parents[1] / "scripts/monetary_schema_step.py"
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", CHILD, str(script), source_url, "postgresql+psycopg", mode],
        cwd=tmp_path, env={"PATH": os.defpath, "LANG": "C", "LC_ALL": "C"},
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("sqlstate", ["42501", "42P01", "40001"])
def test_failure_diagnostic_has_only_fixed_class_phase_and_native_sqlstate(sqlstate):
    native = RuntimeError("PRIVATE_ERROR_SENTINEL_NEVER_LOG")
    native.sqlstate = sqlstate
    error = OperationalError("PRIVATE_SQL_SENTINEL_NEVER_LOG", {"password": "private"}, native)
    assert step.failure_diagnostic(error, "migration_execution") == (
        f"Monetary migration diagnostic: phase=migration_execution exception_class=OperationalError sqlstate={sqlstate}")


@pytest.mark.parametrize("sqlstate", [None, 42501, "42501\nPRIVATE", "secret", "42P01 ", True])
def test_failure_diagnostic_rejects_unbounded_or_non_native_metadata(sqlstate):
    class PrivateExceptionNameNeverLog(Exception):
        pass
    error = PrivateExceptionNameNeverLog("PRIVATE_ERROR_SENTINEL_NEVER_LOG")
    error.orig = SimpleNamespace(sqlstate=sqlstate)
    assert step.failure_diagnostic(error, "engine_creation") == (
        "Monetary migration diagnostic: phase=engine_creation exception_class=unclassified sqlstate=unavailable")


def test_failure_diagnostic_does_not_trust_phase_or_top_level_sqlstate():
    error = RuntimeError("PRIVATE_ERROR_SENTINEL_NEVER_LOG")
    error.sqlstate = "42501"
    assert step.failure_diagnostic(error, "PRIVATE_PHASE_SENTINEL_NEVER_LOG") == (
        "Monetary migration diagnostic: phase=unavailable exception_class=RuntimeError sqlstate=unavailable")
