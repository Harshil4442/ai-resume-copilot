"""Read-only database and journal custody for the continuity release.

This module has no provider client or operator entry point. The native release
controller must independently authenticate source, policy and service state.
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import re
import stat
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "infra/gcp"), str(ROOT / "backend"), str(ROOT)]

import rolling_release as rolling  # noqa: E402

from scripts import neon_continuity_identity as neon  # noqa: E402

KIND = "hirewiz_schema27_continuity_bridge_v1"
JOURNAL_BINDING = "continuity.bridge.binding"
JOURNAL_LOCATION = "continuity.bridge.location"
MIGRATION_FENCE = "continuity.bridge.migration-intent"
RUNTIME_REFERENCE = "hirewiz-fresh-runtime-db-20261013:3"
COORDINATOR_REFERENCE = "hirewiz-resume-coordinator-db-20261015:2"
ROLE_RECEIPT_SHA256 = "4ba95b0fe7aa2d4b88195b797982369ad86214116bddb99feb0da97e1bddbc2f"
DESTINATION_RECEIPT_SHA256 = "84dfd33cec9fef36dad677bda8333b17014f593640dc46ce1b755199687b3309"
ROLE_BUNDLE = Path("/Users/harshil/.codex/hirewiz-recovery-keys/hirewiz-neon-free-roles-20261115.json")
BUNDLE_SOURCE = "continuity_private_role_bundle_20261115"
MAX_BUNDLE_BYTES = 4096


def _need(value, reason):
    rolling.need(value, "schema27_continuity_" + reason)


def _nonce(value: str) -> bool:
    return type(value) is str and re.fullmatch(r"[a-f0-9]{32}", value) is not None


def _hash(value: str) -> bool:
    return type(value) is str and rolling.HASH.fullmatch(value) is not None


@dataclass(frozen=True, slots=True)
class ContinuityDatabasePin:
    observer_commitment_sha256: str
    project: str = neon.PROJECT
    branch: str = neon.BRANCH
    endpoint: str = neon.ENDPOINT
    host: str = neon.HOST
    database: str = neon.DATABASE
    database_oid: int = neon.DATABASE_OID
    cluster_sha256: str = neon.CLUSTER_SHA256
    runtime_reference: str = RUNTIME_REFERENCE
    runtime_role: tuple[str, int] = (neon.RUNTIME, neon.RUNTIME_OID)
    coordinator_reference: str = COORDINATOR_REFERENCE
    coordinator_role: tuple[str, int] = ("hirewiz_free_resume_coordinator_20261115", 49158)
    observer_role: tuple[str, int] = (neon.OBSERVER, neon.OBSERVER_OID)
    observer_source: str = BUNDLE_SOURCE
    migrator_role: tuple[str, int] = ("hirewiz_free_migrator_20261115", 49152)
    role_receipt_sha256: str = ROLE_RECEIPT_SHA256
    destination_receipt_sha256: str = DESTINATION_RECEIPT_SHA256

    def validate(self) -> None:
        _need(type(self) is ContinuityDatabasePin and _hash(self.observer_commitment_sha256)
              and type(self.database_oid) is int and all(
                  type(role) is tuple and len(role) == 2 and type(role[0]) is str and type(role[1]) is int
                  for role in (self.observer_role, self.runtime_role, self.coordinator_role, self.migrator_role)
              ), "database_pin_invalid")
        _need(self == ContinuityDatabasePin(self.observer_commitment_sha256), "database_pin_changed")


@dataclass(frozen=True, slots=True)
class BridgeCustody:
    nonce: str
    bridge_metadata_sha256: str
    database: ContinuityDatabasePin
    kind: str = KIND

    def validate(self) -> None:
        _need(type(self) is BridgeCustody and self.kind == KIND and _nonce(self.nonce)
              and _hash(self.bridge_metadata_sha256) and type(self.database) is ContinuityDatabasePin,
              "bridge_pin_invalid")
        self.database.validate()

    def journal_binding(self) -> dict:
        self.validate()
        return {"kind": self.kind, "database_sha256": rolling.sha(asdict(self.database))}


def verify_journal(custody: BridgeCustody, journal: rolling.Journal) -> None:
    """Read an existing binding; no reconstruction or issuance is permitted."""
    _need(type(custody) is BridgeCustody and type(journal) is rolling.Journal, "journal_type_invalid")
    binding = custody.journal_binding()
    _need(journal.plan_hash == rolling.sha(asdict(custody)), "journal_owner_changed")
    _need(journal.read(JOURNAL_BINDING) == binding | {"plan_sha256": journal.plan_hash},
          "journal_database_binding_changed")


def journal_location(journal: rolling.Journal) -> dict:
    directory = journal.directory
    status = directory.stat(follow_symlinks=False)
    _need(stat.S_ISDIR(status.st_mode) and status.st_uid == os.getuid()
          and stat.S_IMODE(status.st_mode) == 0o700 and directory.resolve() == directory,
          "lifecycle_journal_location")
    return {"path": str(directory), "device": status.st_dev, "inode": status.st_ino}


@contextmanager
def lifecycle_lock(journal: rolling.Journal):
    """Serialize bridge controls with migration intent before either inner lock."""
    _need(type(journal) is rolling.Journal, "journal_type_invalid")
    _need(journal.read(JOURNAL_LOCATION) == journal_location(journal) | {"plan_sha256": journal.plan_hash},
          "lifecycle_journal_location")
    location = journal.directory / "continuity-lifecycle.lock"
    fd = os.open(location, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        owner = os.fstat(fd)
        _need(stat.S_ISREG(owner.st_mode) and owner.st_uid == os.getuid()
              and stat.S_IMODE(owner.st_mode) == 0o600 and owner.st_nlink == 1, "lifecycle_lock_unverified")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def _reject_duplicates(pairs: list[tuple[str, str]]) -> dict[str, str]:
    value = dict(pairs)
    _need(len(value) == len(pairs), "private_bundle_shape_invalid")
    return value


def _verify_private_disk() -> None:
    try:
        result = subprocess.run(
            ["/usr/bin/fdesetup", "status"], capture_output=True, timeout=8, check=False,
            stdin=subprocess.DEVNULL, env={"PATH": "/usr/bin:/bin"},
        )
    except (OSError, subprocess.SubprocessError):
        raise rolling.RollingDenied("schema27_continuity_private_disk_unavailable") from None
    _need(result.returncode == 0 and result.stdout.strip() == b"FileVault is On.", "private_disk_unverified")


def _file_identity(value: os.stat_result) -> tuple[int, ...]:
    return (value.st_dev, value.st_ino, value.st_mode, value.st_nlink, value.st_uid,
            value.st_size, value.st_ctime_ns, value.st_mtime_ns)


def _observer_password() -> str:
    """Read the fixed previous provisioning bundle without creating or rewriting it."""
    _verify_private_disk()
    try:
        _need(ROLE_BUNDLE.parent.resolve(strict=True) == ROLE_BUNDLE.parent, "private_directory_invalid")
        root = os.open(ROLE_BUNDLE.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parent = os.fstat(root)
            _need(parent.st_uid == os.getuid() and stat.S_IMODE(parent.st_mode) == 0o700,
                  "private_directory_invalid")
            fd = os.open(ROLE_BUNDLE.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root)
            with os.fdopen(fd, "rb") as bundle:
                before = os.fstat(bundle.fileno())
                _need(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                      and stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1
                      and 0 < before.st_size <= MAX_BUNDLE_BYTES,
                      "private_bundle_metadata_invalid")
                raw = bundle.read(MAX_BUNDLE_BYTES + 1)
                after = os.stat(ROLE_BUNDLE.name, dir_fd=root, follow_symlinks=False)
                _need(len(raw) == before.st_size and _file_identity(before) == _file_identity(after),
                      "private_bundle_changed")
        finally:
            os.close(root)
        values = json.loads(raw, object_pairs_hook=_reject_duplicates)
        _need(type(values) is dict and set(values) == {"observer", "runtime", "coordinator"}
              and all(type(value) is str and re.fullmatch(r"[A-Za-z0-9_-]{56}", value)
                      for value in values.values()), "private_bundle_shape_invalid")
        return values["observer"]
    except rolling.RollingDenied:
        raise
    except (OSError, ValueError, RecursionError):
        raise rolling.RollingDenied("schema27_continuity_private_bundle_unavailable") from None


def _commitment(nonce: str, password: str) -> str:
    scope = f"{KIND}\0{neon.PROJECT}\0{neon.OBSERVER}\0{nonce}".encode()
    return hmac.new(password.encode(), scope, hashlib.sha256).hexdigest()


def capture_database_pin(nonce: str) -> ContinuityDatabasePin:
    """Capture commitment metadata for an operator who independently verified source."""
    _need(_nonce(nonce), "nonce_invalid")
    return ContinuityDatabasePin(_commitment(nonce, _observer_password()))


def observe_journal(custody: BridgeCustody, journal: rolling.Journal, purpose: neon.Purpose) -> neon.Observation:
    """Keep actual credential access downstream of exact database and journal custody."""
    verify_journal(custody, journal)
    _need(type(purpose) is neon.Purpose, "purpose_invalid")
    password = _observer_password()
    _need(hmac.compare_digest(_commitment(custody.nonce, password), custody.database.observer_commitment_sha256),
          "observer_commitment_changed")
    return neon.observe(purpose, observer_password=password)
