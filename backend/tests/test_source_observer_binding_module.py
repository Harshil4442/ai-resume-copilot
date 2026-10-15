"""Actual module-owned binding guards; all credential/SQL transports are inert."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from test_fresh_cutover_execute import Connection, native, rebind


def load(name, source):
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_rebind_uses_the_actual_session_verifier_private_binding_issuer(monkeypatch, tmp_path):
    boundary, candidate, calls = rebind(monkeypatch)
    root = Path(__file__).resolve().parents[2]
    helper = load(
        "owned_source_rebind_session_regression",
        root / "backend/scripts/monetary_cutover_preflight.py",
    )
    correct = helper.neon_identity
    separate = load(
        "owned_wrong_source_rebind_neon_regression",
        root / "backend/scripts/neon_direct_identity.py",
    )
    assert (
        correct is not separate
        and correct.NeonDirectProxyBinding is not separate.NeonDirectProxyBinding
    )
    receipt = {
        "organization_id_from_actual_billing_href": correct.ORGANIZATION,
        "project_id": correct.NEON_PROJECT,
        "branch_id": correct.BRANCH,
        "endpoint_id": correct.ENDPOINT,
        "direct_host": correct.RELEASE_DIRECT_HOST,
        "pooler_host": correct.RELEASE_POOLER_HOST,
        "database_name": correct.DATABASE,
        "postgres_major": 17,
    }
    path = tmp_path / "synthetic-source-provenance.json"
    path.write_text(json.dumps(receipt))
    module_calls = []

    def modules(self, relative, name):
        module_calls.append(relative)
        return separate if relative.endswith("neon_direct_identity.py") else helper

    monkeypatch.setattr(native.NativeFreshFenceBoundary, "_module", modules)
    reference = candidate.observer_secret
    metadata = {
        "name": f"projects/{correct.PROJECT_NUMBER}/secrets/{reference.replace(':', '/versions/')}",
        "state": "ENABLED",
        "etag": "owned-inert-etag",
        "createTime": datetime.now(UTC).isoformat(),
    }
    monkeypatch.setattr(boundary, "describe_release_secret", lambda _: dict(metadata))
    monkeypatch.setattr(
        boundary,
        "access_release_secret",
        lambda _: (
            f"postgresql://{correct.RELEASE_ACTORS['observer'][0]}:owned-inert-secret@{correct.RELEASE_DIRECT_HOST}/neondb?sslmode=verify-full"
        ),
    )

    class Engine:
        def connect(self):
            calls.append("authenticate")
            return Connection()

        def dispose(self):
            calls.append("dispose")

    for owner in (correct, separate):
        monkeypatch.setattr(owner, "RELEASE_CONTROL_RECEIPT", path)
        monkeypatch.setattr(
            owner, "RELEASE_CONTROL_RECEIPT_SHA", hashlib.sha256(path.read_bytes()).hexdigest()
        )
        monkeypatch.setattr(owner._TransientTarget, "engine", lambda self: Engine())
        monkeypatch.setattr(
            owner,
            "verify_transport",
            lambda connection, binding, module=owner: module._registered(binding),
        )
        monkeypatch.setattr(owner, "verify_native_target", lambda connection, binding, oid: None)
        monkeypatch.setattr(owner, "text", lambda statement: statement)
    # The real ownership/type/provenance check that failed in native operation.
    # No copying registries, caller-minted bindings or alternate acceptance path.
    original_verify = helper._native_session_ids

    def sessions(connection, oid, *, neon_binding):
        helper.neon_identity._registered(neon_binding)
        calls.append("same_private_issuer")
        return [], {}

    monkeypatch.setattr(helper, "_native_session_ids", sessions)
    boundary.bind_source_observer_after_isolation(candidate)
    assert calls == ["authenticate", "same_private_issuer", "dispose"]
    assert module_calls == ["backend/scripts/monetary_cutover_preflight.py"]
    assert boundary.scope.source == candidate and original_verify is not sessions
