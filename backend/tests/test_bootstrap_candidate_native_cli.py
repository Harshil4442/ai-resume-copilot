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
    child = r'''
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
'''
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
