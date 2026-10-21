"""Standalone plan validation must not load candidate runtime credentials."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_clean_standalone_plan_needs_no_runtime_or_native_credentials(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/bootstrap_candidate_native_authority.py"
    plan = {
        "project_number": "157225724590",
        "location": "us-central1",
        "execution_database_id": "synthetic-plan-execution",
        "protected_database_id": "synthetic-plan-protected",
        "replay_database_id": "synthetic-plan-replay",
        "journal_bucket": "synthetic-plan-journal",
        "bucket_location": "US-CENTRAL1",
        "minimum_locked_retention_seconds": 86400,
        "kms_key_ring_id": "synthetic-plan-ring",
        "assertion_key_id": "candidate-assertion",
        "claim_key_id": "browser-claim",
        "website_origin": "https://www.hirewizhq.com",
        "ingress_key_id": "synthetic_plan_v1",
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    plan_path.chmod(0o600)
    child = r"""
import http.client
import importlib.abc
import runpy
import socket
import sqlite3
import subprocess
import sys
import urllib.request
import os

import google.auth
import sqlalchemy
from sqlalchemy.engine import Engine

assert not any(os.environ.get(name) for name in (
    "DATABASE_URL", "JWT_SECRET", "APP_ENV", "PYTEST_CURRENT_TEST"))
attempts = []
def forbidden(*args, **kwargs):
    attempts.append("forbidden_io")
    raise AssertionError("Standalone plan attempted external IO")
socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
socket.create_connection = forbidden
http.client.HTTPConnection.connect = forbidden
urllib.request.urlopen = forbidden
sqlite3.connect = forbidden
Engine.connect = forbidden
sqlalchemy.create_engine = forbidden
subprocess.Popen = forbidden
google.auth.default = forbidden

class DenyCredentialManager(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "googlecloudsdk" or fullname.startswith("googlecloudsdk."):
            forbidden()
sys.meta_path.insert(0, DenyCredentialManager())
script, plan = sys.argv[1:]
sys.argv = [script, "--plan-file", plan]
try:
    runpy.run_path(script, run_name="__main__")
except SystemExit as result:
    assert result.code == 0
assert attempts == []
assert "app.database" not in sys.modules and "app.security" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", child, str(script), str(plan_path)],
        cwd=tmp_path,
        env={"PATH": os.defpath, "LANG": "C", "LC_ALL": "C"},
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["state"] == "PLAN_ONLY"
    assert output["runtime_activation_allowed"] is False
    assert output["plan"]["website_origin"] == plan["website_origin"]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["plan.json"]


def test_isolated_manager_load_cannot_replace_application_protobuf_modules(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/bootstrap_candidate_native_authority.py"
    vendor = tmp_path / "manager-third-party"
    (vendor / "proto").mkdir(parents=True)
    (vendor / "proto/__init__.py").write_text(
        'raise AssertionError("Manager protobuf must not replace application dependencies")\n'
    )
    child = r"""
import importlib
import importlib.util
import json
import socket
import sys
from types import ModuleType

import google.auth
from google.auth.credentials import AnonymousCredentials

def forbidden(*args, **kwargs):
    raise AssertionError("Import ordering must not resolve ADC or call a provider")
google.auth.default = forbidden
socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
socket.create_connection = forbidden
script, vendor = sys.argv[1:]
spec = importlib.util.spec_from_file_location("isolated_bootstrap", script)
bootstrap = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bootstrap
spec.loader.exec_module(bootstrap)

# Model the stock manager's later sys.path insertion without loading credentials
# or requiring the desktop's gcloud installation in CI.
calls = []
credential = AnonymousCredentials()
def validate(plan):
    calls.append("validate")
    sys.path.insert(0, vendor)
def load(**kwargs):
    assert kwargs == {"account": bootstrap.ACCOUNT, "allow_account_impersonation": False}
    calls.append("load")
    return credential
bootstrap.validate_manager = validate
for name in ("googlecloudsdk", "googlecloudsdk.core", "googlecloudsdk.core.credentials",
             "googlecloudsdk.core.credentials.store"):
    sys.modules[name] = ModuleType(name)
sys.modules["googlecloudsdk.core.credentials.store"].Load = load
assert bootstrap.manager_credentials(object()) is credential
for module in (
    "google.cloud.firestore_admin_v1.services.firestore_admin",
    "google.cloud.firestore_v1.services.firestore",
    "google.cloud.kms_v1.services.key_management_service",
    "google.cloud.storage",
):
    importlib.import_module(module)
import proto
assert not proto.__file__.startswith(vendor)
assert calls == ["validate", "load"]
print(json.dumps({"state": "APPLICATION_PROTOBUF_RETAINED", "provider_operations": 0}))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", child, str(script), str(vendor)],
        cwd=tmp_path,
        env={"PATH": os.defpath, "LANG": "C", "LC_ALL": "C"},
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "state": "APPLICATION_PROTOBUF_RETAINED",
        "provider_operations": 0,
    }
