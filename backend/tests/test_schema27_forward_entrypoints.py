"""Source-only help and once-only forward Jobs; never credentials or providers."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from infra.gcp import schema27_forward_diagnostics as diagnostics
from infra.gcp import schema27_mixed_document_proof as documents
from infra.gcp import schema27_mixed_identity_profile as identities
from test_document_source_v5_release import probe as probe
from test_schema26_app_successor import no_network as no_network
from test_schema27_api_identity_operator import controlled as controlled
from test_schema27_api_identity_promotion import handoff as handoff
from test_schema27_mixed_app_successor import profile as profile
from test_schema27_mixed_maintenance import published as published


@pytest.mark.parametrize("module", ["schema27_api_identity_promotion", "schema27_mixed_app_successor", "schema27_mixed_maintenance"])
def test_new_help_cannot_load_auth_or_database_or_connect(module):
    code = """
import builtins,runpy,socket,sys
import psycopg
original=builtins.__import__
def guarded(name,*args,**kwargs):
    if name.startswith(('google.auth','google.cloud','app.database','app.models','app.main')):
        raise AssertionError('auth/app import forbidden')
    return original(name,*args,**kwargs)
builtins.__import__=guarded
def forbidden(*args,**kwargs):
    raise AssertionError('network/database forbidden')
socket.socket.connect=forbidden
psycopg.connect=forbidden
sys.path.insert(0,sys.argv[1])
module=sys.argv[2]
sys.argv=[module,'--help']
try:
    runpy.run_module('infra.gcp.'+module,run_name='__main__')
except SystemExit as error:
    assert error.code==0
"""
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(root), module],
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode()
    assert all(option not in result.stdout for option in (b"--token", b"--grant", b"--dsn", b"--transport"))


@pytest.mark.parametrize("lost", ["create", "run"])
def test_forward_jobs_keep_existing_unknown_outcome_stop_without_replay(
    published, probe, monkeypatch, tmp_path, lost,
):
    from test_schema27_forward_diagnostics import BadStatusCodeError

    from scripts import document_refresh as builder

    public, _, _ = published
    target = documents.SourceProbe(probe.build, public)
    monkeypatch.setattr(target, "source_contract", lambda: {"synthetic-runtime": "a" * 64})
    monkeypatch.setattr(target, "build_identity", lambda **_: target.build)
    monkeypatch.setattr(target, "desired", lambda _image, nonce, _mode: {
        "name": documents.prior.PARENT + "/jobs/hirewiz-doc-six-" + nonce[:12]})
    monkeypatch.setattr(target, "validate_job", lambda *_: None)
    monkeypatch.setattr(documents.prior, "build_module", lambda: SimpleNamespace(write_new=builder.write_new))
    monkeypatch.setattr(documents.prior, "wait_operation", lambda *_: None)
    calls, jobs = [], {}
    class Client:
        def __init__(self, profile, **kwargs):
            assert profile == public and "read_budget" in kwargs
            calls.append("construct")
        def preflight(self):
            calls.append("read-only-preflight")
        def job(self, name):
            if name not in jobs:
                raise diagnostics.ForwardDenied("schema27_forward_read_failed", "run_job_read", BadStatusCodeError(404))
            return jobs[name]
        def create(self, body):
            calls.append("create")
            jobs[body["name"]] = body | {"uid": "owned-job", "etag": "owned-etag", "executionCount": 0}
            if lost == "create":
                raise RuntimeError("synthetic create acknowledgement lost")
            return {"name": documents.prior.PARENT + "/operations/owned-create"}
        def start(self, job):
            calls.append("run")
            raise RuntimeError("synthetic run acknowledgement lost")
        def close(self):
            calls.append("close")
    monkeypatch.setattr(documents, "Clients", Client)
    journal = tmp_path / "forward-native"
    with pytest.raises(RuntimeError, match="acknowledgement lost"):
        documents.run_candidate(target, journal)
    before = list(calls)
    with pytest.raises(identities.rolling.RollingDenied, match="forward_fresh_native_journal"):
        documents.run_candidate(target, journal)
    assert calls == before and calls.count("create") == 1 and calls.count("run") == (lost == "run")
    assert (journal / "inventory.create.started.json").exists()
    if lost == "run":
        assert (journal / "inventory.run.started.json").exists()
    assert not (journal / "acceptance.json").exists()


def test_forward_source_origin_refuses_before_constructing_stock_auth(profile, monkeypatch, tmp_path):
    pin, _ = profile
    monkeypatch.setattr(identities, "ROOT", tmp_path)
    monkeypatch.setattr(documents.prior.Clients, "__init__", lambda *args, **kwargs: pytest.fail("auth before source origin"))
    with pytest.raises(identities.rolling.RollingDenied, match="forward_helper_origin"):
        documents.Clients(pin)
