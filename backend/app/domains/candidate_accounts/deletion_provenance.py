"""Read-only closed native ownership; never privacy intent or a native action.

The email recovery flow supplies fresh explicit erasure intent separately. These
reads only bind an already denied SQL projection to its genuine retained native
subject, original session/enrollment, command and recovery mailbox. They never
call prepare_delete, deny, _execute, a signer or a credential/session issuer.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from ...models import CandidatePasswordAccount, User
from ..recovery.contracts import fingerprint
from ..recovery.store import GuardDenied, GuardUnavailable
from .contracts import CandidateCredentialRevision
from .service import credential_digest


def closed_revision(account: CandidatePasswordAccount, owner: User) -> CandidateCredentialRevision:
    if (account is None or owner is None or account.user_id != owner.id or account.state != "DELETE_DENIED"
            or account.credential_sha256 != credential_digest(owner.password_hash)):
        raise GuardDenied("Exact closed native credential ownership is required")
    return CandidateCredentialRevision(candidate_id=owner.id, subject_uuid=account.subject_uuid,
        account_binding_id=account.account_binding_id, principal_sha256=account.principal_sha256,
        auth_generation=account.auth_generation, credential_sha256=account.credential_sha256)


def _read(logout, revision: CandidateCredentialRevision, email_sha256: str):
    from ..recovery.gcp_candidate_logout import GcpCandidateCookieLogout
    from ..recovery.gcp_partitioned_contracts import (
        PartitionedRecord,
        ScopedDenial,
        ScopedJournalIntent,
        ScopedTombstone,
    )
    from ..recovery.password_reauth import CandidateWebSession
    if type(logout) is not GcpCandidateCookieLogout:
        raise GuardUnavailable("Actual closed native ownership reader required")
    result = []

    def read(tx):
        logout.scoped.publication._root(tx, opened=True)
        tombstone = ScopedTombstone.model_validate(tx.get("v3_account_tombstones", str(revision.account_binding_id)))
        record = PartitionedRecord.model_validate(tx.get("v3_records", str(tombstone.operation_id)))
        intent = ScopedJournalIntent.model_validate(record.intent)
        command = intent.command
        if (not isinstance(command, ScopedDenial) or command.scope != "subject"
                or command.subject.model_dump(mode="json", exclude={"enrollment_operation"}) != revision.model_dump(mode="json")
                or intent.operation_id != tombstone.operation_id or record.reference.operation_id != intent.operation_id
                or record.reference.partition != intent.partition or record.reference.kind != "scoped_admission"
                or intent.pin != logout.scoped.publication.resource.pin.authority.target
                or tombstone.subject_uuid != revision.subject_uuid or tombstone.account_binding_id != revision.account_binding_id
                or tombstone.session_id is not None):
            raise GuardDenied("Closed native ownership does not match retained original denial")
        mailbox = {"version": 1, "subject_uuid": str(revision.subject_uuid),
            "candidate_id": revision.candidate_id, "account_binding_id": str(revision.account_binding_id),
            "principal_sha256": revision.principal_sha256, "email_sha256": email_sha256}
        if tx.get("v3_recovery_mailboxes", str(revision.subject_uuid)) != mailbox:
            raise GuardDenied("Closed native recovery mailbox is not independently bound")
        context = CandidateWebSession(candidate_id=revision.candidate_id,
            account_binding_id=revision.account_binding_id, session_id=command.session.session_id,
            authentication_method=command.session.authentication_method)
        result.append((context, {"version": "closed-native-ownership-v1", "revision": revision.model_dump(mode="json"),
            "context": context.model_dump(mode="json"), "operation_id": str(intent.operation_id),
            "intent_sha256": fingerprint(intent.model_dump(mode="json")), "mailbox_sha256": fingerprint(mailbox),
            "resource_sha256": fingerprint(logout.scoped.publication.resource.model_dump(mode="json"))}))

    logout.scoped._run(read)
    return result[0]


def read_closed_ownership(revision: CandidateCredentialRevision, email_sha256: str) -> str:
    """Use actual configured composition and its pre/post configuration/witness gates."""
    from .production import production_candidate_accounts

    source = os.getenv("APP_RELEASE", "")
    if re.fullmatch(r"[a-f0-9]{40}", source) is None:
        raise GuardUnavailable("Immutable current application source is required")
    configured = production_candidate_accounts()

    def observe(service):
        logout = service.cookie_logout
        context, first = _read(logout, revision, email_sha256)
        # This existing verifier proves full original enrollment/session/owner
        # provenance. Its boolean remains a denial fact, never erasure authority.
        if logout.confirm_deleted_cookie(context, auth_generation=revision.auth_generation) is not True:
            raise GuardUnavailable("Complete retained native denial is unavailable")
        _, second = _read(logout, revision, email_sha256)
        if first != second:
            raise GuardUnavailable("Closed native ownership changed during observation")
        return fingerprint({"ownership": first,
            "configuration_sha256": credential_digest(configured._raw.decode()),
            "application_source": source,
            "adapter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})

    return configured._invoke(observe)
