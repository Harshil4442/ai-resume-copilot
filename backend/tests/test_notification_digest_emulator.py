"""Actual loopback Firestore digest transactions and complete native census.

Uses original genuine lifetime enrollment/session ACKs and scoped publication.
Storage/admin UID pins are existing synthetic ports, not cloud/IAM/durability
proof. No SQL, model, email provider, ambient credentials, or external RPC.
"""
from __future__ import annotations

import pytest
from test_gcp_pairing_emulator import case as case  # noqa: F401
from test_gcp_password_lifetime_emulator import lifetime as lifetime  # noqa: F401
from test_gcp_scoped_authority_emulator import enroll, session
from test_gcp_scoped_authority_emulator import scoped as scoped  # noqa: F401

from app.domains.candidate_accounts.contracts import CandidateCredentialRevision
from app.domains.notifications.mail_contracts import ProviderResult
from app.domains.recovery.gcp_candidate_lifetimes import GcpProtectedCandidateLifetimes
from app.domains.recovery.gcp_notification_digest import HEADS, GcpDigestAuthority
from app.domains.recovery.gcp_partitioned_export import PartitionedCutExporter
from app.domains.recovery.store import GuardDenied, GuardUnavailable

EMAIL = "a" * 64
POLICY = "weekly-digest-v1"


@pytest.fixture
def digest(scoped):
    c = scoped
    command, _ = enroll(c)
    context = session(c, command)
    subject, _ = c.scoped.resolve(context)
    revision = CandidateCredentialRevision.model_validate(
        subject.model_dump(exclude={"enrollment_operation"}))
    # Genuine current-session protected mailbox finalization, no seeded consent,
    # subject, session, command history, or claimed send ACK.
    assert GcpProtectedCandidateLifetimes(c.scoped).finalize_password_login(context, revision, EMAIL)
    c.digest = GcpDigestAuthority(c.scoped)
    c.digest_context, c.digest_revision = context, revision
    return c


def consent(c, *, generation=0, enabled=True):
    return c.digest.change(c.digest_context, c.digest_revision,
        expected_generation=generation, enabled=enabled, email_sha256=EMAIL, policy_version=POLICY)


def begin(c, generation=1):
    return c.digest.begin(c.digest_revision, email_sha256=EMAIL,
        consent_generation=generation, policy_version=POLICY, message_sha256="b" * 64)


def test_real_emulator_consent_send_and_complete_census(digest):
    c = digest
    head = consent(c)
    assert c.digest.read_current(c.digest_revision, EMAIL) == head
    ack = begin(c)
    c.digest.claim(ack, c.digest_revision, EMAIL)
    outcome = c.digest.record_outcome(ack, ProviderResult("sent", "c" * 64, 200))
    assert outcome.outcome.outcome == "sent"
    with pytest.raises(GuardDenied):
        begin(c)
    # The original exporter reads genuine emulator rows and retained full intents;
    # it runs the extended scoped census, rather than validating hand-built rows.
    receipt = PartitionedCutExporter(c.v3).complete(c.v3.close())
    assert receipt.records_count == 5
    assert receipt.projection_complete is False and not c.active.any()


def test_real_emulator_optout_and_session_revocation_prevent_new_send(digest):
    c = digest
    consent(c)
    disabled = consent(c, generation=1, enabled=False)
    assert disabled.generation == 2 and disabled.enabled is False
    with pytest.raises(GuardDenied):
        begin(c, generation=2)
    assert c.scoped.deny(c.digest_context, scope="session") is not None
    with pytest.raises(GuardDenied):
        consent(c, generation=2)
    receipt = PartitionedCutExporter(c.v3).complete(c.v3.close())
    assert receipt.records_count == 5 and not c.active.any()


def test_real_emulator_rolled_back_consent_head_cannot_hide_history(digest):
    c = digest
    old = consent(c)
    consent(c, generation=1, enabled=False)
    # Explicit owned corruption models a restored mutable head; immutable newer
    # history/records remain in the actual emulator and must stop a new send.
    c.v3.registry.run(lambda tx: tx.put(HEADS, str(old.subject.subject_uuid), old.model_dump(mode="json")))
    with pytest.raises(GuardUnavailable):
        c.digest.read_current(c.digest_revision, EMAIL)
    with pytest.raises(GuardUnavailable):
        begin(c)
    with pytest.raises(GuardUnavailable):
        PartitionedCutExporter(c.v3).complete(c.v3.close())
    assert not c.active.any()
