"""Build-contract checks with a fake builder/FreshClam, not native scan proof."""

import importlib.util
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
NONCE = "DOCUMENT_DEFINITIONS_NONCE"
IMAGE = "sha256:" + "a" * 64


@pytest.fixture
def checker():
    spec = importlib.util.spec_from_file_location(
        "definition_cache_checker", ROOT / "backend/scripts/document_test_image.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def instructions():
    """Join the bounded instruction syntax used by this checked-in Dockerfile."""
    rows = []
    pending = ""
    for line in (ROOT / "infra/document-processing/Dockerfile").read_text().splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        pending += value.removesuffix("\\").strip() + " "
        if not value.endswith("\\"):
            command, argument = pending.strip().split(" ", 1)
            rows.append((command.upper(), argument))
            pending = ""
    assert not pending
    return rows


def definition_command():
    return next(argument for command, argument in instructions()
                if command == "RUN" and "freshclam --" in argument)


def test_nonce_is_consumed_only_in_separate_definition_acquisition_stage():
    stages = []
    for command, argument in instructions():
        if command == "FROM":
            stages.append([(command, argument)])
        else:
            stages[-1].append((command, argument))
    assert len(stages) == 3
    dependencies, definitions, runtime = stages
    assert dependencies[0] == ("FROM", "public.ecr.aws/docker/library/python:3.12-slim-bookworm@"
                               "sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258"
                               " AS dependencies")
    assert definitions[0] == ("FROM", "dependencies AS definitions")
    assert runtime[0] == ("FROM", "dependencies AS runtime")
    assert all("freshclam --" not in argument and NONCE not in argument
               for _, argument in dependencies)
    assert definitions[1] == ("ARG", NONCE)
    assert len(definitions) == 3 and definitions[2][0] == "RUN"
    assert '"$' + NONCE + '"' in definitions[2][1]
    assert "freshclam --stdout --datadir=/var/lib/clamav" in definitions[2][1]
    assert ("COPY", "--from=definitions /var/lib/clamav /var/lib/clamav") in runtime
    assert all(command not in {"ARG", "ENV", "RUN"} for command, _ in runtime)
    assert ("USER", "65534:65534") in runtime
    assert ("WORKDIR", "/opt/document") in runtime
    assert ("ENTRYPOINT", '["/usr/local/bin/python3", "-I", "/opt/document/service.py"]') in runtime


def test_two_builds_pass_distinct_builder_owned_nonces_and_exact_secretless_context(checker, monkeypatch):
    hashes = checker.source_hashes()
    owned_nonces = iter(["1" * 32, "2" * 32])
    monkeypatch.setattr(checker.uuid, "uuid4", lambda: SimpleNamespace(hex=next(owned_nonces)))
    monkeypatch.setenv(NONCE, "caller-value-must-not-route-or-be-exported")
    captured = []

    def fake_build(args, *, timeout=20):
        assert timeout == 600
        assert args[:4] == ["docker", "--host=unix:///fixed.sock", "build", "--pull=false"]
        argument = args[args.index("--build-arg") + 1]
        assert re.fullmatch(NONCE + "=[0-9a-f]{32}", argument)
        captured.append(argument)
        context = Path(args[-1])
        files = {str(path.relative_to(context)) for path in context.rglob("*") if path.is_file()}
        assert files == set(checker.SOURCES)
        assert all((context / path).read_bytes() == (ROOT / path).read_bytes() for path in files)
        assert args[args.index("--platform") + 1] == "linux/amd64"
        labels = [args[index + 1] for index, value in enumerate(args) if value == "--label"]
        assert set(labels) == {checker.LABEL + path + "=" + sha for path, sha in hashes.items()}
        assert not {"--secret", "--ssh", "--network", "--env"}.intersection(args)
        assert not any("caller-value" in value for value in args)
        Path(args[args.index("--iidfile") + 1]).write_text(IMAGE)
        return b""

    monkeypatch.setattr(checker, "successful", fake_build)
    assert checker.build(["docker", "--host=unix:///fixed.sock"], "linux/amd64", hashes) == IMAGE
    assert checker.build(["docker", "--host=unix:///fixed.sock"], "linux/amd64", hashes) == IMAGE
    assert captured == [NONCE + "=" + "1" * 32, NONCE + "=" + "2" * 32]


@pytest.mark.parametrize("nonce", [None, "", "a" * 31, "x" * 32, "a" * 33, "a" * 31 + " "])
def test_definition_step_refuses_missing_or_invalid_nonce_before_fake_acquisition(tmp_path, nonce):
    marker = tmp_path / "acquired"
    fake = tmp_path / "freshclam"
    fake.write_text('#!/bin/sh\n: > "' + str(marker) + '"\n')
    fake.chmod(0o755)
    env = {"PATH": str(tmp_path)}
    if nonce is not None:
        env[NONCE] = nonce
    result = subprocess.run(["/bin/sh", "-c", definition_command()], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
    assert result.returncode != 0
    assert not marker.exists()


def test_valid_nonce_executes_exact_fake_definition_acquisition(tmp_path):
    marker = tmp_path / "acquired"
    fake = tmp_path / "freshclam"
    fake.write_text('#!/bin/sh\n[ "$#" -eq 2 ] && [ "$1" = --stdout ] '
                    '&& [ "$2" = --datadir=/var/lib/clamav ] || exit 1\n'
                    ': > "' + str(marker) + '"\n')
    fake.chmod(0o755)
    result = subprocess.run(["/bin/sh", "-c", definition_command()],
                            env={"PATH": str(tmp_path), NONCE: "a" * 32},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
    assert result.returncode == 0 and marker.exists()


@pytest.mark.parametrize("field", ["DOCKER_HOST", "DOCKER_CONTEXT"])
def test_caller_daemon_routing_is_still_refused_before_any_command(checker, monkeypatch, field):
    monkeypatch.setenv(field, "untrusted-routing")
    monkeypatch.setattr(checker, "successful", lambda *a, **k: pytest.fail("unexpected command"))
    with pytest.raises(checker.DocumentImageError):
        checker.docker_command()


def test_source_hash_mismatch_stops_before_build_or_nonce_acquisition(checker, monkeypatch):
    hashes = checker.source_hashes()
    hashes[checker.SOURCES[0]] = "b" * 64
    monkeypatch.setattr(checker.uuid, "uuid4", lambda: pytest.fail("nonce before integrity check"))
    monkeypatch.setattr(checker, "successful", lambda *a, **k: pytest.fail("unexpected command"))
    with pytest.raises(checker.DocumentImageError):
        checker.build(["docker", "--host=unix:///fixed.sock"], "linux/amd64", hashes)
