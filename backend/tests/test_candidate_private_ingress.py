"""Strict keys/wire semantics, actual native shared replay, no action grants."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from backend.tests.fixtures.candidate_ingress import NOW, connect, create, phase_classes
from google.cloud.firestore_v1.types import Document, Write

from app.domains.candidate_ingress.contracts import (
    MAX_FRESHNESS_MS,
    IngressAssertion,
    IngressControl,
    IngressResource,
)
from app.domains.candidate_ingress.crypto import IngressKey
from app.domains.candidate_ingress.replay import (
    CONTROL_NAMESPACE,
    REPLAY_NAMESPACE,
    production_candidate_ingress,
)
from app.domains.recovery.contracts import canonical
from app.domains.recovery.gcp_buffer import BufferedTransaction
from app.domains.recovery.gcp_contracts import AmbiguousCommit
from app.domains.recovery.store import GuardDenied, GuardUnavailable

BODY = b'{"email":"candidate@example.invalid","password":"synthetic-private-value-only"}'
BEARER = b"Bearer synthetic-server-owned-bearer"
PATH = b"/api/auth/candidate/v1/login"


@pytest.fixture
def case():
    c = create()
    yield c
    c.channel.close()


def assertion(c, *, request_id=None, raw=BODY, authorization=BEARER):
    request_id = request_id or uuid4()
    return IngressAssertion(
        key_id=c.resource.key_id,
        request_id=request_id,
        method="POST",
        path=PATH.decode(),
        origin=c.resource.origin,
        issued_at_ms=NOW,
        expires_at_ms=NOW + MAX_FRESHNESS_MS,
        body_commitment=c.key.commitment(request_id, raw),
        authorization_commitment=c.key.commitment(request_id, authorization, authorization=True),
    )


def send(c, value, **overrides):
    args = {"method": "POST", "path": PATH, "raw": BODY, "authorization": BEARER}
    args.update(overrides)
    c.ingress.authorize(c.key.header(value), **args)


def row(c, value):
    tx = c.rpc.begin()
    try:
        return BufferedTransaction(c.rpc, tx).get(REPLAY_NAMESPACE, str(value.request_id))
    finally:
        c.rpc.rollback(tx)


def test_actual_native_original_ack_only_and_full_safe_retention(case):
    c = case
    value = assertion(c)
    assert send(c, value) is None
    retained = row(c, value)
    assert retained["assertion"] == value.model_dump(mode="json") and retained[
        "resource"
    ] == c.resource.model_dump(mode="json")
    raw = canonical(retained).encode()
    assert BODY not in raw and BEARER not in raw and b"synthetic-private-value-only" not in raw
    assert hashlib.sha256(BODY).hexdigest().encode() not in raw
    assert c.key.header(value).encode() not in raw and repr(c.key) == "IngressKey(<private>)"
    with pytest.raises(GuardDenied):
        send(c, value)
    replica = connect(c.resource)
    try:
        with pytest.raises(GuardDenied):
            send(replica, value)
    finally:
        replica.channel.close()


@pytest.mark.parametrize(
    "change",
    [
        {"raw": BODY + b" "},
        {"authorization": BEARER + b"x"},
        {"method": "GET"},
        {"path": b"/api/auth/login"},
        {"path": b"/api/%61uth/candidate/v1/login"},
    ],
)
def test_changed_physical_binding_never_retains_a_row(case, change):
    value = assertion(case)
    with pytest.raises(GuardDenied):
        send(case, value, **change)
    assert row(case, value) is None


@pytest.mark.parametrize("now", [NOW - 1, NOW + 5000, NOW + 5001, True, 0])
def test_unfresh_or_untrusted_clock_never_admits(case, now):
    value = assertion(case)
    case.environment.time = now
    with pytest.raises((GuardDenied, GuardUnavailable)):
        send(case, value)
    assert row(case, value) is None


@pytest.mark.parametrize("header", ["", "ordinary-header", "e30.e30", "=" * 100, "x" * 2049, "é"])
def test_untrusted_header_never_reaches_shared_writer(case, header):
    with pytest.raises(GuardDenied):
        case.ingress.authorize(header, method="POST", path=PATH, raw=BODY, authorization=BEARER)


def test_key_separation_request_domain_and_no_unsalted_digest(case):
    c = case
    a, b = uuid4(), uuid4()
    assert c.key.commitment(a, BODY) != c.key.commitment(b, BODY)
    assert c.key.commitment(a, BODY) != c.key.commitment(a, BODY, authorization=True)
    other = IngressKey(c.key.key_id, b"o" * 32)
    assert other.commitment(a, BODY) != c.key.commitment(a, BODY)
    v = assertion(c)
    with pytest.raises(GuardDenied):
        other.verify(
            c.key.header(v),
            resource=c.resource,
            method="POST",
            path=PATH,
            raw=BODY,
            authorization=BEARER,
            now_ms=NOW,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "closed",
        "missing",
        "restore_generation",
        "uid",
        "custody",
        "missing_version",
        "boolean_version",
    ],
)
def test_actual_root_identity_and_custody_gates_fail_closed(case, mutation):
    c = case
    v = assertion(c)
    if mutation in {
        "closed",
        "missing",
        "restore_generation",
        "missing_version",
        "boolean_version",
    }:
        resource = c.resource.model_dump(mode="json")
        if mutation == "restore_generation":
            resource["restore_generation"] = 2
        control = IngressControl(
            resource=IngressResource.model_validate(resource),
            state="CLOSED" if mutation == "closed" else "OPEN",
        )
        control_value = control.model_dump(mode="json")
        if mutation == "missing_version":
            del control_value["resource"]["version"]
        if mutation == "boolean_version":
            control_value["resource"]["version"] = True
        path = BufferedTransaction(c.rpc, b"path-only").path(CONTROL_NAMESPACE, "root")
        write = (
            Write(delete=path)
            if mutation == "missing"
            else Write(
                update=Document(
                    name=path,
                    fields={
                        "body": {
                            "bytes_value": canonical(
                                {
                                    "namespace": CONTROL_NAMESPACE,
                                    "key": "root",
                                    "value": control_value,
                                }
                            ).encode()
                        }
                    },
                )
            )
        )
        c.client.commit(
            request={"database": c.resource.registry.database, "writes": [write]},
            retry=None,
            timeout=2,
        )
    elif mutation == "uid":
        c.rpc.identity = lambda: {
            "name": c.resource.registry.database,
            "uid": str(uuid4()),
            "type": "FIRESTORE_NATIVE",
        }
    else:
        c.environment.available = False
    with pytest.raises(GuardUnavailable):
        send(c, v)
    assert row(c, v) is None


@pytest.mark.parametrize("persisted", [False, True])
def test_actual_commit_unknown_no_ack_no_lifecycle_and_no_status_adoption(
    case, monkeypatch, persisted
):
    c = case
    v = assertion(c)
    original = c.rpc.commit
    calls = []

    def lose(tx, writes):
        if persisted:
            original(tx, writes)
        else:
            c.rpc.rollback(tx)
        raise AmbiguousCommit("Synthetic lost native ingress acknowledgement")

    monkeypatch.setattr(c.rpc, "commit", lose)
    with pytest.raises(GuardUnavailable):
        send(c, v)
        calls.append("lifecycle")
    assert calls == [] and (row(c, v) is not None) == persisted
    monkeypatch.setattr(c.rpc, "commit", original)
    if persisted:
        with pytest.raises(GuardDenied):
            send(c, v)
    else:
        # A new trusted server operation may run; the unknown operation did not.
        send(c, assertion(c))


def test_actual_concurrent_replicas_have_at_most_one_original_winner(case, record_property):
    c = case
    replica = connect(c.resource)
    v = assertion(c)

    def attempt(w):
        try:
            send(w, v)
            return {"decision": "accepted", "phase_classes": []}
        except GuardDenied as error:
            return {"decision": "denied", "phase_classes": phase_classes(error)}
        except GuardUnavailable as error:
            return {"decision": "unknown", "phase_classes": phase_classes(error)}

    try:
        with ThreadPoolExecutor(2) as pool:
            result = list(pool.map(attempt, [c, replica]))
        record_property("replica_outcome_classes", json.dumps(result))
        assert sum(x["decision"] == "accepted" for x in result) <= 1
        if any(x["decision"] == "accepted" for x in result):
            assert row(c, v) is not None
        # Bounded uncertain outcomes cannot manufacture a lifecycle winner.
        assert all(x["decision"] in {"accepted", "denied", "unknown"} for x in result)
    finally:
        replica.channel.close()


def test_node_server_signer_matches_python_exact_keyed_wire(case):
    c = case
    v = assertion(c)
    root = Path(__file__).resolve().parents[2]
    script = """const m=await import(process.env.HIREWIZ_TEST_SIGNER);const h=m.candidateIngressHeaders('/api/auth/candidate/v1/login',{method:'POST',headers:{authorization:process.env.HIREWIZ_TEST_BEARER},body:process.env.HIREWIZ_TEST_BODY},Number(process.env.HIREWIZ_TEST_NOW),process.env.HIREWIZ_TEST_REQUEST);process.stdout.write(h.get('x-hirewiz-candidate-ingress'));"""
    env = {
        **os.environ,
        "CANDIDATE_AUTH_TRANSPORT_SECRET": c.key.secret.hex(),
        "CANDIDATE_AUTH_TRANSPORT_KEY_ID": c.key.key_id,
        "NEXTAUTH_URL": c.resource.origin,
        "HIREWIZ_TEST_SIGNER": str(root / "frontend/lib/candidateIngressServer.ts"),
        "HIREWIZ_TEST_BODY": BODY.decode(),
        "HIREWIZ_TEST_BEARER": BEARER.decode(),
        "HIREWIZ_TEST_NOW": str(NOW),
        "HIREWIZ_TEST_REQUEST": str(v.request_id),
    }
    result = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
        env=env,
        capture_output=True,
        timeout=10,
        check=True,
    )
    assert result.stdout.decode() == c.key.header(v)
    c.ingress.authorize(
        result.stdout.decode(), method="POST", path=PATH, raw=BODY, authorization=BEARER
    )


def test_default_factory_never_uses_environment_key_to_enable(monkeypatch):
    monkeypatch.setenv("CANDIDATE_AUTH_TRANSPORT_SECRET", "n" * 64)
    with pytest.raises(GuardUnavailable):
        production_candidate_ingress()


def test_actual_concurrent_independent_process_replay_refusal(case, record_property):
    import selectors

    c = case
    v = assertion(c)
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": str(root / "backend") + os.pathsep + str(root)}
    children = []

    def line(child):
        with selectors.DefaultSelector() as ready:
            ready.register(child.stdout, selectors.EVENT_READ)
            assert ready.select(timeout=15), "Owned replay process deadline exceeded"
            return child.stdout.readline().strip()

    try:
        spec = {
            "resource": c.resource.model_dump(mode="json"),
            "header": c.key.header(v),
            "path": PATH.decode(),
            "body": BODY.decode(),
            "authorization": BEARER.decode(),
        }
        for _ in range(2):
            child = subprocess.Popen(
                [sys.executable, "-m", "backend.tests.fixtures.candidate_ingress_process"],
                cwd=root / "backend",
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            children.append(child)
            child.stdin.write(json.dumps(spec) + "\n")
            child.stdin.flush()
        assert [line(child) for child in children] == ["READY", "READY"]
        for child in children:
            child.stdin.write("GO\n")
            child.stdin.flush()
        result = [json.loads(line(child)) for child in children]
        record_property("process_outcome_classes", json.dumps(result))
        assert sum(x["decision"] == "accepted" for x in result) <= 1 and all(
            x["decision"] in {"accepted", "denied", "unknown"} for x in result
        )
        if any(x["decision"] == "accepted" for x in result):
            assert row(c, v) is not None
        for child in children:
            assert child.wait(timeout=10) == 0
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=10)
            child.stdin.close()
            child.stdout.close()


def test_independent_process_refuses_replay_after_genuine_parent_owned_ack(case, record_property):
    c, root = case, Path(__file__).resolve().parents[2]
    v = assertion(c)
    send(c, v)  # Actual original definite Commit, not a seeded replay receipt.
    spec = {
        "resource": c.resource.model_dump(mode="json"),
        "header": c.key.header(v),
        "path": PATH.decode(),
        "body": BODY.decode(),
        "authorization": BEARER.decode(),
    }
    result = subprocess.run(
        [sys.executable, "-m", "backend.tests.fixtures.candidate_ingress_process"],
        cwd=root / "backend",
        env={**os.environ, "PYTHONPATH": str(root / "backend") + os.pathsep + str(root)},
        input=json.dumps(spec) + "\nGO\n",
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    lines = result.stdout.splitlines()
    assert lines[0] == "READY" and len(lines) == 2
    decision = json.loads(lines[1])
    record_property("ordered_process_outcome_classes", json.dumps(decision))
    assert decision["decision"] == "denied" and row(c, v) is not None


@pytest.mark.parametrize(
    "mutation", ["version_bool", "missing_version", "unknown_field", "duplicate", "nonfinite"]
)
def test_valid_mac_cannot_hide_noncanonical_or_permissive_json(case, mutation):
    import hmac

    from app.domains.candidate_ingress.crypto import _MAC_DOMAIN, _b64, _key

    c = case
    v = assertion(c)
    data = v.model_dump(mode="json")
    if mutation == "version_bool":
        data["version"] = True
    elif mutation == "missing_version":
        del data["version"]
    elif mutation == "unknown_field":
        data["private_extra"] = "synthetic"
    elif mutation == "nonfinite":
        data["issued_at_ms"] = float("nan")
    raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    if mutation == "duplicate":
        raw = raw[:-1] + b',"version":1}'
    header = (
        _b64(raw)
        + "."
        + _b64(hmac.digest(_key(c.key.secret, _MAC_DOMAIN), _MAC_DOMAIN + raw, "sha256"))
    )
    with pytest.raises(GuardDenied):
        c.ingress.authorize(header, method="POST", path=PATH, raw=BODY, authorization=BEARER)
    assert row(c, v) is None
