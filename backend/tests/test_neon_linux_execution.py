"""Synthetic filesystem and collector controls; no Linux/native/provider proof."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import neon_direct_identity as neon
from scripts import neon_execution_files as files


@pytest.fixture
def package(tmp_path, monkeypatch):
    root = tmp_path / "fixed-linux-test-package"
    root.mkdir()
    monkeypatch.setattr(files, "ROOT", root)
    monkeypatch.setattr(files.sys, "platform", "linux")
    source = {
        "organization_id_from_actual_billing_href": neon.ORGANIZATION,
        "project_id": neon.NEON_PROJECT,
        "branch_id": neon.BRANCH,
        "endpoint_id": neon.ENDPOINT,
        "direct_host": neon.RELEASE_DIRECT_HOST,
        "pooler_host": neon.RELEASE_POOLER_HOST,
        "database_name": "neondb",
        "postgres_major": 17,
    }
    roles = {
        "status": "target_roles_created",
        "stage": "fresh_target_role_creation",
        "org_id": neon.ORGANIZATION,
        "project_id": neon.FRESH_NEON_PROJECT,
        "branch_id": neon.FRESH_BRANCH,
        "endpoint_id": neon.FRESH_ENDPOINT,
        "direct_host": neon.FRESH_DIRECT_HOST,
        "database": "neondb",
        "commit_acknowledged": True,
        "production_switch": False,
        "global_provider_fence_proven": False,
        "summary": {
            "role_oids": {name: oid for name, oid in neon.FRESH_RELEASE_ACTORS.values()}
        },
    }
    setup = {
        "status": "fresh_credentials_ready_empty_target",
        "project_id": neon.FRESH_NEON_PROJECT,
        "branch_id": neon.FRESH_BRANCH,
        "endpoint_id": neon.FRESH_ENDPOINT,
        "database_oid": 16395,
        "role_oids": {"runtime": 24576, "migration": 24578, "observer": 24580},
        "new_numeric_references": {
            "runtime": files.REFERENCES["replacement_runtime"],
            "migration": files.REFERENCES["replacement_migration"],
            "observer": files.REFERENCES["observer"],
        },
        "new_secret_versions_verified": True,
        "role_activation_and_authentication_verified": True,
        "outcome_requires_reconciliation": False,
        "production_switch": False,
    }
    contents = {
        "ca": b"public synthetic trust input",
        "source": json.dumps(source).encode(),
        "roles": json.dumps(roles).encode(),
        "setup": json.dumps(setup).encode(),
    }
    entries = {}
    for kind, (relative, _, _) in files.FILES.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents[kind])
        target.chmod(0o444)
        entries[kind] = (
            relative,
            len(contents[kind]),
            hashlib.sha256(contents[kind]).hexdigest(),
        )
    monkeypatch.setattr(files, "FILES", entries)
    monkeypatch.setattr(neon, "RELEASE_CONTROL_RECEIPT_SHA", entries["source"][2])
    monkeypatch.setattr(neon, "FRESH_CONTROL_RECEIPT_SHA", entries["roles"][2])
    state = {"uid": 0, "links": 1, "parent_writable": False, "closed": []}
    actual_lstat, actual_fstat, actual_close = Path.lstat, os.fstat, os.close

    def lstat(path):
        observed = actual_lstat(path)
        return SimpleNamespace(
            st_mode=(observed.st_mode & ~0o022)
            | (0o020 if state["parent_writable"] else 0),
            st_uid=0,
        )

    def fstat(descriptor):
        observed = actual_fstat(descriptor)
        names = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode")
        return SimpleNamespace(
            **{name: getattr(observed, name) for name in names},
            st_uid=state["uid"],
            st_nlink=state["links"],
        )

    def close(descriptor):
        state["closed"].append(descriptor)
        actual_close(descriptor)

    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(files.os, "fstat", fstat)
    monkeypatch.setattr(files.os, "close", close)
    return files.LinuxExecutionFiles(root), contents, state


def test_bounded_eof_reads_use_complete_exact_bytes(package, monkeypatch):
    binding, contents, state = package
    read = os.read
    monkeypatch.setattr(files.os, "read", lambda fd, count: read(fd, min(count, 3)))
    binding.validate()
    assert binding.read("ca") == contents["ca"]
    assert len(state["closed"]) == 5


@pytest.mark.parametrize("key,value", [("uid", 501), ("links", 2)])
def test_owner_and_link_refusal_closes_the_descriptor(package, key, value):
    binding, _, state = package
    state[key] = value
    with pytest.raises(files.LinuxExecutionFilesDenied, match="metadata_invalid"):
        binding.read("ca")
    assert len(state["closed"]) == 1


def test_writable_parent_or_file_and_symlink_refuse(package):
    binding, _, state = package
    state["parent_writable"] = True
    with pytest.raises(files.LinuxExecutionFilesDenied, match="parent_invalid"):
        binding.read("ca")
    assert not state["closed"]
    state["parent_writable"] = False
    target = binding.root / "ca/cert.pem"
    target.chmod(0o644)
    with pytest.raises(files.LinuxExecutionFilesDenied, match="metadata_invalid"):
        binding.read("ca")
    target.unlink()
    target.symlink_to(binding.root / "provenance/source-control.json")
    with pytest.raises(OSError):
        binding.read("ca")


def test_hash_change_and_scope_refuse(package, monkeypatch):
    binding, contents, state = package
    target = binding.root / "ca/cert.pem"
    target.chmod(0o644)
    target.write_bytes(b"X" * len(contents["ca"]))
    target.chmod(0o444)
    with pytest.raises(files.LinuxExecutionFilesDenied, match="bytes_changed"):
        binding.read("ca")
    assert len(state["closed"]) == 1
    with pytest.raises(files.LinuxExecutionFilesDenied, match="scope_invalid"):
        replace(binding, root=binding.root / "other").read("ca")
    monkeypatch.setattr(files.sys, "platform", "darwin")
    with pytest.raises(files.LinuxExecutionFilesDenied, match="scope_invalid"):
        binding.read("ca")


class Reader:
    def __init__(self, execution_files, alias, reference, *, fresh):
        self.linux_execution_files, self.calls = execution_files, []
        self.reference, self.alias, self.fresh = reference, alias, fresh

    def describe_release_secret(self, reference):
        self.calls.append("metadata")
        assert reference == self.reference
        name, version = reference.split(":")
        return {
            "name": f"projects/{neon.PROJECT_NUMBER}/secrets/{name}/versions/{version}",
            "state": "ENABLED",
            "etag": "synthetic-native-etag",
        }

    def access_release_secret(self, reference):
        self.calls.append("access")
        assert reference == self.reference
        host = neon.FRESH_DIRECT_HOST if self.fresh else neon.RELEASE_DIRECT_HOST
        actors = neon.FRESH_RELEASE_ACTORS if self.fresh else neon.RELEASE_ACTORS
        actor = actors[self.alias][0]
        return f"postgresql://{actor}:synthetic-password@{host}/neondb?sslmode=verify-full&channel_binding=prefer"


def test_source_and_all_fresh_numeric_collectors_preserve_native_binding_and_linux_ca(
    package,
):
    execution, _, _ = package
    source = Reader(execution, "observer", "synthetic-source-observer:9", fresh=False)
    binding, target = neon.collect_source_release_target(
        source,
        source.reference,
        alias="observer",
        database_owner_oid=16387,
        expected_schema="20261008_0009",
    )
    assert binding.purpose == "release_capture" and binding.actor_oid == 1884182
    assert target._url.query["sslrootcert"] == execution.root_certificate
    assert target.dump_environment()["PGSSLROOTCERT"] == execution.root_certificate
    assert source.calls == ["metadata", "access", "metadata"]
    for alias, reference in files.REFERENCES.items():
        reader = Reader(execution, alias, reference, fresh=True)
        fresh_binding, destination = neon.collect_fresh_release_target(
            reader,
            reference,
            alias=alias,
            database_owner_oid=16391,
            expected_schema="20261008_0009",
        )
        assert fresh_binding.purpose == "fresh_release_capture"
        assert (
            fresh_binding.actor,
            fresh_binding.actor_oid,
        ) == neon.FRESH_RELEASE_ACTORS[alias]
        assert fresh_binding.host != binding.host
        assert destination._url.query["sslrootcert"] == execution.root_certificate
        assert destination.dump_environment()["PGSSLMODE"] == "verify-full"
        assert reader.calls == ["metadata", "access", "metadata"]


@pytest.mark.parametrize("alias", list(files.SECRET_NAMES))
def test_rotated_numeric_version_uses_the_existing_native_secret_binding_protocol(
    package, alias
):
    execution, _, _ = package
    reference = files.SECRET_NAMES[alias] + ":2"
    reader = Reader(execution, alias, reference, fresh=True)
    binding, destination = neon.collect_fresh_release_target(
        reader,
        reference,
        alias=alias,
        database_owner_oid=16391,
        expected_schema="20261008_0009",
    )
    assert reader.calls == ["metadata", "access", "metadata"]
    assert neon._issued[binding][1].endswith("/versions/2")
    assert (binding.actor, binding.actor_oid) == neon.FRESH_RELEASE_ACTORS[alias]
    assert destination.dump_environment()["PGHOST"] == neon.FRESH_DIRECT_HOST
    assert destination.dump_environment()["PGSSLROOTCERT"] == execution.root_certificate
    # This controlled reader exercises genuine-source wiring, not actual SDK/SQL
    # authorization. A rotated version is still rejected when its native source
    # reports DISABLED; the historical successful setup cannot override it.
    disabled = Reader(execution, alias, reference, fresh=True)
    metadata = disabled.describe_release_secret
    disabled.describe_release_secret = lambda ref: {
        **metadata(ref),
        "state": "DISABLED",
    }
    with pytest.raises(neon.NeonIdentityDenied):
        neon.collect_fresh_release_target(
            disabled,
            reference,
            alias=alias,
            database_owner_oid=16391,
            expected_schema="20261008_0009",
        )
    assert disabled.calls == ["metadata"]


@pytest.mark.parametrize(
    "alias,reference",
    [
        ("observer", "hirewiz-other-observer-db-20261013:2"),
        ("replacement_runtime", "hirewiz-fresh-observer-db-20261013:2"),
        ("unrecognized_actor", "hirewiz-fresh-observer-db-20261013:2"),
        ("observer", "hirewiz-fresh-observer-db-20261013:latest"),
        ("observer", "hirewiz-fresh-observer-db-20261013:0"),
        ("observer", "hirewiz-fresh-observer-db-20261013:02"),
    ],
)
def test_wrong_name_alias_or_nonnumeric_reference_refuses_before_secret_access(
    package, alias, reference
):
    execution, _, _ = package
    reader = Reader(execution, alias, reference, fresh=True)
    with pytest.raises(neon.NeonIdentityDenied):
        neon.collect_fresh_release_target(
            reader,
            reference,
            alias=alias,
            database_owner_oid=16391,
            expected_schema="20261008_0009",
        )
    assert not reader.calls


def test_untyped_profile_refuses_before_secret_access(package):
    execution, _, _ = package
    reader = Reader(execution, "observer", files.REFERENCES["observer"], fresh=True)
    reader.linux_execution_files = True
    with pytest.raises(neon.NeonIdentityDenied):
        neon.collect_fresh_release_target(
            reader,
            reader.reference,
            alias="observer",
            database_owner_oid=16391,
            expected_schema="20261008_0009",
        )
    assert not reader.calls


def test_historical_setup_cannot_assert_fencing_or_current_authentication(package):
    execution, _, _ = package
    execution.verify_fresh_reference("observer", files.REFERENCES["observer"])
    # File admission issues no fence, source cut, database engine or grant.
    assert not hasattr(execution, "verify_fences") and not hasattr(execution, "engine")
    execution.verify_fresh_reference("observer", "hirewiz-fresh-observer-db-20261013:2")
    with pytest.raises(files.LinuxExecutionFilesDenied, match="duplicate_key"):
        json.loads('{"status":1,"status":2}', object_pairs_hook=files._unique)
