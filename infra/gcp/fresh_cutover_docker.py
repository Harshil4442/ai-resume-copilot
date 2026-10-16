"""Fixed local Docker owner and opaque local Neon barrier for final review.

No Docker, SDK or SQL calls at import. Credentials flow only through owned RAM
pipes. All writes require an explicit execute invocation after human approval.
The named data volume and durable host attempt remain after uncertain outcomes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

sys.path[:0] = [
    str(Path(__file__).resolve().parent),
    str(Path(__file__).resolve().parents[2] / "backend"),
]
import fresh_cutover_execute as safe_events  # noqa: E402 -- pure protocol definitions
import fresh_cutover_writes as writes  # noqa: E402 -- standalone ordinary imports
import local_neon_source_delete as local  # noqa: E402 -- standalone ordinary imports
from sqlalchemy.engine import make_url  # noqa: E402

DOCKER = Path("/Applications/Docker.app/Contents/Resources/bin/docker")
DOCKER_SHA256 = "e3d9f77f123981d274c1d8d37376ac857b84997a77d40d7d2ec25d99df8b5743"
SDK = Path("/opt/homebrew/share/google-cloud-sdk/lib")
CA = Path("/opt/homebrew/etc/ca-certificates/cert.pem")
CA_SHA256 = "426806ef8b5e5711c484e53522ca10e2f103c0d514a6699bdf615d45134477a1"
GCP_MANAGER = Path("/Users/harshil/.config/gcloud")
GH_MANAGER = Path("/Users/harshil/.config/gh")
HOST_AUTH = Path(__file__).with_name("fresh_cutover_host_auth.py")
LABEL = "com.hirewiz.reviewed-cutover"


def need(value: Any, code: str) -> None:
    if not value:
        raise writes.WriteDenied(code)


def environment() -> dict[str, str]:
    return {
        "PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": "/Users/harshil",
        "LANG": "C",
        "LC_ALL": "C",
        "CLOUDSDK_CONFIG": str(GCP_MANAGER),
        "CLOUDSDK_CORE_ACCOUNT": "harshilyogeshbhai886@gmail.com",
        "CLOUDSDK_CORE_PROJECT": writes.PROJECT,
        "CLOUDSDK_CORE_UNIVERSE_DOMAIN": "googleapis.com",
        "CLOUDSDK_CORE_DISABLE_PROMPTS": "true",
        "CLOUDSDK_CORE_DISABLE_FILE_LOGGING": "true",
        "CLOUDSDK_CORE_LOG_HTTP": "false",
        "CLOUDSDK_AUTH_DISABLE_SSL_VALIDATION": "false",
        "CLOUDSDK_AUTH_DISABLE_CREDENTIALS": "false",
    }


def public_pin(path: Path, digest: str, *, cap: int = 1048576) -> None:
    info = path.lstat()
    need(
        stat.S_ISREG(info.st_mode)
        and info.st_uid == 501
        and not info.st_mode & 0o022
        and 0 < info.st_size <= cap
        and hashlib.sha256(path.read_bytes()).hexdigest() == digest,
        "docker_public_runtime_pin_refused",
    )


def command(args: list[str], deadline: float, cap: int = 65536) -> bytes:
    need(time.monotonic() < deadline, "docker_operation_deadline")
    try:
        result = subprocess.run(
            [str(DOCKER), *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=max(0.001, min(30, deadline - time.monotonic())),
            check=False,
            env=environment(),
        )
        need(
            result.returncode == 0 and len(result.stdout) <= cap,
            "docker_command_unknown_reconcile_only",
        )
        return result.stdout
    except writes.WriteDenied:
        raise
    except Exception:
        raise writes.WriteDenied("docker_command_unknown_reconcile_only") from None


def bootstrap_password(deadline: float) -> str:
    """Normal retained Neon URI goes to a private pipe, never terminal output."""
    helper = Path(local.__file__)
    public_pin(writes.LOCAL_PYTHON, writes.LOCAL_PYTHON_SHA256)
    public_pin(helper, writes.LOCAL_HELPER_SHA256)
    read, write = os.pipe()
    process = None
    try:
        process = subprocess.Popen(
            [
                str(writes.LOCAL_PYTHON),
                "-I",
                "-B",
                str(helper),
                "--bootstrap-credential-fd",
                str(write),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=(write,),
            env=environment(),
        )
        os.close(write)
        write = -1
        result = bytearray()
        with selectors.DefaultSelector() as selected:
            selected.register(read, selectors.EVENT_READ)
            while selected.get_map():
                need(time.monotonic() < deadline, "docker_bootstrap_deadline")
                for key, _ in selected.select(min(0.1, max(0, deadline - time.monotonic()))):
                    chunk = os.read(key.fd, 16385 - len(result))
                    if not chunk:
                        selected.unregister(key.fd)
                        continue
                    result.extend(chunk)
                    need(len(result) <= 16384, "docker_bootstrap_pipe_bound")
        need(
            process.wait(timeout=max(0.001, deadline - time.monotonic())) == 0,
            "docker_bootstrap_native_get_refused",
        )
        value = json.loads(result, object_pairs_hook=writes.native.object_pairs)
        need(
            type(value) is dict
            and set(value) == {"password"}
            and type(value["password"]) is str
            and 1 <= len(value["password"].encode()) <= 8192
            and "\x00" not in value["password"],
            "docker_bootstrap_pipe_shape",
        )
        return value["password"]
    finally:
        if write >= 0:
            os.close(write)
        os.close(read)
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=2)


def ephemeral_host_authentication(deadline: float) -> dict[str, Any]:
    """Normal SDK/keychain clients deliver only short-lived RAM credentials."""
    public_pin(writes.LOCAL_PYTHON, writes.LOCAL_PYTHON_SHA256)
    metadata = HOST_AUTH.lstat()
    need(stat.S_ISREG(metadata.st_mode) and metadata.st_uid == 501
         and not metadata.st_mode & 0o022, "docker_auth_helper_metadata_refused")
    read, write = os.pipe()
    process = None
    try:
        process = subprocess.Popen(
            [str(writes.LOCAL_PYTHON), "-I", "-B", str(HOST_AUTH),
             "--credential-fd", str(write)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            pass_fds=(write,), env=environment(),
        )
        os.close(write)
        write = -1
        result = bytearray()
        with selectors.DefaultSelector() as selected:
            selected.register(read, selectors.EVENT_READ)
            while selected.get_map():
                need(time.monotonic() < deadline, "docker_auth_native_deadline")
                for key, _ in selected.select(min(0.1, max(0, deadline - time.monotonic()))):
                    chunk = os.read(key.fd, 16385 - len(result))
                    if not chunk:
                        selected.unregister(key.fd)
                        continue
                    result.extend(chunk)
                    need(len(result) <= 16384, "docker_auth_pipe_bound")
        need(process.wait(timeout=max(0.001, deadline - time.monotonic())) == 0,
             "docker_auth_native_unavailable")
        value = json.loads(result, object_pairs_hook=writes.native.object_pairs)
        result.clear()
        need(type(value) is dict and set(value) == {"google_access_token", "google_expires_at"}
             and type(value["google_access_token"]) is str
             and re.fullmatch(r"[A-Za-z0-9._~+/=-]{1,8192}", value["google_access_token"])
             and type(value["google_expires_at"]) in {float, int}
             and 3570 <= value["google_expires_at"] - time.time() <= 3660,
             "docker_auth_shape_or_expiry_refused")
        github = subprocess.run(
            ["/opt/homebrew/bin/gh", "auth", "token", "--hostname=github.com"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=max(0.001, min(30, deadline - time.monotonic())),
            check=False, env=environment(),
        )
        need(github.returncode == 0 and 0 < len(github.stdout) <= 8193,
             "docker_github_normal_manager_unavailable")
        token = github.stdout.decode("ascii").strip()
        need(re.fullmatch(r"[A-Za-z0-9._~+/=-]{1,8192}", token), "docker_github_auth_shape_refused")
        value["github_token"] = token
        return value
    except writes.WriteDenied:
        raise
    except Exception:
        raise writes.WriteDenied("docker_auth_native_unavailable") from None
    finally:
        if write >= 0:
            os.close(write)
        os.close(read)
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=2)


def run_argv(
    image: str, release: str, name: str, volume: str, nonce: str, inputs: Path,
    resume: writes.source_sql.PreparedOwnerResume | None = None,
) -> list[str]:
    need(
        re.fullmatch(
            r"us-central1-docker.pkg.dev/"
            + writes.PROJECT
            + r"/cloud-run-source-deploy/hirewiz-release-operator@sha256:[a-f0-9]{64}",
            image,
        )
        and re.fullmatch(r"[a-f0-9]{40}", release)
        and re.fullmatch(r"[a-f0-9]{32}", nonce)
        and name == "hirewiz-cutover-" + nonce
        and volume == "hirewiz-cutover-data-" + nonce,
        "docker_fixed_execution_identity",
    )
    prior_mount: list[str] = []
    if resume is not None:
        resume.validate()
        need(volume != resume.volume, "docker_resume_current_prior_volume_overlap")
        prior_mount = ["--mount", "type=volume,src=" + resume.volume
                       + ",dst=/operator-prior,readonly"]
    return [
        str(DOCKER),
        "run",
        "--init",
        "--platform",
        "linux/amd64",
        "--name",
        name,
        "--label",
        LABEL + "=" + nonce,
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--network",
        "bridge",
        "--cpus",
        "2",
        "--memory",
        "4g",
        "--pids-limit",
        "256",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=256m,mode=1777",
        "--mount",
        "type=volume,src=" + volume + ",dst=/operator-work",
        *prior_mount,
        "--mount",
        "type=bind,src=" + str(inputs) + ",dst=/operator-input,readonly",
        "--mount",
        "type=bind,src=" + str(GCP_MANAGER) + ",dst=/operator-manager/gcloud,readonly",
        "--mount",
        "type=bind,src=" + str(GH_MANAGER) + ",dst=/operator-manager/gh,readonly",
        "-e",
        "CLOUDSDK_CONFIG=/operator-manager/gcloud",
        "-e",
        "GH_CONFIG_DIR=/operator-manager/gh",
        "-e",
        "CLOUDSDK_CORE_ACCOUNT=harshilyogeshbhai886@gmail.com",
        "-e",
        "CLOUDSDK_CORE_PROJECT=" + writes.PROJECT,
        "-e",
        "CLOUDSDK_CORE_UNIVERSE_DOMAIN=googleapis.com",
        "-e",
        "CLOUDSDK_CORE_DISABLE_PROMPTS=true",
        "-e",
        "CLOUDSDK_CORE_DISABLE_FILE_LOGGING=true",
        "-e",
        "CLOUDSDK_CORE_LOG_HTTP=false",
        "-e",
        "CLOUDSDK_AUTH_DISABLE_SSL_VALIDATION=false",
        "-e",
        "CLOUDSDK_AUTH_DISABLE_CREDENTIALS=false",
        "-e",
        "GH_HOST=github.com",
        "-e",
        "GH_PROMPT_DISABLED=1",
        "-e",
        "HIREWIZ_OPERATOR_COMMIT=" + release,
        "-i",
        "--entrypoint",
        "/opt/hirewiz-release/checkout/backend/.venv/bin/python",
        image,
        "-I",
        "-B",
        "/opt/hirewiz-release/checkout/infra/gcp/fresh_cutover_auth.py",
    ]


def verify_resume_volume(resume: writes.source_sql.PreparedOwnerResume,
                         deadline: float) -> None:
    """Fixed prior operation must be terminal; retained volume is never erased."""
    resume.validate()
    rows = json.loads(command(["volume", "inspect", resume.volume], deadline),
                      object_pairs_hook=writes.native.object_pairs)
    need(type(rows) is list and len(rows) == 1, "docker_resume_prior_volume_identity")
    row = rows[0]
    need(type(row) is dict and row.get("Name") == resume.volume
         and row.get("Driver") == "local" and row.get("Scope") == "local"
         and row.get("Options") in (None, {})
         and row.get("Labels", {}).get(LABEL) == resume.prior_nonce,
         "docker_resume_prior_volume_identity")
    containers = command(["ps", "-a", "--no-trunc", "--filter",
                          "label=" + LABEL + "=" + resume.prior_nonce,
                          "--format", "{{.ID}}"], deadline)
    need(not containers.strip(), "docker_resume_prior_container_not_terminal")


def local_barrier(value: dict[str, Any], workdir: Path) -> None:
    need(
        set(value)
        == {
            "event",
            "source_observer_secret",
            "source_owner_oid",
            "project_id",
            "branch_id",
            "old_role_oid",
        }
        and value.get("event") == "local_neon_delete_required"
        and value.get("project_id") == local.PROJECT
        and value.get("branch_id") == local.BRANCH
        and value.get("old_role_oid") == 16387
        and type(value.get("source_owner_oid")) is int
        and re.fullmatch(
            re.escape(writes.SOURCE_SECRET) + r":[1-9][0-9]{0,8}",
            value.get("source_observer_secret", ""),
        ),
        "docker_local_barrier_join_refused",
    )
    owner = writes.source_sql.PreparedOwner(value["source_owner_oid"])
    owner.validate()
    reader = writes.native.SDKGoogleReads(SDK, "harshilyogeshbhai886@gmail.com")
    name, version = value["source_observer_secret"].split(":")
    state, dsn = writes.retained._secret_value(
        reader, f"projects/{writes.NUMBER}/secrets/{name}/versions/{version}"
    )
    need(state == "ENABLED" and type(dsn) is str, "docker_local_source_secret_unavailable")
    assert isinstance(dsn, str)
    uri = make_url(dsn)
    need(
        uri.host == writes.source_sql.SOURCE_HOST
        and uri.port in {None, 5432}
        and uri.database == "neondb"
        and uri.username == writes.source_sql.OBSERVER_NAME
        and type(uri.password) is str
        and bool(uri.password)
        and set(uri.query) <= {"sslmode", "channel_binding"}
        and uri.query.get("sslmode") == "verify-full"
        and uri.query.get("channel_binding") == "prefer",
        "docker_local_source_secret_target_refused",
    )
    assert isinstance(uri.password, str)
    identity = writes.transfer.NativeDatabaseIdentity(
        writes.source_sql.SOURCE_HOST,
        5432,
        "neondb",
        writes.source_sql.SOURCE_CLUSTER,
        16391,
        writes.source_sql.OBSERVER_NAME,
        1884182,
        str(CA),
    )
    observer = writes.transfer.BoundDatabase(identity, uri.password)
    writes.delete_source_role_local(observer, owner, workdir)


def observe_container(name: str, nonce: str, image_id: str, deadline: float) -> str:
    # The container blocks on stdin until this real ownership join succeeds.
    for _ in range(20):
        rows = command(
            [
                "ps",
                "-a",
                "--no-trunc",
                "--filter",
                "label=" + LABEL + "=" + nonce,
                "--format",
                "{{.ID}}",
            ],
            deadline,
        ).splitlines()
        if rows:
            need(
                len(rows) == 1 and re.fullmatch(rb"[a-f0-9]{64}", rows[0]),
                "docker_container_identity_ambiguous",
            )
            ident = rows[0].decode()
            body = json.loads(
                command(["inspect", "--type", "container", ident], deadline),
                object_pairs_hook=writes.native.object_pairs,
            )
            need(type(body) is list and len(body) == 1, "docker_container_inspect_shape")
            row = body[0]
            need(
                row.get("Id") == ident
                and row.get("Name") == "/" + name
                and row.get("Image") == image_id
                and row.get("Config", {}).get("Labels", {}).get(LABEL) == nonce
                and row.get("HostConfig", {}).get("Init") is True,
                "docker_container_native_join_refused",
            )
            return ident
        time.sleep(0.1)
    raise writes.WriteDenied("docker_container_create_outcome_unknown_reconcile_only")


def terminal_projection(event: Any, release: str) -> dict[str, Any]:
    fields = {
        "event",
        "release",
        "joined_revisions",
        "gcp_traffic_promoted",
        "goal_complete",
        "queue_admission",
        "checkout_enabled",
        "frontend_deployment_verified",
        "requested_write_calls",
        "exact_http_request_count_known",
    }
    need(
        type(event) is dict
        and set(event) == fields
        and event.get("event") == "gcp_cutover_complete"
        and event.get("release") == release
        and event.get("gcp_traffic_promoted") is True
        and event.get("goal_complete") is False
        and event.get("queue_admission") == "closed"
        and event.get("checkout_enabled") is False
        and event.get("frontend_deployment_verified") is False
        and event.get("exact_http_request_count_known") is False
        and type(event.get("requested_write_calls")) is int
        and 0 < event["requested_write_calls"] <= 256,
        "docker_terminal_join_refused",
    )
    revisions = event.get("joined_revisions")
    need(
        type(revisions) is dict
        and set(revisions) == set(writes.native.release.SERVICES)
        and all(
            type(revision) is str
            and len(revision) <= 128
            and re.fullmatch(re.escape(service) + r"-[a-z0-9-]+", revision)
            for service, revision in revisions.items()
        ),
        "docker_terminal_revision_projection",
    )
    return {name: event[name] for name in fields}


def run(image: str, value: dict[str, Any], inputs: Path, workdir: Path) -> dict[str, Any]:
    absolute = time.monotonic() + 3660
    work_deadline = absolute - 60
    public_pin(DOCKER, DOCKER_SHA256, cap=64 * 1024 * 1024)
    public_pin(CA, CA_SHA256)
    for manager in (GCP_MANAGER, GH_MANAGER):
        info = manager.lstat()
        need(
            stat.S_ISDIR(info.st_mode) and info.st_uid == 501 and not info.st_mode & 0o022,
            "docker_normal_manager_metadata_refused",
        )
    need(
        inputs == workdir / "inputs" and inputs.is_absolute(), "docker_fixed_public_input_directory"
    )
    metadata = inputs.lstat()
    need(
        stat.S_ISDIR(metadata.st_mode)
        and metadata.st_uid == 501
        and metadata.st_mode & 0o777 == 0o700,
        "docker_public_input_directory_metadata",
    )
    inventory = inputs / "inventory.json"
    metadata = inventory.lstat()
    need(
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == 501
        and metadata.st_mode & 0o777 == 0o600
        and 0 < metadata.st_size <= 131072
        and hashlib.sha256(inventory.read_bytes()).hexdigest() == value["inventory_sha256"],
        "docker_expected_registry_input_pin",
    )
    nonce = uuid.uuid4().hex
    name, volume = "hirewiz-cutover-" + nonce, "hirewiz-cutover-data-" + nonce
    resume = (writes.source_sql.PreparedOwnerResume.from_payload(value["prepared_owner_resume"])
              if "prepared_owner_resume" in value else None)
    if resume is not None:
        verify_resume_volume(resume, work_deadline)
    argv = run_argv(image, value["release"], name, volume, nonce, inputs, resume)
    image_row = json.loads(
        command(["image", "inspect", image], work_deadline),
        object_pairs_hook=writes.native.object_pairs,
    )
    need(type(image_row) is list and len(image_row) == 1, "docker_image_native_inspect_shape")
    row = image_row[0]
    need(
        row.get("Os") == "linux"
        and row.get("Architecture") == "amd64"
        and re.fullmatch(r"sha256:[a-f0-9]{64}", row.get("Id", ""))
        and image in row.get("RepoDigests", [])
        and row.get("Config", {}).get("Labels", {}).get("org.opencontainers.image.revision")
        == value["release"],
        "docker_full_git_operator_image_join_refused",
    )
    password = bootstrap_password(min(work_deadline, time.monotonic() + 60))
    value = {**value, "bootstrap_password": password}
    writes.source_sql.once(
        workdir,
        "full-customer-cutover-container"
        if value["operation"] == "execute"
        else "readonly-operator-preflight",
    )
    scope = {
        "nonce": nonce,
        "name": name,
        "volume": volume,
        "image": image,
        "release": value["release"],
        "native_create_ack": False,
    }
    if resume is not None:
        scope["prepared_owner_resume"] = value["prepared_owner_resume"]
    fd = os.open(
        workdir
        / (
            "full-customer-cutover-container.scope.json"
            if value["operation"] == "execute"
            else "readonly-operator-preflight.scope.json"
        ),
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(fd, "wb") as record:
        record.write(json.dumps(scope, separators=(",", ":")).encode() + b"\n")
        record.flush()
        os.fsync(record.fileno())
    fd = os.open(workdir, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    command(["volume", "create", "--label", LABEL + "=" + nonce, volume], work_deadline)
    transient_auth = ephemeral_host_authentication(min(work_deadline, time.monotonic() + 60))
    process = None
    ident = None
    terminal = None
    seen_barrier = False

    def elapsed(_signal: int, _frame: Any) -> None:
        raise writes.WriteDenied("docker_absolute_deadline_reconcile_only")

    prior = signal.signal(signal.SIGALRM, elapsed)
    signal.setitimer(signal.ITIMER_REAL, max(0.001, work_deadline - time.monotonic()))
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment(),
        )
        ident = observe_container(name, nonce, row["Id"], work_deadline)
        assert (
            process.stdin is not None and process.stdout is not None and process.stderr is not None
        )
        need(transient_auth["google_expires_at"] - time.time() >= 3545,
             "docker_auth_expired_before_container_handoff")
        process.stdin.write(json.dumps(transient_auth, separators=(",", ":")).encode() + b"\n")
        transient_auth.clear()
        process.stdin.write(json.dumps(value, separators=(",", ":")).encode() + b"\n")
        process.stdin.flush()
        value["bootstrap_password"] = ""
        password = ""
        pending = bytearray()
        counts = {process.stdout.fileno(): 0, process.stderr.fileno(): 0}
        with selectors.DefaultSelector() as selected:
            selected.register(process.stdout, selectors.EVENT_READ)
            selected.register(process.stderr, selectors.EVENT_READ)
            while selected.get_map():
                need(time.monotonic() < work_deadline, "docker_absolute_deadline_reconcile_only")
                for key, _ in selected.select(0.1):
                    chunk = os.read(key.fd, 4096)
                    if not chunk:
                        selected.unregister(key.fileobj)
                        continue
                    counts[key.fd] += len(chunk)
                    need(counts[key.fd] <= 65536, "docker_protocol_or_discarded_error_bound")
                    if key.fd != process.stdout.fileno():
                        continue
                    pending.extend(chunk)
                    need(len(pending) <= 8192, "docker_protocol_line_bound")
                    while b"\n" in pending:
                        raw, _, tail = pending.partition(b"\n")
                        pending = bytearray(tail)
                        event = json.loads(raw, object_pairs_hook=writes.native.object_pairs)
                        need(
                            type(event) is dict and type(event.get("event")) is str,
                            "docker_protocol_shape",
                        )
                        if event["event"] == "local_neon_delete_required":
                            need(
                                not seen_barrier and terminal is None,
                                "docker_duplicate_local_barrier",
                            )
                            seen_barrier = True
                            local_barrier(event, workdir)
                            process.stdin.write(
                                b'{"event":"local_neon_action_finished_reobserve_native"}\n'
                            )
                            process.stdin.flush()
                        elif event["event"] in {"native_scope_bound", "native_cut_established"}:
                            need(
                                terminal is None
                                and set(event)
                                <= {
                                    "event",
                                    "source_database_oid",
                                    "source_owner_oid",
                                    "target_database_oid",
                                },
                                "docker_progress_projection_refused",
                            )
                        elif event["event"] == "cutover_stage":
                            need(terminal is None, "docker_progress_after_terminal_refused")
                            projection = safe_events.stage_projection(event)
                            print(json.dumps(projection, separators=(",", ":")), flush=True)
                        elif event["event"] == "cutover_stopped_reconcile_only":
                            need(terminal is None, "docker_refusal_after_terminal_refused")
                            projection = safe_events.stopped_projection(
                                event, safe_events.literal_codes(Path(__file__).resolve().parents[2]),
                            )
                            print(json.dumps(projection, separators=(",", ":")), flush=True)
                            raise writes.WriteDenied("docker_cutover_stopped_reconcile_only")
                        elif event["event"] == "operator_preflight_complete":
                            need(
                                value["operation"] == "preflight"
                                and terminal is None
                                and set(event)
                                == {
                                    "event",
                                    "release",
                                    "github_user_id",
                                    "project_number",
                                    "target_database_oid",
                                    "writes_performed",
                                    "full_release_admission_verified",
                                    "goal_complete",
                                }
                                and event.get("release") == value["release"]
                                and type(event.get("github_user_id")) is int
                                and event["github_user_id"] > 0
                                and event.get("project_number") == writes.NUMBER
                                and event.get("target_database_oid") == 16395
                                and event.get("writes_performed") is False
                                and event.get("full_release_admission_verified") is False
                                and event.get("goal_complete") is False,
                                "docker_preflight_projection_refused",
                            )
                            terminal = event
                        elif event["event"] == "gcp_cutover_complete":
                            need(
                                terminal is None
                                and seen_barrier
                                and event.get("release") == value["release"]
                                and event.get("gcp_traffic_promoted") is True
                                and event.get("goal_complete") is False,
                                "docker_terminal_join_refused",
                            )
                            terminal = terminal_projection(event, value["release"])
                        else:
                            raise writes.WriteDenied("docker_cutover_stopped_reconcile_only")
        need(
            not pending
            and process.wait(timeout=max(0.001, work_deadline - time.monotonic())) == 0
            and terminal is not None,
            "docker_cutover_result_unknown_reconcile_only",
        )
        assert terminal is not None
        return terminal
    finally:
        transient_auth.clear()
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, prior)
        # No signal to reused local CLI PIDs or unjoined containers. The joined
        # owned container is force-removed, then native absence must be observed.
        try:
            if ident is not None:
                command(["rm", "--force", ident], absolute)
                remaining = command(
                    [
                        "ps",
                        "-a",
                        "--no-trunc",
                        "--filter",
                        "label=" + LABEL + "=" + nonce,
                        "--format",
                        "{{.ID}}",
                    ],
                    absolute,
                )
                need(not remaining.strip(), "docker_container_cleanup_unconfirmed")
        finally:
            # A failed native Docker cleanup must not skip local CLI reap.
            if process is not None:
                if process.stdin:
                    process.stdin.close()
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=max(0.001, min(2, absolute - time.monotonic())))
        # Volume is deliberately retained for protected operator reconciliation;
        # no unknown restore rollback or customer-data destruction is inferred.


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--execute", action="store_true")
    modes.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    info = args.config.lstat()
    need(
        args.config == args.workdir / "approved-cutover.json"
        and sys.platform == "darwin"
        and stat.S_ISREG(info.st_mode)
        and info.st_uid == 501
        and info.st_mode & 0o777 == 0o600
        and 0 < info.st_size <= 65536,
        "docker_approved_config_metadata_refused",
    )
    raw = args.config.read_bytes()
    need(
        hashlib.sha256(raw).hexdigest() == args.config_sha256, "docker_approved_config_pin_refused"
    )
    value = json.loads(raw, object_pairs_hook=writes.native.object_pairs)
    # Importing the Linux entry point is pure; no provider action is performed.
    from fresh_cutover_execute import INPUT_FIELDS, RESUME_FIELD

    need(
        type(value) is dict and frozenset(value) in {
            INPUT_FIELDS - {"bootstrap_password", "operation"},
            (INPUT_FIELDS | {RESUME_FIELD}) - {"bootstrap_password", "operation"},
        },
        "docker_approved_config_fields_refused",
    )
    if RESUME_FIELD in value:
        writes.source_sql.PreparedOwnerResume.from_payload(value[RESUME_FIELD])
    value["operation"] = "execute" if args.execute else "preflight"
    result = run(args.image, value, args.inputs, args.workdir)
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        print(
            '{"event":"cutover_stopped_reconcile_only","automatic_replay":false,"goal_complete":false}'
        )
        raise SystemExit(2) from None
