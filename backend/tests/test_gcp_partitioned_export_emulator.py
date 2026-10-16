"""Actual all-writer SDK ordering, complete cut, and late/fault publication probes."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4

import pytest
from backend.tests.fixtures.gcp_partitioned_publication import partitioned_for
from test_gcp_pairing_emulator import case as case
from test_gcp_password_lifetime_emulator import lifetime as lifetime
from test_gcp_scoped_authority_emulator import action, enroll, session
from test_gcp_scoped_authority_emulator import scoped as scoped

from app.domains.recovery.contracts import canonical
from app.domains.recovery.gcp_contracts import AmbiguousCommit, JournalIntent, OpeningHold
from app.domains.recovery.gcp_journal import GcsJournal
from app.domains.recovery.gcp_pairing_attempts import GcsPairingAttempts
from app.domains.recovery.gcp_pairing_contracts import PairingJournalIntent
from app.domains.recovery.gcp_partitioned_export import PartitionedCutExporter
from app.domains.recovery.gcp_password_lifetime_contracts import EnrollPasswordAccount
from app.domains.recovery.gcp_password_lifetime_journal import GcsPasswordLifetimeJournal
from app.domains.recovery.store import GuardDenied, GuardUnavailable


def ordinary(c):
    return JournalIntent(operation_id=uuid4(), pin=c.pin, effect=OpeningHold(subject_uuid=c.subject,
        employer_key="a" * 64, tenant_id="synthetic", opening_key="b" * 64, binding_sha256="c" * 64),
        created_at_ms=c.now, deadline_ms=c.now + 60_000)


def test_complete_cut_exports_full_canonical_intents_and_all_scoped_native_rows(scoped):
    c = scoped
    a, _ = enroll(c)
    a1, a2 = session(c, a), session(c, a)
    assert c.scoped.begin(a2, action(c, a2)) is not None
    assert c.scoped.deny(a1, scope="session") is not None
    closed = c.v3.close()
    assert closed is not None
    receipt = PartitionedCutExporter(c.v3).complete(closed)
    manifest = json.loads(c.v3.test_state.objects[receipt.manifest_path][0])
    entries = [entry for chunk in manifest["chunks"] for entry in json.loads(c.v3.test_state.objects[chunk["path"]][0])["entries"]]
    assert receipt.records_count == 5 and manifest["entries_count"] == len(entries)
    rows = [json.loads(c.v3.test_state.objects[e["path"]][0]) for e in entries if e["kind"] == "native_authority_row"]
    assert len(rows) == receipt.authority_rows_count
    assert {v["namespace"] for v in rows} >= {"v3_pending_session_denials", "v3_session_tombstones",
        "v3_generation_history", "v3_activations", "v3_begins", "v3_records", "v3_record_slots"}
    assert not receipt.projection_complete and not c.active.any()


def test_paused_ordinary_post_is_included_before_complete_ack_and_never_becomes_late_orphan(case):
    c = case
    pub = partitioned_for(c)
    journal = GcsJournal(pub.bucket, publication=pub)
    value = ordinary(c)
    path, raw = journal._intent_bytes(value)
    waiting, release = Event(), Event()
    paused = False
    def hook(method, url, kwargs):
        nonlocal paused
        if method == "POST" and raw in kwargs["data"] and not paused:
            paused = True
            waiting.set()
            assert release.wait(10)
    pub.test_state.on_request = hook
    with ThreadPoolExecutor(max_workers=2) as pool:
        write = pool.submit(journal.write, value)
        assert waiting.wait(5)
        try:
            assert pub.registry.read("v3_records", str(value.operation_id)) is not None
            close = pub.close()
            assert close is not None
            receipt = PartitionedCutExporter(pub).complete(close)
            assert receipt.records_count == 1
            assert pub.test_state.objects[path][0] == raw
        finally:
            release.set()
        # Original generic write may return a status-only reconciled receipt.
        # It cannot add a new object or create action authority after this cut.
        assert write.result(timeout=15) is not None
    assert pub.test_state.objects[path][0] == raw


def test_owning_denial_at_final_manifest_read_is_refused_by_closed_registration_boundary(scoped):
    c = scoped
    a, _ = enroll(c)
    a1 = session(c, a)
    close = c.v3.close()
    assert close is not None
    attempted = []
    def hook(method, url, kwargs):
        if (method == "GET" and "authority-v3-complete-cuts" in unquote(urlsplit(url).path)
                and parse_qs(urlsplit(url).query).get("alt") == ["media"] and not attempted):
            attempted.append(True)
            with pytest.raises((GuardDenied, GuardUnavailable)):
                c.scoped.deny(a1, scope="session")
    c.v3.test_state.on_request = hook
    receipt = PartitionedCutExporter(c.v3).complete(close)
    assert attempted and receipt.records_count == 2
    assert c.v3.registry.read("v3_pending_session_denials", str(a1.session_id)) is None


@pytest.mark.parametrize("persisted", [False, True])
def test_unknown_emergency_native_close_never_allows_new_publication(case, monkeypatch, persisted):
    pub = partitioned_for(case)
    original = pub.registry.rpc.commit
    def lose(tx, writes):
        if any(json.loads(w.raw)["namespace"] == "v3_root" for w in writes):
            if persisted:
                original(tx, writes)
            else:
                pub.registry.rpc.rollback(tx)
            raise AmbiguousCommit("Synthetic native root close acknowledgement loss")
        return original(tx, writes)
    monkeypatch.setattr(pub.registry.rpc, "commit", lose)
    assert pub.close() is None
    monkeypatch.setattr(pub.registry.rpc, "commit", original)
    assert pub.resource.close_path in pub.test_state.objects
    with pytest.raises(GuardUnavailable):
        pub.retain(canonical(ordinary(case).model_dump(mode="json")).encode(), pub.resource.bucket)


def test_all_three_actual_journal_ports_retain_full_record_before_any_storage_post(scoped):
    c = scoped
    journal = GcsJournal(c.v3.bucket, publication=c.v3)
    attempts = GcsPairingAttempts(c.v3.bucket, publication=c.v3)
    lifetime = GcsPasswordLifetimeJournal(c.v3.bucket, publication=c.v3)
    seen = []
    def hook(method, url, kwargs):
        if method != "POST":
            return
        raw = kwargs["data"].split(b"\r\n\r\n")[2].rsplit(b"\r\n--", 1)[0]
        body = json.loads(raw)
        if "operation_id" not in body:
            return
        assert c.v3.registry.read("v3_records", body["operation_id"])["intent"] == body
        seen.append(body["operation_id"])
    c.v3.test_state.on_request = hook
    assert journal.write(ordinary(c)) is not None
    pairing = PairingJournalIntent(operation_id=uuid4(), pin=c.pin, epoch_generation=1,
        command="create_request", input_sha256="a" * 64, allocated_ids=(uuid4(),), nonce_commitments=(),
        created_at_ms=c.now, deadline_ms=c.now + 60_000)
    assert attempts.write_intent(pairing) is not None
    command = EnrollPasswordAccount(candidate_id=103, account_binding_id=uuid4(), subject_uuid=uuid4(),
        principal_sha256="a" * 64, credential_sha256="b" * 64, subject_registration_event_id=uuid4())
    plan = c.lifetime.allocate(command)
    assert lifetime.create(plan.intent) is not None
    assert len(seen) == 3
    c.v3.test_state.on_request = None
    close = c.v3.close()
    assert close is not None
    receipt = PartitionedCutExporter(c.v3).complete(close)
    assert receipt.records_count == 3


def test_retention_failure_and_missing_native_resource_never_return_complete_receipt(case):
    pub = partitioned_for(case)
    value = ordinary(case)
    pub.retain(canonical(value.model_dump(mode="json")).encode(), pub.resource.bucket)
    close = pub.close()
    assert close is not None
    def fail(method, url, kwargs):
        if method == "POST" and b"authority-v3-native-rows/" in kwargs["data"]:
            raise TimeoutError("Synthetic protected retention unavailable")
    pub.test_state.on_request = fail
    with pytest.raises(GuardUnavailable):
        PartitionedCutExporter(pub).complete(close)


def test_complete_cut_more_than_63_has_complete_native_and_gcs_terminal_pages(case):
    pub = partitioned_for(case)
    inputs = [ordinary(case) for _ in range(80)]
    for value in inputs:
        pub.retain(canonical(value.model_dump(mode="json")).encode(), pub.resource.bucket)
    close = pub.close()
    assert close is not None
    receipt = PartitionedCutExporter(pub).complete(close, max_documents=512)
    assert receipt.records_count == 80
    lists = [url for method, url in pub.test_state.requests if method == "GET" and urlsplit(url).path.endswith("/o")]
    assert len(lists) == 4 and sum("pageToken" in url for url in lists) == 2
    assert all(any(v.operation_id.hex.replace("-", "") in path.replace("-", "")
        for path in pub.test_state.objects) for v in inputs)


def test_unregistered_canonical_gcs_intent_refuses_complete_prefix(case):
    pub = partitioned_for(case)
    value = ordinary(case)
    path, raw = GcsJournal._intent_bytes(value)
    # Privileged/old bypass publication: exact canonical bytes, no Native row.
    pub.test_state.objects[path] = (raw, "9001")
    close = pub.close()
    assert close is not None
    with pytest.raises(GuardUnavailable, match="unregistered"):
        PartitionedCutExporter(pub).complete(close)


@pytest.mark.parametrize("fault", ["missing", "changed_resource", "changed_native_uid"])
def test_missing_or_changed_pinned_authority_never_returns_admission(case, fault):
    pub = partitioned_for(case)
    if fault == "missing":
        pub.test_state.missing = True
    elif fault == "changed_resource":
        pub.test_state.raw = b'{"untrusted":"restored resource"}'
    else:
        pub.registry.rpc.admin.reported_uid = str(uuid4())
    with pytest.raises(GuardUnavailable):
        pub.retain(canonical(ordinary(case).model_dump(mode="json")).encode(), pub.resource.bucket)


def test_native_root_close_wins_after_witness_precheck_before_actual_registration_transaction(case, monkeypatch):
    from threading import get_ident
    pub = partitioned_for(case)
    value = ordinary(case)
    original = pub.registry.rpc.begin
    owner = get_ident()
    waiting, release = Event(), Event()
    held = False
    def begin():
        nonlocal held
        if get_ident() != owner and not held:
            held = True
            waiting.set()
            assert release.wait(10)
        return original()
    monkeypatch.setattr(pub.registry.rpc, "begin", begin)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(pub.retain, canonical(value.model_dump(mode="json")).encode(), pub.resource.bucket)
        assert waiting.wait(5)
        try:
            close = pub.close()
            assert close is not None
        finally:
            release.set()
        with pytest.raises(GuardUnavailable):
            pending.result(timeout=15)
    receipt = PartitionedCutExporter(pub).complete(close)
    assert receipt.records_count == 0
    assert pub.registry.read("v3_records", str(value.operation_id)) is None
