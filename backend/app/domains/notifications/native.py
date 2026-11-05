"""Default-off configured native digest runtime; no reusable send capability.

Retries exist only within this call's live original acknowledgement chain. A
cold process never adopts retained BEGIN/outcome rows as a callback owner.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from threading import Lock

from ..candidate_accounts.contracts import CandidateCredentialRevision
from ..candidate_accounts.production import (
    ConfiguredCandidateAccounts,
    production_candidate_accounts,
)
from ..notification_consent.unsubscribe import VerifiedUnsubscribeProof
from ..recovery.gcp_notification_digest import GcpDigestAuthority
from ..recovery.gcp_partitioned_contracts import DigestConsentHead
from ..recovery.password_reauth import CandidateWebSession
from ..recovery.store import GuardDenied, GuardUnavailable
from .mail_budget import MailBudgetError, ProviderResult, _result


def _valid_flags() -> None:
    if any(os.environ.get(name, "false") not in {"true", "false"}
           for name in ("NOTIFICATION_DIGESTS_ENABLED", "LIFECYCLE_EMAILS_ENABLED")):
        raise GuardUnavailable("Digest runtime flags are unavailable")


def _enabled() -> None:
    _valid_flags()
    if (os.environ.get("NOTIFICATION_DIGESTS_ENABLED", "false") != "true"
            or os.environ.get("LIFECYCLE_EMAILS_ENABLED", "false") != "true"):
        raise GuardUnavailable("Digest native runtime is disabled")


class ConfiguredDigestNative:
    def __init__(self, accounts: ConfiguredCandidateAccounts | None = None):
        _enabled()
        self.accounts = accounts if accounts is not None else production_candidate_accounts()
        if type(self.accounts) is not ConfiguredCandidateAccounts:
            raise GuardUnavailable("Exact configured candidate lifecycle required")
        self._native: GcpDigestAuthority | None = None
        self._lock = Lock()

    def _invoke(self, operation):
        _enabled()
        def invoke(_service):
            composition = self.accounts._composition()
            with self._lock:
                if self._native is None:
                    self._native = GcpDigestAuthority(composition.scoped)
                elif self._native.scoped is not composition.scoped:
                    raise GuardDenied("Digest configured native authority changed")
                native = self._native
            _enabled()
            result = operation(native)
            _enabled()
            return result
        result = self.accounts._invoke(invoke)
        _enabled()
        return result

    def read_current(self, revision: CandidateCredentialRevision, email_sha256: str) -> DigestConsentHead | None:
        return self._invoke(lambda native: native.read_current(revision, email_sha256))

    def change(self, context: CandidateWebSession, revision: CandidateCredentialRevision, *,
               expected_generation: int, enabled: bool, email_sha256: str,
               policy_version: str) -> DigestConsentHead:
        return self._invoke(lambda native: native.change(context, revision,
            expected_generation=expected_generation, enabled=enabled,
            email_sha256=email_sha256, policy_version=policy_version))

    @classmethod
    def for_unsubscribe(cls, accounts: ConfiguredCandidateAccounts | None = None) -> ConfiguredDigestUnsubscribe:
        """A distinct disable-only port; pausing delivery cannot disable opt-out."""
        return ConfiguredDigestUnsubscribe(accounts)

    def disable_verified_email(self, proof: VerifiedUnsubscribeProof,
                               revision: CandidateCredentialRevision) -> DigestConsentHead:
        return self.for_unsubscribe(self.accounts).disable_verified_email(proof, revision)

    def execute_once(self, revision: CandidateCredentialRevision, *, email_sha256: str,
                     consent_generation: int, policy_version: str, message_sha256: str,
                     callback: Callable[[], ProviderResult],
                     before_retry: Callable[[ProviderResult], None] | None = None,
                     expected_week: str | None = None) -> ProviderResult:
        if not callable(callback) or before_retry is not None and not callable(before_retry):
            raise GuardDenied("Original trusted delivery callback required")
        previous = None
        for ordinal in range(1, 4):
            begin = self._invoke(lambda native, previous=previous: native.begin(revision,
                email_sha256=email_sha256, consent_generation=consent_generation,
                policy_version=policy_version, message_sha256=message_sha256,
                previous=previous, expected_week=expected_week))
            self._invoke(lambda native, begin=begin: native.claim(begin, revision, email_sha256))
            # All configured/native transactions and both witness barriers have
            # finished before this first possible provider/SQL-budget callback.
            _enabled()
            try:
                result = _result(callback())
            except MailBudgetError as error:
                # Only the budget's acknowledged post-commit handoff may carry a
                # known provider outcome. Missing/unknown never gains a receipt.
                if error.committed_result is None:
                    raise GuardUnavailable("Digest provider outcome is unavailable; no retry") from None
                result = _result(error.committed_result)
            except Exception:
                raise GuardUnavailable("Digest callback outcome unknown; no retry") from None
            previous = self._invoke(lambda native, begin=begin, result=result: native.record_outcome(begin, result))
            if result.outcome != "known_retryable" or ordinal == 3 or before_retry is None:
                return result
            # The trusted hook owns actual Retry-After custody. No next BEGIN is
            # attempted if it cannot honor that provider wait within its bound.
            _enabled()
            before_retry(result)
            _enabled()
        return result


class ConfiguredDigestUnsubscribe:
    """Only exact email disable, including while valid delivery flags are false.

    There is deliberately no generic operation dispatcher, preference creation,
    enabling, sending, read authority or reusable permit on this narrow port.
    """
    def __init__(self, accounts: ConfiguredCandidateAccounts | None = None):
        _valid_flags()
        self.accounts = accounts if accounts is not None else production_candidate_accounts()
        if type(self.accounts) is not ConfiguredCandidateAccounts:
            raise GuardUnavailable("Exact configured candidate lifecycle required")

    def disable_verified_email(self, proof: VerifiedUnsubscribeProof,
                               revision: CandidateCredentialRevision) -> DigestConsentHead:
        _valid_flags()
        if type(proof) is not VerifiedUnsubscribeProof or type(revision) is not CandidateCredentialRevision:
            raise GuardDenied("Original verified unsubscribe proof and current SQL revision required")
        def disable(_service):
            _valid_flags()
            native = GcpDigestAuthority(self.accounts._composition().scoped)
            # The low-level writer revalidates seal/expiry/current identity,
            # immutable mailbox and exact existing head inside each transaction.
            result = native._disable_verified_email(revision, proof)
            proof.require_current(native.scoped._now())
            _valid_flags()
            return result
        result = self.accounts._invoke(disable)
        _valid_flags()
        return result
