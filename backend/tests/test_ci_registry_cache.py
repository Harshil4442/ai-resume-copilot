"""Owned file/fake-daemon checks; never alter a host daemon or pull an image."""

import hashlib
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
    assert "docker pull --platform linux/amd64 mirror.gcr.io/library/debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587" in cache_step
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
    assert (root / "infra/native-tex/Dockerfile").read_text().splitlines()[2] == "FROM mirror.gcr.io/library/debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587"


def test_reload_observation_timeout_retries_only_read_inside_original_deadline(monkeypatch):
    clock, calls = [0.0], []
    monkeypatch.setattr(cache.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(cache.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    def observe(command, **kwargs):
        calls.append((command, kwargs["timeout"]))
        assert command == ["docker", "info", "--format", "{{json .RegistryConfig.Mirrors}}"]
        if len(calls) == 1:
            clock[0] += kwargs["timeout"]
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return SimpleNamespace(stdout=json.dumps([cache.MIRROR]))

    monkeypatch.setattr(cache.subprocess, "run", observe)
    cache.verify()
    assert [timeout for _, timeout in calls] == [5, 4.75]
    assert clock[0] == 5.25


def test_repeated_observation_timeouts_exhaust_same_ten_second_budget(monkeypatch):
    clock, calls = [0.0], []
    monkeypatch.setattr(cache.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(cache.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    def unavailable(command, **kwargs):
        calls.append((command, kwargs["timeout"]))
        clock[0] += kwargs["timeout"]
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(cache.subprocess, "run", unavailable)
    with pytest.raises(subprocess.TimeoutExpired):
        cache.verify()
    assert [timeout for _, timeout in calls] == [5, 4.75]
    assert clock[0] == 10
    assert all(command == ["docker", "info", "--format", "{{json .RegistryConfig.Mirrors}}"]
               for command, _ in calls)


@pytest.mark.parametrize("raw", ["not-json", "{}", '[123]'])
def test_malformed_native_mirrors_are_not_retried_or_accepted(raw, monkeypatch):
    calls = []

    def observe(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout=raw)

    monkeypatch.setattr(cache.subprocess, "run", observe)
    with pytest.raises((ValueError, json.JSONDecodeError)):
        cache.verify()
    assert len(calls) == 1


@pytest.mark.parametrize("failure,reason", [
    (subprocess.TimeoutExpired("private-command-metadata", 5, stderr="do-not-export"), "observation timed out"),
    (subprocess.CalledProcessError(1, "private-command-metadata", stderr="do-not-export"), "observation command failed"),
])
def test_cli_reports_only_fixed_timeout_or_command_diagnostics(failure, reason, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_OS", "Linux")
    monkeypatch.setattr("sys.argv", ["cache", "--verify"])

    def refused():
        raise failure

    monkeypatch.setattr(cache, "verify", refused)
    with pytest.raises(SystemExit, match=reason) as result:
        cache.main()
    assert "CI remains required" in str(result.value)
    assert "private-command-metadata" not in str(result.value) and "do-not-export" not in str(result.value)


def test_document_policy_routes_only_two_literal_forms_to_the_same_pinned_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path.resolve()))
    monkeypatch.delenv(cache.SOURCE_POLICY_ENV, raising=False)
    monkeypatch.setattr(cache.subprocess, "run", lambda *a, **k: pytest.fail("No Docker command or pull is required."))
    path = cache.document_source_policy()
    raw = path.read_bytes()
    assert path.parent == tmp_path.resolve() and path.stat().st_mode & 0o777 == 0o600
    assert len(raw) <= 4096
    policy = json.loads(raw)
    digest = "sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1"
    origin = "docker-image://public.ecr.aws/docker/library/python"
    assert policy == {"version": 1, "rules": [
        {"action": "CONVERT", "selector": {"identifier": source, "matchType": "EXACT"},
         "updates": {"identifier": "docker-image://mirror.gcr.io/library/python@" + digest}}
        for source in (origin + ":3.12-slim-trixie@" + digest, origin + "@" + digest)
    ]}
    # An unrelated image, different digest, unpinned tag or lookalike repository
    # cannot match an EXACT selector. No mutable-tag or wildcard rule exists.
    selectors = {rule["selector"]["identifier"] for rule in policy["rules"]}
    assert not selectors.intersection({
        origin + ":3.12-slim-trixie", origin + "@sha256:" + "0" * 64,
        origin + "-other@" + digest, origin.replace("public.ecr.aws", "other.example") + "@" + digest,
        "docker-image://docker.io/library/debian:bookworm-slim",
    })
    root = Path(__file__).resolve().parents[2]
    dockerfile = root / "infra/document-processing/Dockerfile"
    assert ("FROM " + origin.removeprefix("docker-image://") + ":3.12-slim-trixie@" + digest
            + " AS dependencies") in dockerfile.read_text().splitlines()
    source_policy = json.loads((root / "infra/document-processing/policy.json").read_bytes())
    assert hashlib.sha256(dockerfile.read_bytes()).hexdigest() == source_policy["source_sha256"][
        "infra/document-processing/Dockerfile"]


@pytest.mark.parametrize("inherited", ["/tmp/foreign-policy.json", "{}", "https://other.example/policy", "\n"])
def test_inherited_source_policy_refuses_before_creating_or_reading_files(tmp_path, monkeypatch, inherited):
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path.resolve()))
    monkeypatch.setenv(cache.SOURCE_POLICY_ENV, inherited)
    with pytest.raises(ValueError, match="inherited"):
        cache.document_source_policy()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kind", ["missing", "relative", "symlink", "file", "control"])
def test_document_policy_refuses_unowned_or_noncanonical_temp_destination(tmp_path, monkeypatch, kind):
    runner = tmp_path / "runner"
    runner.mkdir()
    link = tmp_path / "link"
    link.symlink_to(runner, target_is_directory=True)
    regular = tmp_path / "regular"
    regular.write_text("preserve")
    choices = {"missing": "", "relative": "runner", "symlink": str(link),
               "file": str(regular), "control": str(runner) + "\n"}
    monkeypatch.setenv("RUNNER_TEMP", choices[kind])
    monkeypatch.delenv(cache.SOURCE_POLICY_ENV, raising=False)
    with pytest.raises(ValueError, match="temporary directory"):
        cache.document_source_policy()
    assert list(runner.iterdir()) == [] and regular.read_text() == "preserve"


def test_failed_policy_write_removes_only_its_owned_new_file(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path.resolve()))
    monkeypatch.delenv(cache.SOURCE_POLICY_ENV, raising=False)
    retained = tmp_path / "retained.json"
    retained.write_text("preserve")

    def fail_sync(_):
        raise OSError("private failure must not become a usable partial policy")

    monkeypatch.setattr(cache.os, "fsync", fail_sync)
    with pytest.raises(OSError):
        cache.document_source_policy()
    assert list(tmp_path.iterdir()) == [retained] and retained.read_text() == "preserve"


def test_document_build_scopes_and_cleans_its_generated_policy_without_skipping_native_checks():
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/ci.yml").read_text()
    step = workflow.split("      - name: Build and verify genuine native document engines\n", 1)[1].split(
        "      - name:", 1)[0]
    assert 'EXPERIMENTAL_BUILDKIT_SOURCE_POLICY: ""' in step.split("        run: |", 1)[0]
    create = 'document_source_policy_path="$(python3 backend/scripts/ci_registry_cache.py --document-source-policy)"'
    export = 'export EXPERIMENTAL_BUILDKIT_SOURCE_POLICY="$document_source_policy_path"'
    check = "backend/.venv/bin/python backend/scripts/document_test_image.py"
    assert step.index(create) < step.index(export) < step.index(check)
    assert 'trap \'rm -f -- "$document_source_policy_path"\' EXIT' in step
    assert '--platform linux/amd64 --source-revision --output "$document_test_report_path"' in step
    assert "--image-id" not in step and "|| true" not in step and "continue-on-error" not in step
    assert workflow.count("EXPERIMENTAL_BUILDKIT_SOURCE_POLICY:") == 1
