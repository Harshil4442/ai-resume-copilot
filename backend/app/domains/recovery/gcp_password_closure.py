"""Disabled permanent denial closure; typed native writer and bounded closed cut.

No production client, reopen operation, signing or browser authority is supplied.
"""
from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, cast
from uuid import uuid4

from google.cloud.storage import Bucket, Client
from pydantic import TypeAdapter, ValidationError

from .contracts import canonical, fingerprint
from .gcp_buffer import BufferedRegistry, BufferedTransaction
from .gcp_closure_contracts import (
    AuthorityCloseIntent,
    AuthorityCloseReceipt,
    ClosedIntentReference,
    ClosedInventoryManifest,
    ClosedInventoryReceipt,
    NativeClosedControl,
    NativeHeadCut,
    closure_path,
)
from .gcp_contracts import AmbiguousCommit, JournalIntent, RegistryPin
from .gcp_journal import GcsWitnessFence, _exact_bytes, _generation, _metadata, _sdk_configuration
from .gcp_media import BoundedSink, DownloadClient
from .gcp_pairing import _checked_event, pairing_control_record
from .gcp_pairing_attempts import GcsPairingAttempts
from .gcp_pairing_contracts import PairingJournalIntent
from .gcp_password_lifetime_contracts import (
    AdvancePasswordAuthGeneration,
    DeletePasswordSubject,
    PasswordLifetimeCommand,
    PasswordLifetimeIntent,
    RevokePasswordWebSession,
    TombstonePasswordAccount,
)
from .gcp_publication import GcpPublicationCoordinator
from .gcp_publication_contracts import PublicationSealReceipt
from .gcp_service import control_record
from .store import GuardDenied, GuardUnavailable

_COMMAND = TypeAdapter(PasswordLifetimeCommand)
_DENIALS = (RevokePasswordWebSession, AdvancePasswordAuthGeneration, TombstonePasswordAccount,
           DeletePasswordSubject)


def _bytes(value: dict) -> bytes:
    raw = canonical(value).encode()
    if not 0 < len(raw) <= 65_536:
        raise GuardDenied("Protected closure bytes exceed their budget")
    return raw


def _same(value: dict | None, expected: dict) -> bool:
    return value is not None and canonical(value) == canonical(expected)


def _command(command: PasswordLifetimeCommand) -> PasswordLifetimeCommand:
    if type(command) not in _DENIALS:
        raise GuardDenied("Protected closure accepts only a typed lifetime denial")
    return _COMMAND.validate_python(command.model_dump(mode="json"))


class GcsAuthorityClosure:
    """Pinned explicit Storage port; all reads/writes are outside native transactions."""

    def __init__(self, bucket: Bucket, *, rpc_timeout: float = 2.0):
        self._creator = GcsPairingAttempts(bucket, rpc_timeout=rpc_timeout)
        self.bucket, self.rpc_timeout = bucket, rpc_timeout

    def create(self, body: AuthorityCloseIntent) -> AuthorityCloseReceipt | None:
        raw = _bytes(body.model_dump(mode="json"))
        path = closure_path(body.pin)
        generation = self._creator._fresh_create(path, raw)
        if generation is None:
            return None
        return AuthorityCloseReceipt(body=body.model_copy(deep=True), bucket=self.bucket.name,
            path=path, generation=generation, sha256=hashlib.sha256(raw).hexdigest())

    def verify(self, receipt: AuthorityCloseReceipt) -> None:
        receipt = AuthorityCloseReceipt.model_validate_json(canonical(receipt.model_dump(mode="json")))
        if receipt.bucket != self.bucket.name:
            raise GuardUnavailable("Protected closure bucket pin changed")
        raw = _bytes(receipt.body.model_dump(mode="json"))
        try:
            _exact_bytes(self.bucket, receipt.path, raw, generation=receipt.generation,
                         timeout=self.rpc_timeout)
        except Exception:
            raise GuardUnavailable("Protected closure generation/bytes are unavailable") from None


class ClosedPasswordDenialFence:
    """Only the owning denial writer receives this result, never an action caller.

    Complete canonical control bytes are private; all public exports are detached.
    Native snapshots recheck them. This never asserts OPEN or clears the closure.
    """

    def __init__(self, witness: GcsWitnessFence, closure: GcsAuthorityClosure,
                 control: NativeClosedControl, publication: GcpPublicationCoordinator):
        if type(witness) is not GcsWitnessFence or type(closure) is not GcsAuthorityClosure:
            raise GuardDenied("Actual pinned witness and protected closure ports are required")
        if witness.pin.bucket != closure.bucket.name or witness.pin.registry != control.pin:
            raise GuardDenied("Closed denial witness/resource pin disagrees")
        if type(publication) is not GcpPublicationCoordinator or publication.resource.pin.target != control.pin:
            raise GuardDenied("Exact independent full-publication authority is required")
        self.publication = publication
        self.witness, self.closure = witness, closure
        self._raw = _bytes(control.model_dump(mode="json"))

    @property
    def control(self) -> NativeClosedControl:
        return NativeClosedControl.model_validate_json(self._raw)

    def command_matches(self, command: PasswordLifetimeCommand) -> bool:
        return fingerprint(_command(command).model_dump(mode="json")) == self.control.close.body.command_sha256

    def check(self, pin: RegistryPin) -> None:
        self._check(pin, status_only=False)

    def check_status(self, pin: RegistryPin) -> None:
        self._check(pin, status_only=True)

    def _check(self, pin: RegistryPin, *, status_only: bool) -> None:
        control = self.control
        if pin != control.pin:
            raise GuardUnavailable("Closed denial authority pin changed")
        self.closure.verify(control.close)
        self.publication.check_closed(control.close, status_only=status_only)
        # _verify_current alone grants nothing. This typed path verifies the exact
        # protected CLOSED receipt on both sides and allows denial retention only.
        self.witness._verify_current(pin)
        self.closure.verify(control.close)


@dataclass(frozen=True)
class PasswordCloseExecution:
    status: Literal["COMMITTED", "UNKNOWN"]
    close: AuthorityCloseReceipt | None = None
    denial_fence: ClosedPasswordDenialFence | None = None


class GcpPasswordCloseCoordinator:
    def __init__(self, registry: BufferedRegistry, pin: RegistryPin, *, epoch_generation: int,
                 witness: GcsWitnessFence, closure: GcsAuthorityClosure,
                 publication: GcpPublicationCoordinator | None = None,
                 now_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000):
        if (type(witness) is not GcsWitnessFence or type(closure) is not GcsAuthorityClosure
                or registry.rpc.database_resource != pin.database or witness.pin.registry != pin
                or witness.pin.bucket != closure.bucket.name):
            raise GuardDenied("Close requires one exact native/witness/protected resource pin")
        if (type(publication) is not GcpPublicationCoordinator or publication.resource.pin.target != pin
                or publication.resource.bucket != witness.pin.bucket):
            raise GuardDenied("Close requires independent publication admission for every journal")
        self.publication = publication
        pairing_control_record(pin, epoch_generation)
        self.registry, self.pin, self.epoch_generation = registry, pin.model_copy(deep=True), epoch_generation
        self.witness, self.closure, self.now_ms = witness, closure, now_ms

    def close(self, command: PasswordLifetimeCommand) -> PasswordCloseExecution:
        command = _command(command)
        # Delayed import avoids the typed lifetime/closed-fence dependency cycle.
        # This validates actual native ownership and registration, not a client
        # role, account number or arbitrary callback. It emits no execution plan.
        from .gcp_password_lifetime import GcpPasswordLifetimeCoordinator
        validator = GcpPasswordLifetimeCoordinator(self.registry, self.pin,
            epoch_generation=self.epoch_generation, fence=self.witness, now_ms=self.now_ms)
        validator.validate_denial_before_close(command)
        now = self.now_ms()
        if type(now) is not int or not 0 < now <= 2**53 - 1:
            raise GuardDenied("Protected close clock is invalid")
        self.witness.check(self.pin)
        body = AuthorityCloseIntent(pin=self.pin, close_id=uuid4(),
            command_sha256=fingerprint(command.model_dump(mode="json")), created_at_ms=now)
        receipt = self.closure.create(body)
        if receipt is None:
            # Lost/412/create-without-ACK cannot mint a new owning closed writer.
            # Any retained object already denies ordinary witness checks.
            return PasswordCloseExecution("UNKNOWN")
        self.closure.verify(receipt)
        if not self.publication.close(receipt):
            return PasswordCloseExecution("UNKNOWN", receipt)
        captured: list[NativeClosedControl] = []
        def before() -> None:
            self.closure.verify(receipt)
            self.witness._verify_current(self.pin)
            self.closure.verify(receipt)
        def stage(tx: BufferedTransaction) -> None:
            if (not _same(tx.get("control", "meta"), control_record(self.pin))
                    or not _same(tx.get("pairing_control", "current"),
                        pairing_control_record(self.pin, self.epoch_generation))):
                raise GuardUnavailable("Only current OPEN native control can be closed")
            head = NativeHeadCut.model_validate(tx.get("head", "global"))
            control = NativeClosedControl(pin=self.pin, close=receipt, head_at_close=head)
            # This restricted operator closes one exact record. Runtime put()
            # remains unable to change control; no corresponding OPEN method exists.
            tx._stage("control", "meta", control.model_dump(mode="json"), immutable=False)
            captured[:] = [control]
        try:
            self.registry.run(stage, before_attempt=before)
        except (AmbiguousCommit, GuardUnavailable, GuardDenied, ValidationError):
            return PasswordCloseExecution("UNKNOWN", receipt)
        try:
            before()
            control = captured[0]
            rows = self.registry.read_many((("control", "meta"), ("head", "global")))
            if (not _same(rows["control", "meta"], control.model_dump(mode="json"))
                    or not _same(rows["head", "global"], control.head_at_close.model_dump(mode="json"))):
                raise GuardUnavailable("Native close cut changed before its own acknowledgement")
            before()
        except (GuardUnavailable, GuardDenied, ValidationError):
            return PasswordCloseExecution("UNKNOWN", receipt)
        return PasswordCloseExecution("COMMITTED", receipt,
            ClosedPasswordDenialFence(self.witness, self.closure, control, self.publication))


class GcsClosedInventory:
    """Bounded exhaustive live-object inventory; no sampling or restore grant."""

    def __init__(self, bucket: Bucket, *, rpc_timeout: float = 2.0,
                 monotonic: Callable[[], float] = time.monotonic):
        self._creator = GcsPairingAttempts(bucket, rpc_timeout=rpc_timeout)
        self.bucket, self.rpc_timeout, self.monotonic = bucket, rpc_timeout, monotonic

    def _list(self, pin: RegistryPin, deadline: float) -> tuple[tuple[str, str], ...]:
        _sdk_configuration(self.bucket)
        authority = hashlib.sha256(str(pin.authority_id).encode()).hexdigest()
        prefix = f"authority-intents/{authority}/"
        iterator = self.bucket.client.list_blobs(self.bucket, prefix=prefix, page_size=16,
            max_results=65, retry=None, timeout=self.rpc_timeout)
        listed: list[tuple[str, str]] = []
        pages = 0
        for page in iterator.pages:
            pages += 1
            if pages > 5 or self.monotonic() >= deadline:
                raise GuardUnavailable("Closed inventory page/deadline budget exceeded")
            for blob in page:
                if len(listed) == 64:
                    raise GuardUnavailable("Complete closed inventory exceeds its object budget")
                path = blob._properties.get("name")
                if type(path) is not str or not path.startswith(prefix):
                    raise GuardUnavailable("Closed inventory returned a foreign path")
                listed.append((path, _generation(blob._properties.get("generation"))))
        if iterator.next_page_token or len({path for path, _ in listed}) != len(listed):
            raise GuardUnavailable("Closed inventory has a gap, duplicate or unconsumed page")
        return tuple(sorted(listed))

    def _read(self, path: str, generation: str) -> bytes:
        _sdk_configuration(self.bucket)
        blob = self.bucket.blob(path, generation=int(generation))
        blob.reload(if_generation_match=int(generation), retry=None, timeout=self.rpc_timeout)
        size = blob._properties.get("size")
        if (type(size) is not str or not size.isascii() or not size.isdecimal()
                or size.startswith("0") or len(size) > 5 or not 0 < int(size) <= 65_536
                or _metadata(blob, int(size)) != generation):
            raise GuardUnavailable("Closed inventory object size/generation is invalid")
        length = int(size)
        proxy = DownloadClient(self.bucket.client, self.bucket.name, path, generation, length)
        sink = BoundedSink(length)
        try:
            self.bucket.blob(path, generation=int(generation)).download_to_file(sink,
                client=cast(Client, proxy), start=0, end=length, raw_download=True,
                single_shot_download=False, if_generation_match=int(generation),
                retry=None, timeout=self.rpc_timeout, checksum=None)
            raw = sink.getvalue()
        finally:
            proxy.close_responses()
            sink.close()
        if len(raw) != length:
            raise GuardUnavailable("Closed inventory object bytes are incomplete")
        return raw

    @staticmethod
    def _reference(path: str, generation: str, raw: bytes, pin: RegistryPin) -> ClosedIntentReference:
        parsed: JournalIntent | PairingJournalIntent | PasswordLifetimeIntent | None = None
        for kind in (JournalIntent, PairingJournalIntent, PasswordLifetimeIntent):
            try:
                candidate = kind.model_validate_json(raw)
                if _bytes(candidate.model_dump(mode="json")) == raw:
                    parsed = candidate
                    break
            except (ValidationError, ValueError, TypeError):
                continue
        if parsed is None or parsed.pin.authority_id != pin.authority_id:
            raise GuardUnavailable("Closed inventory contains unsupported or foreign canonical intent bytes")
        authority = hashlib.sha256(str(pin.authority_id).encode()).hexdigest()
        expected = f"authority-intents/{authority}/{parsed.partition}/{parsed.operation_id}.json"
        if path != expected:
            raise GuardUnavailable("Closed inventory intent path disagrees with its canonical binding")
        kind_label: Literal["safety_effect", "pairing_command", "password_lifetime_effects"] = (
            "safety_effect" if isinstance(parsed, JournalIntent) else "pairing_command"
            if isinstance(parsed, PairingJournalIntent) else "password_lifetime_effects")
        return ClosedIntentReference(path=path, generation=generation,
            sha256=hashlib.sha256(raw).hexdigest(), byte_size=len(raw), partition=parsed.partition, kind=kind_label)

    def seal(self, registry: BufferedRegistry, closed: ClosedPasswordDenialFence) -> tuple[
            ClosedInventoryManifest, ClosedInventoryReceipt] | None:
        if type(closed) is not ClosedPasswordDenialFence:
            raise GuardDenied("An exact closed denial context is required for inventory")
        control = closed.control
        if registry.rpc.database_resource != control.pin.database:
            raise GuardDenied("Closed inventory native database pin disagrees")
        deadline = self.monotonic() + 30.0
        def check() -> dict:
            if self.monotonic() >= deadline:
                raise GuardUnavailable("Closed inventory total admission deadline exceeded")
            closed.check_status(control.pin)
            rows = registry.read_many((("control", "meta"), ("head", "global")))
            if not _same(rows["control", "meta"], control.model_dump(mode="json")):
                raise GuardUnavailable("Closed inventory native closure changed")
            head = NativeHeadCut.model_validate(rows["head", "global"])
            if (head.sequence not in {control.head_at_close.sequence, control.head_at_close.sequence + 1}
                    or (head.sequence == control.head_at_close.sequence
                        and head.digest != control.head_at_close.digest)):
                raise GuardUnavailable("Closed inventory native event cut rolled back or escaped its owning denial")
            return head.model_dump(mode="json")
        head = check()
        try:
            publication = closed.publication
            if publication.resource.pin.journal_bucket != self.bucket.name:
                raise GuardUnavailable("Publication and export journal bucket pins disagree")
            cut = publication.seal(control.close)
            if cut is None:
                return None
            records = publication.complete(cut)
            expected_paths = {v.reference.path for v in records}
            # Export every retained full record, including native/publication
            # UNKNOWN orphans. Existing exact bytes are inventory evidence only.
            for item in records:
                if self.monotonic() >= deadline:
                    raise GuardUnavailable("Complete publication export deadline exceeded")
                raw_intent = canonical(item.intent).encode()
                self._creator._fresh_create(item.reference.path, raw_intent)
                _exact_bytes(self.bucket, item.reference.path, raw_intent, generation=None, timeout=self.rpc_timeout)
            listed = self._list(control.pin, deadline)
            if {path for path, _ in listed} != expected_paths:
                raise GuardUnavailable("Canonical journal prefix contains missing or unregistered publications")
            entries: list[ClosedIntentReference] = []
            post_close: list[PasswordLifetimeIntent] = []
            for path, generation in listed:
                if self.monotonic() >= deadline:
                    raise GuardUnavailable("Closed inventory byte-read deadline exceeded")
                raw = self._read(path, generation)
                reference = self._reference(path, generation, raw, control.pin)
                entries.append(reference)
                if reference.kind == "password_lifetime_effects":
                    candidate = PasswordLifetimeIntent.model_validate_json(raw)
                    event = _checked_event(candidate.effects.event)
                    if (candidate.pin == control.pin
                            and fingerprint(candidate.command.model_dump(mode="json")) == control.close.body.command_sha256
                            and event["sequence"] == head["sequence"]
                            and event["previous"] == control.head_at_close.digest
                            and event["digest"] == head["digest"]):
                        before_control = next((item.value for item in candidate.effects.before
                            if item.namespace == "control" and item.key == "meta"), None)
                        if not _same(before_control, control.model_dump(mode="json")):
                            raise GuardUnavailable("Closed inventory denial lacks its exact native closure precondition")
                        post_close.append(candidate)
            if head["sequence"] == control.head_at_close.sequence + 1:
                if len(post_close) != 1:
                    raise GuardUnavailable("Closed inventory lacks its complete owning post-close denial cut")
                from .gcp_password_lifetime import GcpPasswordLifetimeCoordinator
                from .gcp_password_lifetime_journal import GcsPasswordLifetimeJournal
                reader = GcpPasswordLifetimeCoordinator(registry, control.pin,
                    epoch_generation=post_close[0].epoch_generation, closed_denial=closed,
                    journal=GcsPasswordLifetimeJournal(self.bucket, rpc_timeout=self.rpc_timeout))
                if reader.status(post_close[0]).status != "COMMITTED":
                    raise GuardUnavailable("Closed inventory post-close denial lacks exact retained native evidence")
            if (listed != self._list(control.pin, deadline) or head != check()
                    or publication.complete(cut) != records):
                raise GuardUnavailable("Closed inventory changed during its complete cut")
            gate_raw = _bytes(cut.gate.model_dump(mode="json"))
            gate_path = f"authority-publication-seals/{fingerprint(cut.gate.pin.model_dump(mode='json'))}/{cut.gate.seal_id}.json"
            gate_generation = self._creator._fresh_create(gate_path, gate_raw)
            if gate_generation is None:
                return None
            _exact_bytes(self.bucket, gate_path, gate_raw, generation=gate_generation, timeout=self.rpc_timeout)
            assert cut.gate.seal_id is not None
            seal_receipt = PublicationSealReceipt(pin=cut.gate.pin, seal_id=cut.gate.seal_id,
                close_sha256=fingerprint(control.close.model_dump(mode="json")),
                entries_sha256=fingerprint({"entries": [v.model_dump(mode="json") for v in cut.gate.entries]}),
                path=gate_path, generation=gate_generation, sha256=hashlib.sha256(gate_raw).hexdigest())
            authority = hashlib.sha256(str(control.pin.authority_id).encode()).hexdigest()
            manifest = ClosedInventoryManifest(control=control, head_while_closed=NativeHeadCut.model_validate(head),
                prefix=f"authority-intents/{authority}/", entries=tuple(entries),
                partitions=tuple(sorted({item.partition for item in entries})),
                publication_cut=seal_receipt.model_dump(mode="json"),
                publication_resource=publication.resource.model_dump(mode="json"))
            raw = _bytes(manifest.model_dump(mode="json"))
            pin_digest = fingerprint(control.pin.model_dump(mode="json"))
            path = f"authority-closed-cuts/{pin_digest}/{control.close.body.close_id}.json"
            manifest_generation = self._creator._fresh_create(path, raw)
            if manifest_generation is None:
                return None
            # A retained manifest alone grants nothing. Refuse even its verified
            # inventory receipt if any writer raced its last observation.
            if (listed != self._list(control.pin, deadline) or head != check()
                    or publication.complete(cut) != records):
                raise GuardUnavailable("Closed inventory changed before its final acknowledgement")
            _exact_bytes(self.bucket, path, raw, generation=manifest_generation, timeout=self.rpc_timeout)
            _exact_bytes(self.bucket, gate_path, gate_raw, generation=gate_generation, timeout=self.rpc_timeout)
            publication.complete(cut)
            check()
            if self.monotonic() >= deadline:
                raise GuardUnavailable("Closed inventory deadline exceeded before return")
            return manifest, ClosedInventoryReceipt(bucket=self.bucket.name, path=path,
                generation=manifest_generation, sha256=hashlib.sha256(raw).hexdigest())
        except (GuardUnavailable, GuardDenied, ValidationError):
            raise
        except Exception:
            raise GuardUnavailable("Complete closed inventory is unavailable") from None
