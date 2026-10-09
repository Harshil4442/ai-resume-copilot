"""Owned file/fake-daemon checks; never alter a host daemon or pull an image."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import ci_registry_cache as cache


def test_merge_preserves_daemon_options_and_promotes_only_official_cache(tmp_path):
    path = tmp_path / "daemon.json"
    original = {"features": {"containerd-snapshotter": True}, "live-restore": True,
                "registry-mirrors": ["https://existing.example", cache.MIRROR + "/"],
                "log-driver": "journald"}
    path.write_text(json.dumps(original))
    cache.configure(path)
    result = json.loads(path.read_text())
    assert result["registry-mirrors"] == [cache.MIRROR, "https://existing.example"]
    assert result["features"] == original["features"]
    assert result["live-restore"] is True and result["log-driver"] == "journald"
    assert path.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.iterdir()) == [path]


def test_absent_file_and_repeated_configuration_are_supported(tmp_path):
    path = tmp_path / "daemon.json"
    cache.configure(path)
    first = path.read_bytes()
    cache.configure(path)
    assert path.read_bytes() == first
    assert json.loads(first) == {"registry-mirrors": [cache.MIRROR]}


@pytest.mark.parametrize("raw", [b"invalid", b"[]", b'{"debug":true,"debug":false}',
                                  b'{"registry-mirrors":null}', b'{"registry-mirrors":[123]}',
                                  b"x" * (128 * 1024 + 1)])
def test_invalid_existing_configuration_refuses_without_replacing_it(raw, tmp_path):
    path = tmp_path / "daemon.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        cache.configure(path)
    assert path.read_bytes() == raw
    assert list(tmp_path.iterdir()) == [path]


def test_symlink_refuses_without_modifying_target(tmp_path):
    target = tmp_path / "other.json"
    target.write_text("{}")
    link = tmp_path / "daemon.json"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="regular file"):
        cache.configure(link)
    assert target.read_text() == "{}"


def test_native_mirror_verification_waits_for_reload_without_restart(monkeypatch):
    answers = iter(["[]", json.dumps([cache.MIRROR + "/"])])
    calls = []

    def native(command, **kwargs):
        calls.append(command)
        assert kwargs["check"] is True and kwargs["timeout"] == 5
        return SimpleNamespace(stdout=next(answers))

    monkeypatch.setattr(cache.subprocess, "run", native)
    monkeypatch.setattr(cache.time, "sleep", lambda _: None)
    cache.verify()
    assert calls == [["docker", "info", "--format", "{{json .RegistryConfig.Mirrors}}"]] * 2


def test_missing_native_activation_is_a_bounded_failure(monkeypatch):
    clocks = iter([0, 11])
    monkeypatch.setattr(cache.time, "monotonic", lambda: next(clocks))
    monkeypatch.setattr(cache.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="[]"))
    with pytest.raises(ValueError, match="did not activate"):
        cache.verify()


def test_native_daemon_error_is_not_masked_or_restarted(monkeypatch):
    def unavailable(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "docker")

    monkeypatch.setattr(cache.subprocess, "run", unavailable)
    with pytest.raises(subprocess.CalledProcessError):
        cache.verify()


def test_main_is_not_a_general_host_configuration_tool(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr("sys.argv", ["cache"])
    with pytest.raises(SystemExit, match="ephemeral Linux"):
        cache.main()


def test_workflow_preserves_required_exact_source_compiler_and_security_contracts():
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/ci.yml").read_text()
    cache_step = workflow.split("      - name: Configure verified public cache", 1)[1].split("      - name:", 1)[0]
    assert "sudo dockerd --validate --config-file /etc/docker/daemon.json" in cache_step
    assert "sudo kill -HUP" in cache_step
    assert "restart" not in cache_step.split("        run: |", 1)[1]
    assert '"$postgres_before" = "$postgres_after"' in cache_step
    assert '"true healthy"' in cache_step and "job.services.postgres.id" in cache_step
    assert "|| true" not in cache_step and "continue-on-error" not in cache_step
    assert "docker pull --platform linux/amd64 public.ecr.aws/docker/library/debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587" in cache_step
    security = workflow.split("  security-and-container:\n", 1)[1].split("\n  browser-companion:", 1)[0]
    security_cache = security.split("      - name: Configure verified public cache", 1)[1].split("      - name:", 1)[0]
    for command in (
        "sudo --preserve-env=GITHUB_ACTIONS,RUNNER_OS python3 backend/scripts/ci_registry_cache.py",
        "sudo dockerd --validate --config-file /etc/docker/daemon.json",
        'daemon_pid="$(systemctl show --property=MainPID --value docker)"',
        '[[ "$daemon_pid" =~ ^[1-9][0-9]*$ ]]',
        '[ "$daemon_pid" -gt 1 ]',
        'sudo kill -HUP "$daemon_pid"',
        "timeout 15s python3 backend/scripts/ci_registry_cache.py --verify",
    ):
        assert command in cache_step and command in security_cache
    assert "restart" not in security_cache
    assert "|| true" not in security_cache and "continue-on-error" not in security
    assert security.index("Configure verified public cache") < security.index("Scan repository for committed secrets")
    assert security.index("Scan repository for committed secrets") < security.index("Build production container")
    assert "ghcr.io/gitleaks/gitleaks:v8.24.2@sha256:b5918eb91b8d2473cec722f066abb4352e4ffdc4ec9f4283ec143aba9ec9ebc4\n          detect --source /repo --no-banner --redact" in workflow
    assert "fetch-depth: 0" in workflow
    assert "ref: ${{ github.event.pull_request.head.sha || github.sha }}" in workflow
    assert "scripts/native_tex_test_image.py" in workflow
    assert "--platform linux/amd64 --github-env" in workflow
    assert ".venv/bin/pytest -q" in workflow
    assert (root / "infra/native-tex/Dockerfile").read_text().splitlines()[2] == "FROM public.ecr.aws/docker/library/debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587"
