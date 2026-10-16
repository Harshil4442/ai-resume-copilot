"""Fixed public trust and sanitized provenance for a reviewed Linux release.

These files establish transport inputs and historical native target provenance,
never current authentication, a writer cut, provider authority or release success.
No environment variable or arbitrary path selects an alternative input.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path("/opt/hirewiz-release")
FILES = {
    "ca": (
        "ca/cert.pem",
        286583,
        "426806ef8b5e5711c484e53522ca10e2f103c0d514a6699bdf615d45134477a1",
    ),
    "source": (
        "provenance/source-control.json",
        1255,
        "875747de81988a2504b1d75b78813b96f274f2e338005bc1f69c7b4bb730ad0a",
    ),
    "roles": (
        "provenance/fresh-role-creation.json",
        1928,
        "44af0a94b0912c3649e4fc757e972c863dca2f03c423d9c1e9a14266c7c46c39",
    ),
    "setup": (
        "provenance/fresh-credentials.json",
        1348,
        "88cc06834b6f7d0f237020bb2fea83c2afd4c1f380e6b6cb64324ee8cabe305f",
    ),
}
# Exact initial references in the historical setup receipt; not current authority.
REFERENCES = {
    "replacement_runtime": "hirewiz-fresh-runtime-db-20261013:1",
    "replacement_migration": "hirewiz-fresh-migrator-db-20261013:1",
    "observer": "hirewiz-fresh-observer-db-20261013:1",
}

SECRET_NAMES = {
    "replacement_runtime": "hirewiz-fresh-runtime-db-20261013",
    "replacement_migration": "hirewiz-fresh-migrator-db-20261013",
    "observer": "hirewiz-fresh-observer-db-20261013",
}


class LinuxExecutionFilesDenied(RuntimeError):
    """Finite nonsecret refusal; never a file body or arbitrary path."""


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise LinuxExecutionFilesDenied("linux_provenance_duplicate_key")
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class LinuxExecutionFiles:
    root: Path = ROOT

    @property
    def root_certificate(self) -> str:
        return str(ROOT / "ca/cert.pem")

    def read(self, kind: str) -> bytes:
        if (
            type(self) is not LinuxExecutionFiles
            or self.root != ROOT
            or sys.platform != "linux"
            or kind not in FILES
        ):
            raise LinuxExecutionFilesDenied("linux_execution_files_scope_invalid")
        relative, exact_size, expected = FILES[kind]
        path = ROOT / relative
        # All parent components are fixed; reject aliases and writable ancestry.
        for parent in reversed(path.parents):
            metadata = parent.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != 0
                or stat.S_IMODE(metadata.st_mode) & 0o022
            ):
                raise LinuxExecutionFilesDenied("linux_execution_files_parent_invalid")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            before = os.fstat(descriptor)
            cap = 1048576 if kind == "ca" else 131072
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != 0
                or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) != 0o444
                or not 1 <= before.st_size <= cap
                or exact_size is not None
                and before.st_size != exact_size
            ):
                raise LinuxExecutionFilesDenied(
                    "linux_execution_files_metadata_invalid"
                )
            raw = bytearray()
            while True:
                chunk = os.read(descriptor, min(65536, cap + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > cap:
                    raise LinuxExecutionFilesDenied("linux_execution_files_byte_bound")
            after = os.fstat(descriptor)

            def identity(item: Any) -> tuple[Any, ...]:
                return (
                    item.st_dev,
                    item.st_ino,
                    item.st_size,
                    item.st_mtime_ns,
                    item.st_ctime_ns,
                    item.st_uid,
                    item.st_mode,
                    item.st_nlink,
                )

            if (
                identity(before) != identity(after)
                or len(raw) != before.st_size
                or hashlib.sha256(raw).hexdigest() != expected
            ):
                raise LinuxExecutionFilesDenied("linux_execution_files_bytes_changed")
            return bytes(raw)
        finally:
            os.close(descriptor)

    def validate(self) -> None:
        for kind in FILES:
            self.read(kind)

    def verify_fresh_reference(self, alias: str, reference: str) -> None:
        if (
            type(alias) is not str
            or alias not in SECRET_NAMES
            or type(reference) is not str
            or re.fullmatch(
                re.escape(SECRET_NAMES[alias]) + r":[1-9][0-9]{0,8}", reference
            )
            is None
        ):
            raise LinuxExecutionFilesDenied("linux_fresh_observed_reference_mismatch")
        # This receipt fixes initial preparation history only. The caller must
        # genuinely Describe/Access/recheck/authenticate the selected version.
        value = json.loads(self.read("setup"), object_pairs_hook=_unique)
        if (
            type(value) is not dict
            or value.get("status") != "fresh_credentials_ready_empty_target"
            or value.get("project_id") != "shiny-cell-70214683"
            or value.get("branch_id") != "br-square-mode-b4g6sxrz"
            or value.get("endpoint_id") != "ep-weathered-tooth-b43oa1n6"
            or type(value.get("database_oid")) is not int
            or value["database_oid"] != 16395
            or value.get("role_oids")
            != {"runtime": 24576, "migration": 24578, "observer": 24580}
            or value.get("new_numeric_references")
            != {
                "runtime": REFERENCES["replacement_runtime"],
                "migration": REFERENCES["replacement_migration"],
                "observer": REFERENCES["observer"],
            }
            or value.get("new_secret_versions_verified") is not True
            or value.get("role_activation_and_authentication_verified") is not True
            or value.get("outcome_requires_reconciliation") is not False
            or value.get("production_switch") is not False
        ):
            raise LinuxExecutionFilesDenied("linux_fresh_setup_provenance_mismatch")
