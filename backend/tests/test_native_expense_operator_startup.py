"""Operator initialization must not leak synthetic credentials or config text."""
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from backend.scripts import reconcile_native_expenses as operator
from sqlalchemy.engine import make_url

SECRET = "synthetic-STARTUP-SECRET-DO-NOT-LOG"
SCRIPT = Path(__file__).resolve().parents[1] / "scripts/reconcile_native_expenses.py"


@pytest.mark.parametrize("changes", [
    {"DATABASE_URL": "unknown-secret-driver://" + SECRET},
    {"DATABASE_URL": "postgresql+psycopg://synthetic:synthetic@127.0.0.1:1/unused", "DB_POOL_SIZE": SECRET},
])
def test_initialization_errors_stay_inside_fixed_private_boundary(changes):
    environment = dict(os.environ, APP_ENV="test") | changes
    result = subprocess.run([sys.executable, str(SCRIPT), "dry-run", "--plan", "/nonexistent/plan.json"],
                            env=environment, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert SECRET not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    assert json.loads(result.stdout)["code"] == "native_expense_unavailable"


def test_help_and_module_import_are_inert_under_invalid_app_configuration():
    environment = dict(os.environ, APP_ENV="production", DATABASE_URL=SECRET, DB_POOL_SIZE=SECRET)
    help_result = subprocess.run([sys.executable, str(SCRIPT), "--help"], env=environment,
                                 capture_output=True, text=True, timeout=10)
    assert help_result.returncode == 0 and SECRET not in help_result.stdout + help_result.stderr
    loaded = subprocess.run([sys.executable, "-c", "import scripts.reconcile_native_expenses; print('inert')"],
                            cwd=SCRIPT.parents[1], env=environment, capture_output=True, text=True, timeout=10)
    assert loaded.returncode == 0 and loaded.stdout.strip() == "inert"
    assert SECRET not in loaded.stdout + loaded.stderr


def test_unverified_transport_is_refused_before_any_database_connection():
    environment = dict(os.environ, APP_ENV="production",
        DATABASE_URL="postgresql+psycopg://synthetic:synthetic@no-connection.example.invalid/db?sslmode=require")
    result = subprocess.run([sys.executable, str(SCRIPT), "dry-run", "--plan", "/nonexistent/plan.json"],
                            env=environment, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert json.loads(result.stdout)["code"] == "operator_verified_tls_required"
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("sslmode,row,permitted", [
    ("require", (True, "TLSv1.3", 256), False),
    ("verify-full", (False, None, None), False),
    ("verify-full", (True, "TLSv1.1", 256), False),
    ("verify-full", (True, "TLSv1.3", 256), True),
])
def test_remote_operator_requires_client_verification_and_observed_tls(monkeypatch, sslmode, row, permitted):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("SSLKEYLOGFILE", raising=False)
    url = make_url(f"postgresql+psycopg://synthetic@remote.example.invalid/test?sslmode={sslmode}")
    db = SimpleNamespace(get_bind=lambda: SimpleNamespace(url=url),
                         execute=lambda *_: SimpleNamespace(one_or_none=lambda: row))
    if permitted:
        operator.verify_transport(db)
    else:
        with pytest.raises(operator.OperatorRefused, match="verified_tls"):
            operator.verify_transport(db)
