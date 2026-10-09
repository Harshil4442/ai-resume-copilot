"""Actual separate native publication transactions and all three real SDK ports."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, get_ident
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

import pytest
from backend.tests.fixtures.gcp_closure import closed_writer, install_witness
from backend.tests.fixtures.pairing_authority import EXTENSION, RELEASE, jwk
from test_gcp_journal_sdk import response
from test_gcp_password_closure_emulator import case as case
from test_gcp_password_closure_emulator import command
from test_gcp_password_lifetime_emulator import bind, session
from test_gcp_password_lifetime_emulator import lifetime as lifetime

from app.domains.recovery.contracts import canonical
from app.domains.recovery.gcp_contracts import AmbiguousCommit, JournalIntent, OpeningHold
from app.domains.recovery.gcp_journal import GcsJournal
from app.domains.recovery.gcp_pairing_attempts import GcsPairingAttempts
from app.domains.recovery.gcp_password_closure import GcsClosedInventory
from app.domains.recovery.gcp_password_lifetime_contracts import CreatePasswordWebSession
from app.domains.recovery.gcp_password_lifetime_journal import GcsPasswordLifetimeJournal
from app.domains.recovery.store import GuardUnavailable


def ordinary(c, kind):
    if kind == "generic":
        intent = JournalIntent(
            operation_id=uuid4(),
            pin=c.pin,
            effect=OpeningHold(
                subject_uuid=UUID(c.subject),
                employer_key="a" * 64,
                tenant_id="synthetic",
                opening_key="b" * 64,
                binding_sha256="c" * 64,
            ),
            created_at_ms=c.now,
            deadline_ms=c.now + 60_000,
        )
        c.http.allow(intent)
        return intent, lambda: c.journal.write(intent)
    if kind == "pairing":
        args = {"key": jwk(c.device_key), "extension_id": EXTENSION, "revision": RELEASE}
        plan = c.coordinator.allocate("prepare_request", args)
        c.http.allow(plan.intent)
        return plan.intent, lambda: c.attempts.write_intent(plan.intent)
    plan = c.lifetime.allocate(
        CreatePasswordWebSession(
            account_binding_id=c.binding_id,
            session_id=uuid4(),
            expected_auth_generation=1,
            expires_at_ms=c.now + 600_000,
        )
    )
    return plan.intent, lambda: c.lifetime.execute(plan)


@pytest.mark.parametrize("kind", ["generic", "pairing", "lifetime"])
def test_ordinary_admitted_post_is_exported_before_cut_ack_and_cannot_add_omitted_name(
    lifetime, kind
):
    c = lifetime
    bind(c)
    _, context = session(c)
    install_witness(c)
    c.lifetime.fence = c.fence
    intent, publish = ordinary(c, kind)
    raw = canonical(intent.model_dump(mode="json")).encode()
    path = (
        c.lifetime_journal._bytes(intent)[0]
        if kind == "lifetime"
        else GcsJournal._intent_bytes(intent)[0]
    )
    original, fired, cuts = c.witness_transport.request, [], []

    def paused(method, url, **kwargs):
        if method == "POST":
            meta = json.loads(kwargs["data"].split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0])
            if meta["name"] == path and not fired:
                fired.append(True)
                close = c.close_service.close(command(c, context, "account"))
                assert close.status == "COMMITTED"
                cut = GcsClosedInventory(c.journal.bucket).seal(c.registry, close.denial_fence)
                assert cut is not None
                cuts.append(cut)
                assert c.http.objects[path][0] == raw
        return original(method, url, **kwargs)

    c.journal.bucket.client._http.request.side_effect = paused
    try:
        result = publish()
        if kind == "lifetime":
            assert result.status.status == "UNKNOWN" and result.context is None
        if kind == "pairing":
            assert result is None
    except GuardUnavailable:
        pass
    assert fired == [True] and len(cuts) == 1
    assert path in {ref.path for ref in cuts[0][0].entries}
    assert c.publication.current().phase == "SEALED"
    assert not c.active.any()


@pytest.mark.parametrize("phase", ["creation_verification", "final_ack"])
def test_owning_denial_during_last_manifest_get_cannot_publish_after_durable_seal(lifetime, phase):
    c = lifetime
    bind(c)
    _, context = session(c)
    plan = closed_writer(c, command(c, context, "account"))
    path, _ = c.lifetime_journal._bytes(plan.intent)
    original, fired = c.witness_transport.request, []

    def at_ack(method, url, **kwargs):
        parsed = urlsplit(url).path
        target = unquote(parsed.split("/o/", 1)[1]) if "/o/" in parsed else ""
        if (
            method == "GET"
            and target.startswith("authority-closed-cuts/")
            and not fired
            and (phase == "creation_verification" or len(c.witness_transport.lists) == 3)
        ):
            fired.append(True)
            with pytest.raises(GuardUnavailable, match="publication admission"):
                c.lifetime.execute(plan)
            assert c.lifetime_journal.create(plan.intent) is None
        return original(method, url, **kwargs)

    c.journal.bucket.client._http.request.side_effect = at_ack
    cut = GcsClosedInventory(c.journal.bucket).seal(c.registry, c.closed)
    assert fired == [True] and cut is not None
    assert path not in c.http.objects
    assert path not in {ref.path for ref in cut[0].entries}
    assert c.registry.read("pairing_account_tombstones", str(c.binding_id)) is None


@pytest.mark.parametrize("kind", ["generic", "pairing", "lifetime"])
@pytest.mark.parametrize("persisted", [False, True])
def test_unknown_publication_commit_never_starts_upload_and_retained_orphan_is_exported(
    lifetime, monkeypatch, kind, persisted
):
    c = lifetime
    bind(c)
    _, context = session(c)
    install_witness(c)
    intent, publish = ordinary(c, kind)
    path = GcsJournal._intent_bytes(intent)[0]
    commit = c.publication.registry.rpc.commit

    def unknown(t, writes):
        if persisted:
            commit(t, writes)
        else:
            c.publication.registry.rpc.rollback(t)
        raise AmbiguousCommit("Synthetic protected publication lost acknowledgement")

    monkeypatch.setattr(c.publication.registry.rpc, "commit", unknown)
    try:
        result = publish()
        assert (
            result is None
            if kind == "pairing"
            else kind == "lifetime" and result.status.status == "UNKNOWN"
        )
    except GuardUnavailable:
        pass
    assert path not in c.http.objects
    monkeypatch.setattr(c.publication.registry.rpc, "commit", commit)
    closed = c.close_service.close(command(c, context, "account"))
    assert closed.status == "COMMITTED"
    cut = GcsClosedInventory(c.journal.bucket).seal(c.registry, closed.denial_fence)
    assert cut is not None
    assert (path in {ref.path for ref in cut[0].entries}) is persisted
    if persisted:
        assert c.http.objects[path][0] == canonical(intent.model_dump(mode="json")).encode()


@pytest.mark.parametrize(
    "fault", ["missing_witness", "changed_generation", "restored_uid", "missing_gate"]
)
@pytest.mark.parametrize("kind", ["generic", "pairing", "lifetime"])
def test_missing_or_restored_publication_authority_denies_all_writer_uploads(lifetime, fault, kind):
    c = lifetime
    bind(c)
    _, context = session(c)
    intent, publish = ordinary(c, kind)
    state = c.publication.test_state
    if fault == "missing_witness":
        state.missing = True
    if fault == "changed_generation":
        state.generation = "302"
    if fault == "restored_uid":
        state.rpc.admin.reported_uid = str(uuid4())
    if fault == "missing_gate":
        state.registry.run(
            lambda tx: tx._stage("control", "publication", {"missing": True}, immutable=False)
        )
    try:
        result = publish()
        assert (
            result is None
            if kind == "pairing"
            else kind == "lifetime" and result.status.status == "UNKNOWN"
        )
    except GuardUnavailable:
        pass
    assert GcsJournal._intent_bytes(intent)[0] not in c.http.objects


@pytest.mark.parametrize(
    "fault", ["missing_record", "restored_gate", "export_retention", "seal_retention"]
)
def test_cut_rejects_incomplete_restored_or_unretained_export(lifetime, monkeypatch, fault):
    c = lifetime
    bind(c)
    _, context = session(c)
    plan = closed_writer(c, command(c, context, "account"))
    path, raw = c.lifetime_journal._bytes(plan.intent)
    c.http.allowed[path] = raw
    # A genuine committed publication with lost ACK leaves no ordinary GCS POST.
    commit = c.publication.registry.rpc.commit

    def lost(t, writes):
        commit(t, writes)
        raise AmbiguousCommit("Synthetic protected publication persisted")

    monkeypatch.setattr(c.publication.registry.rpc, "commit", lost)
    assert c.lifetime.execute(plan).status.status == "UNKNOWN"
    monkeypatch.setattr(c.publication.registry.rpc, "commit", commit)
    before = c.publication.current()
    if fault == "missing_record":
        c.publication.registry.run(
            lambda tx: tx._stage(
                "publication_intents",
                str(plan.intent.operation_id),
                {"missing": True},
                immutable=False,
            )
        )
    original, fired = c.witness_transport.request, []

    def failing(method, url, **kwargs):
        target = ""
        if method == "POST":
            target = json.loads(kwargs["data"].split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0])[
                "name"
            ]
        if fault == "export_retention" and method == "POST" and target == path:
            return response(b'{"error":{"code":403}}', status=403)
        if (
            fault == "seal_retention"
            and method == "POST"
            and target.startswith("authority-publication-seals/")
        ):
            return response(b'{"error":{"code":403}}', status=403)
        if (
            fault == "restored_gate"
            and method == "GET"
            and "authority-closed-cuts" in unquote(url)
            and not fired
        ):
            fired.append(True)
            c.publication.registry.run(
                lambda tx: tx._stage(
                    "control", "publication", before.model_dump(mode="json"), immutable=False
                )
            )
        return original(method, url, **kwargs)

    c.journal.bucket.client._http.request.side_effect = failing
    try:
        cut = GcsClosedInventory(c.journal.bucket).seal(c.registry, c.closed)
    except GuardUnavailable:
        cut = None
    assert cut is None


@pytest.mark.parametrize("kind", ["generic", "pairing", "lifetime"])
def test_no_publication_configuration_has_no_authoritative_upload_fallback(lifetime, kind):
    c = lifetime
    bind(c)
    session(c)
    intent, _ = ordinary(c, kind)
    def call():
        if kind == "generic":
            return GcsJournal(c.journal.bucket).write(intent)
        if kind == "pairing":
            return GcsPairingAttempts(c.journal.bucket).write_intent(intent)
        return GcsPasswordLifetimeJournal(c.journal.bucket).create(intent)
    before = len(c.http.calls)
    try:
        assert call() is None
    except GuardUnavailable:
        pass
    assert before == len(c.http.calls)


def test_unregistered_canonical_object_prevents_whole_prefix_completeness(lifetime):
    c = lifetime
    bind(c)
    _, context = session(c)
    closed_writer(c, command(c, context, "account"))
    foreign = JournalIntent(
        operation_id=uuid4(),
        pin=c.pin,
        effect=OpeningHold(
            subject_uuid=uuid4(),
            employer_key="a" * 64,
            tenant_id="synthetic",
            opening_key="b" * 64,
            binding_sha256="c" * 64,
        ),
        created_at_ms=c.now,
        deadline_ms=c.now + 60_000,
    )
    path, raw = GcsJournal._intent_bytes(foreign)
    c.http.objects[path] = (raw, "991")
    with pytest.raises(GuardUnavailable, match="unregistered"):
        GcsClosedInventory(c.journal.bucket).seal(c.registry, c.closed)


def test_actual_native_admission_and_seal_share_atomic_conflict_boundary(lifetime, monkeypatch):
    c = lifetime
    bind(c)
    _, context = session(c)
    plan = closed_writer(c, command(c, context, "account"))
    raw = canonical(plan.intent.model_dump(mode="json")).encode()
    both_staged = Barrier(2, timeout=4)
    first_attempts = set()
    native_commit = c.publication.registry.rpc.commit

    def concurrent_commit(t, writes):
        # Both real transactions stage against the same DENY_ONLY gate before
        # concurrently sending their actual native Commit requests. A genuine
        # aborted transaction must retry against the winner's current gate.
        thread = get_ident()
        if thread not in first_attempts:
            first_attempts.add(thread)
            both_staged.wait()
        return native_commit(t, writes)

    def publish():
        try:
            c.publication.retain(raw, c.journal.bucket.name)
            return "ACK"
        except GuardUnavailable:
            return "DENIED"

    monkeypatch.setattr(c.publication.registry.rpc, "commit", concurrent_commit)
    with ThreadPoolExecutor(max_workers=2) as executor:
        publisher = executor.submit(publish)
        sealer = executor.submit(c.publication.seal, c.closed.control.close)
        result, cut = publisher.result(timeout=10), sealer.result(timeout=10)
    assert cut is not None and len(first_attempts) == 2
    rows = c.publication.complete(cut)
    retained = c.publication.registry.read("publication_intents", str(plan.intent.operation_id))
    included = plan.intent.operation_id in {row.reference.operation_id for row in rows}
    assert included is (retained is not None)
    if result == "ACK":
        assert included
    assert c.publication.current().phase == "SEALED"
    assert not c.active.any()
