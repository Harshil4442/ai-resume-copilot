"""Fail-closed CI image identity, source binding and clean build context guards."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import subprocess
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from backend.app.services import native_tex
from backend.scripts import native_tex_test_image as contract
from pypdf import PdfReader

IMAGE = "sha256:" + "1" * 64
HASHES = {name: hashlib.sha256(name.encode()).hexdigest() for name in contract.SOURCES}


def inspect_bytes(*, image=IMAGE, hashes=HASHES, architecture="amd64"):
    return json.dumps(
        [
            {
                "Id": image,
                "Os": "linux",
                "Architecture": architecture,
                "Config": {
                    "Labels": {
                        contract.LABEL_PREFIX + name: digest for name, digest in hashes.items()
                    }
                },
            }
        ]
    ).encode()


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "tex:latest",
        "sha256:" + "A" * 64,
        "sha256:" + "1" * 63,
        IMAGE + "\n",
        " " + IMAGE,
        "repo@" + IMAGE,
    ],
)
def test_required_image_absent_or_malformed_never_executes_or_falls_back(value, monkeypatch):
    monkeypatch.delenv(contract.TEST_IMAGE_ENV, raising=False)
    monkeypatch.setenv("NATIVE_TEX_TEST_IMAGE", IMAGE)
    if value is not None:
        monkeypatch.setenv(contract.TEST_IMAGE_ENV, value)
    monkeypatch.setattr(
        contract.subprocess,
        "run",
        lambda *a, **k: pytest.fail("invalid ID must fail before Docker"),
    )
    with pytest.raises(contract.NativeTexTestImageError, match="must run, not skip"):
        contract.required_test_image()


@pytest.mark.parametrize(
    "error", [FileNotFoundError("Docker unavailable"), subprocess.TimeoutExpired("docker", 30)]
)
def test_required_docker_absence_or_timeout_is_bounded_failure(error, monkeypatch):
    def unavailable(*args, **kwargs):
        assert kwargs["timeout"] == 30
        raise error

    monkeypatch.setattr(contract.subprocess, "run", unavailable)
    with pytest.raises(contract.NativeTexTestImageError, match="unavailable or timed out"):
        contract.verify_image(IMAGE, HASHES)


@pytest.mark.parametrize(
    "metadata",
    [
        b"null",
        b"[]",
        b"{}",
        b"not json",
        inspect_bytes(image="sha256:" + "2" * 64),
        inspect_bytes(hashes={}),
        inspect_bytes(architecture="arm64"),
    ],
)
def test_inspect_identity_source_or_platform_change_fails_before_container(metadata, monkeypatch):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout=metadata, stderr=b"")

    monkeypatch.setattr(contract.subprocess, "run", run)
    with pytest.raises(contract.NativeTexTestImageError, match="attestation changed"):
        contract.verify_image(IMAGE, HASHES, "linux/amd64")
    assert commands == [["docker", "image", "inspect", IMAGE]]


@pytest.mark.parametrize(
    "worker", [None, [], {"processor.py": "0" * 64, "service.py": HASHES["service.py"]}]
)
def test_changed_actual_worker_refuses_and_cleans_only_owned_container(worker, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        payload = (
            inspect_bytes() if command[1:3] == ["image", "inspect"] else json.dumps(worker).encode()
        )
        return SimpleNamespace(returncode=0, stdout=payload, stderr=b"")

    monkeypatch.setattr(contract.subprocess, "run", run)
    with pytest.raises(contract.NativeTexTestImageError, match="worker bytes differ"):
        contract.verify_image(IMAGE, HASHES)
    probe = calls[1]
    owned = probe[probe.index("--name") + 1]
    assert owned.startswith("hirewiz-tex-attest-")
    assert "--network=none" in probe and "--read-only" in probe
    assert "--pull=never" in probe and "--volume" not in probe
    assert probe[probe.index("--entrypoint=python3") + 1] == IMAGE
    assert calls[-1] == ["docker", "rm", "--force", owned]


def test_runtime_probe_timeout_still_cleans_its_owned_container(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[1] == "run":
            raise subprocess.TimeoutExpired(command, 30)
        return SimpleNamespace(returncode=0, stdout=inspect_bytes(), stderr=b"")

    monkeypatch.setattr(contract.subprocess, "run", run)
    with pytest.raises(contract.NativeTexTestImageError, match="timed out"):
        contract.verify_image(IMAGE, HASHES)
    owned = calls[1][calls[1].index("--name") + 1]
    assert calls[-1] == ["docker", "rm", "--force", owned]


def test_cleanup_failure_cannot_publish_image_attestation(monkeypatch):
    workers = {name: HASHES[name] for name in ("processor.py", "service.py")}

    def run(command, **kwargs):
        if command[1] == "rm":
            return SimpleNamespace(returncode=1, stdout=b"", stderr=b"daemon refused cleanup")
        payload = inspect_bytes() if command[1] == "image" else json.dumps(workers).encode()
        return SimpleNamespace(returncode=0, stdout=payload, stderr=b"")

    monkeypatch.setattr(contract.subprocess, "run", run)
    with pytest.raises(contract.NativeTexTestImageError, match="cleanup failed"):
        contract.verify_image(IMAGE, HASHES)


def test_source_change_invalidates_cached_image_verification(monkeypatch):
    contract._verified.cache_clear()
    monkeypatch.setenv(contract.TEST_IMAGE_ENV, IMAGE)
    observed = []
    current = dict(HASHES)
    monkeypatch.setattr(contract, "source_hashes", lambda: dict(current))

    def verify(image, hashes, platform=None):
        observed.append(hashes)
        if hashes != HASHES:
            raise contract.NativeTexTestImageError("source changed")
        return {}

    monkeypatch.setattr(contract, "verify_image", verify)
    try:
        assert contract.required_test_image() == IMAGE
        assert contract.required_test_image() == IMAGE
        current["processor.py"] = "0" * 64
        with pytest.raises(contract.NativeTexTestImageError, match="source changed"):
            contract.required_test_image()
        assert len(observed) == 2
    finally:
        contract._verified.cache_clear()


def test_builder_copies_only_three_sources_and_checks_platform_before_export(tmp_path, monkeypatch):
    root = tmp_path / "source"
    native = root / "infra/native-tex"
    native.mkdir(parents=True)
    for name in contract.SOURCES:
        (native / name).write_bytes(name.encode())
    (native / "unrelated-secret.txt").write_text("inert exclusion sentinel")
    monkeypatch.setattr(contract, "ROOT", root)
    monkeypatch.setattr(contract, "source_hashes", lambda: dict(HASHES))
    commands = []

    def build(command, **kwargs):
        commands.append(command)
        context = Path(command[-1])
        assert sorted(p.name for p in context.iterdir()) == sorted(contract.SOURCES)
        assert {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in context.iterdir()
        } == HASHES
        assert command[command.index("--platform") + 1] == "linux/amd64"
        assert kwargs["timeout"] == 600 and kwargs["check"] is True
        Path(command[command.index("--iidfile") + 1]).write_text(IMAGE + "\n")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(contract.subprocess, "run", build)
    verified = []
    monkeypatch.setattr(
        contract,
        "verify_image",
        lambda image, hashes, platform: (
            verified.append((image, hashes, platform)) or {"image_id": image}
        ),
    )
    assert contract.build_image("linux/amd64") == {"image_id": IMAGE}
    assert verified == [(IMAGE, HASHES, "linux/amd64")]
    assert "--pull=false" in commands[0] and "--tag" not in commands[0]


def test_actual_verified_image_is_required_and_bound_to_checked_in_worker(monkeypatch):
    # This positive case is intentionally real: no subprocess or image mocks.
    image = contract.required_test_image()
    report = contract.verify_image(image, contract.source_hashes())
    assert report["image_id"] == image
    assert report["worker_sha256"] == {
        name: report["source_sha256"][name] for name in ("processor.py", "service.py")
    }


def test_strict_umask_build_context_files_are_explicitly_readable_without_changing_host_policy(
    monkeypatch,
):
    observed = []

    def build(command, **kwargs):
        context = Path(command[-1])
        observed.append({p.name: stat.S_IMODE(p.stat().st_mode) for p in context.iterdir()})
        assert observed[-1] == {name: 0o644 for name in contract.SOURCES}
        Path(command[command.index("--iidfile") + 1]).write_text(IMAGE + "\n")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(contract.subprocess, "run", build)
    monkeypatch.setattr(
        contract, "verify_image", lambda image, hashes, platform: {"image_id": image}
    )
    previous = os.umask(0o077)
    try:
        assert contract.build_image("linux/amd64")["image_id"] == IMAGE
        current = os.umask(0o077)
        assert current == 0o077
    finally:
        os.umask(previous)
    assert len(observed) == 1


def test_actual_strict_umask_build_attests_and_nonroot_compiler_preserves_source(
    tmp_path, monkeypatch
):
    # Real Docker source build, verification and compiler: no subprocess mocks.
    platform = contract.verify_image(contract.required_test_image(), contract.source_hashes())[
        "platform"
    ]
    previous = os.umask(0o077)
    try:
        report = contract.build_image(platform)
        current = os.umask(0o077)
        assert current == 0o077
    finally:
        os.umask(previous)
    (tmp_path / "strict-umask-image-attestation.json").write_text(json.dumps(report, indent=2))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("NATIVE_TEX_IMAGE", report["image_id"])
    monkeypatch.delenv("NATIVE_TEX_COMPILER_URL", raising=False)
    source = (
        r"\documentclass{article}\usepackage[T1]{fontenc}\usepackage{lmodern}"
        r"\pagestyle{empty}\begin{document}" + "\nBuilt Python reports.\n" + r"\end{document}"
    ).encode()
    seal = native_tex.prepare_artifact(source, "tex", [])
    pdf = base64.b64decode(seal["pdf_base64"])
    assert native_tex.sealed_bytes(seal, source, "tex", [], "tex") == source
    assert seal["classification"] == "unchanged_source_snapshot"
    assert "Built Python reports." in PdfReader(BytesIO(pdf)).pages[0].extract_text()
    assert report["worker_sha256"] == {
        name: report["source_sha256"][name] for name in ("processor.py", "service.py")
    }
    (tmp_path / "strict-umask-compiled.pdf").write_bytes(pdf)
