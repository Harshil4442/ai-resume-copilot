"""Bind the forward source profile independently of retained Cloud Run proofs."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from infra.gcp import document_source_candidate as v4
from infra.gcp import document_source_candidate_v5 as v5

from scripts import document_test_image as image

ROOT = Path(__file__).resolve().parents[2]
V4_RETAINED = "ade1d75633df5e5bb52ac222ed7252dce7d84d57"


def test_current_v5_source_snapshot_keeps_other_runtime_and_policy_controls():
    contents, hashes, digest = v5.working_snapshot(ROOT)
    assert set(contents) == {*v4.SOURCES, v5.POLICY_PATH}
    assert digest == v5.POLICY_SHA256 and hashes[v5.DOCKERFILE] == "7efb244bca8242c9dcc6d1c64aa4d5b35ba88eb9ac253fcaebb33c2134e7959a"
    assert all(v4.digest(contents[name]) == expected for name, expected in hashes.items())
    old = v4.committed(V4_RETAINED, root=ROOT)
    assert old.kind == v4.KIND and old.policy == v4.POLICY_SHA256
    assert {name for name in hashes if old.sources[name] != hashes[name]} == {
        "infra/document-processing/processor.py", "infra/document-processing/service.py"
    }
    controls = json.loads(contents[v5.POLICY_PATH])["required_native_deployment_controls"]
    assert controls["sandbox_egress"] is False and controls["launcher_phase_seconds"] == {"delete": 5, "do": 55}
    assert controls["owned_process_reap_timeout_seconds"] == 2
    assert controls["owned_synchronous_sandbox_teardown"]["strict_force_delete_exit_for_unknown_launch"] == 0
    assert controls["request_only_private_tmp_bind"]["erase_after_confirmed_native_teardown"] is True


def test_valid_digest_alone_cannot_accept_weaker_sandbox_policy(monkeypatch):
    contents, _, _ = v5.working_snapshot(ROOT)
    value = json.loads(contents[v5.POLICY_PATH])
    value["required_native_deployment_controls"]["sandbox_egress"] = True
    raw = v4.canonical(value) + b"\n"
    monkeypatch.setattr(v5, "POLICY_SHA256", v4.digest(raw))
    with pytest.raises(v4.SourceDenied, match="synchronous_policy_semantics_changed"):
        v5.validate_policy(raw, ROOT)


def test_forward_source_binds_one_committed_tree_and_rejects_modified_contract(tmp_path):
    contents, hashes, _ = v5.working_snapshot(ROOT)
    for name in {*v5.CONTRACT_PATHS, v4.POLICY_PATH, v4.courier.POLICY_PATH}:
        contents[name] = (ROOT / name).read_bytes()
    for name, raw in contents.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    commands = [
        ["init", "-q"],
        ["add", "--all"],
        ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
         "-c", "core.hooksPath=/dev/null", "commit", "-qm", "Exact local synthetic source archive"],
    ]
    for arguments in commands:
        subprocess.run(["git", *arguments], cwd=tmp_path, check=True, capture_output=True, timeout=10)
    source = v5.committed(root=tmp_path, clean=True)
    assert source.identity == {
        "kind": v5.KIND, "source_commit": source.commit, "policy_sha256": v5.POLICY_SHA256,
        "qualification_sha256": source.qualification_sha256, "source_sha256": hashes,
    }
    assert source.runtime["/opt/document/processor.py"] == hashes["infra/document-processing/processor.py"]
    assert source.legacy is False and source.dockerfile == v4.DOCKERFILE and source.policy_path == v5.POLICY_PATH
    assert v4.committed(V4_RETAINED, root=ROOT).policy == v4.POLICY_SHA256

    changed = tmp_path / "infra/gcp/document_source_payload_v5.py"
    changed.write_bytes(changed.read_bytes() + b"\n# changed proof code\n")
    with pytest.raises(v4.SourceDenied, match="synchronous_working_contract_changed"):
        v5.require_working_contract(source, tmp_path)
    with pytest.raises(v4.SourceDenied, match="synchronous_committed_clean_source_required"):
        v5.committed(root=tmp_path, clean=True)
    assert v5.committed(source.commit, root=tmp_path).identity == source.identity

    changed = tmp_path / "infra/document-processing/service.py"
    changed.write_bytes(changed.read_bytes() + b"\n# changed runtime\n")
    with pytest.raises(v4.SourceDenied, match="synchronous_working_source_changed"):
        v5.working_snapshot(tmp_path)


def test_native_local_proof_requires_explicit_source_v5_profile(monkeypatch):
    def unexpected_docker():
        pytest.fail("invalid profile reached Docker")
    monkeypatch.setattr(image, "docker_command", unexpected_docker)
    with pytest.raises(image.DocumentImageError):
        image.check("linux/amd64", source_v5=True)


def test_native_proof_cli_requires_both_explicit_source_flags():
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(ROOT / "backend/scripts/document_test_image.py"),
         "--platform", "linux/amd64", "--source-v5"],
        cwd=ROOT, check=False, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 2 and "--source-v5 requires --source-revision" in result.stderr
