"""Fixed local human-manager Neon route; never transport its credentials.

Caller performs genuine SQL readiness before invocation and SQL absence after.
CLI completion is not a role-retirement or native-fence certificate.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

BASE = Path(
    "/Users/harshil/.codex/hirewiz-evidence/20261013/neon-normal-manager-metadata-route-private-g1xawwzt/neon_owned_identity.py"
)
BASE_SHA256 = "af6f3a0c204f44e288079d19741f35f93cbe495ec8d642e3e6b7c3f2cedc3768"
ACTOR = "c9f4043f-260d-402d-8afe-10b644fc72bf"
ORG = "org-ancient-pond-42861142"
PROJECT = "little-pond-36610622"
BRANCH = "br-fancy-sunset-ao8i4a57"
ENDPOINT = "ep-damp-rice-aosknaqp"
HOST = "ep-damp-rice-aosknaqp.c-2.ap-southeast-1.aws.neon.tech"
OWNER = "hirewiz_source_owner_20261014_r1"
ROLE = "neondb_owner"
PREFIX = f"/projects/{PROJECT}/branches/{BRANCH}"


def need(value: Any, code: str) -> None:
    if not value:
        raise RuntimeError(code)


def load_base() -> Any:
    metadata = BASE.lstat()
    need(
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == 501
        and metadata.st_mode & 0o777 == 0o600
        and 0 < metadata.st_size <= 65536,
        "local_neon_owner_source_metadata_refused",
    )
    body = BASE.read_bytes()
    need(hashlib.sha256(body).hexdigest() == BASE_SHA256, "local_neon_owner_source_pin_refused")
    spec = importlib.util.spec_from_file_location("fixed_retained_normal_neon_owner", BASE)
    need(spec is not None and spec.loader is not None, "local_neon_owner_unavailable")
    assert spec is not None and spec.loader is not None
    module: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # The existing live anchor remains unreaped through its last group signal.
    # Add only native group absence observation after the original bounded reap.
    original_stop = module.stop_and_reap

    def stop_checked(pid: int, owned: bool, deadline: float) -> Any:
        result = original_stop(pid, owned, deadline)
        if result is None or not owned:
            return None
        while time.monotonic() < deadline:
            try:
                os.killpg(pid, 0)  # Observation only; no later terminating signal.
            except ProcessLookupError:
                return result
            except OSError:
                return None
            time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        return None

    module.stop_and_reap = stop_checked
    return module


def claim(workdir: Path, phase: str) -> None:
    info = workdir.lstat()
    need(
        workdir.is_absolute()
        and stat.S_ISDIR(info.st_mode)
        and info.st_uid == 501
        and info.st_mode & 0o777 == 0o700,
        "local_neon_private_workdir_refused",
    )
    descriptor = os.open(
        workdir / (phase + ".started.json"),
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(json.dumps({"phase": phase, "operator_replay": False}).encode() + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(workdir, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class Route:
    def __init__(self, base: Any, workdir: Path, absolute: float):
        self.base, self.workdir, self.absolute = base, workdir, absolute
        self.commands = 0

    def call(
        self, method: str, path: str, *, field: str | None = None, phase: str | None = None
    ) -> dict[str, Any] | None:
        allowed_gets = {
            "/users/me",
            "/projects/" + PROJECT,
            "/projects/" + PROJECT + "/endpoints",
            PREFIX + "/databases/neondb",
            PREFIX + "/roles/" + ROLE,
            PREFIX + "/roles",
        }
        allowed = (
            method == "GET"
            and (
                path in allowed_gets
                or re.fullmatch(
                    re.escape("/projects/" + PROJECT + "/operations/") + r"[a-f0-9-]{36}", path
                )
            )
            or method == "PATCH"
            and path == PREFIX + "/databases/neondb"
            and field == "database.owner_name=" + OWNER
            or method == "DELETE"
            and path == PREFIX + "/roles/" + ROLE
            and field is None
        )
        need(
            allowed and self.commands < 20 and time.monotonic() < self.absolute - 2,
            "local_neon_route_or_budget_refused",
        )
        argv = [
            str(self.base.NODE),
            str(self.base.ENTRY),
            "api",
            path,
            *self.base.SHARED,
            "--method",
            method,
            "--force-auth=false",
        ]
        if field is not None:
            argv.extend(("--field", field))

        def launching() -> None:
            if method != "GET":
                need(phase is not None, "local_neon_mutation_phase_missing")
                assert phase is not None
                claim(self.workdir, phase)

        self.commands += 1
        category, raw, _counts = self.base.owned(
            argv,
            self.base.environment("phase1"),
            self.base.PACKET,
            total_seconds=max(2.001, self.absolute - time.monotonic()),
            reap_seconds=2,
            absolute=self.absolute,
            stdout_cap=1048576,
            stderr_cap=65536,
            retain_stdout=True,
            launched=launching,
        )
        need(category == "exit_zero", "local_neon_command_unknown_reconcile_only")
        if raw.strip() == b"":
            need(method == "DELETE", "local_neon_read_response_missing")
            return None  # Supported synchronous204; SQL absence remains required.
        value = self.base.parse_json(raw)
        need(type(value) is dict, "local_neon_response_identity_refused")
        return value

    def completed(self, acknowledgement: dict[str, Any] | None) -> None:
        if acknowledgement is None:
            return
        rows = acknowledgement.get("operations", [])
        need(type(rows) is list and len(rows) <= 4, "local_neon_operation_bound_refused")
        for row in rows:
            need(
                type(row) is dict
                and type(row.get("id")) is str
                and re.fullmatch(r"[a-f0-9-]{36}", row["id"])
                and row.get("project_id") == PROJECT
                and row.get("branch_id", BRANCH) == BRANCH
                and row.get("endpoint_id", ENDPOINT) == ENDPOINT,
                "local_neon_operation_scope_refused",
            )
            ident = row["id"]
            for _ in range(3):
                need(
                    type(row.get("status")) is str
                    and row["status"]
                    in {
                        "scheduling",
                        "running",
                        "finished",
                        "failed",
                        "error",
                        "cancelling",
                        "cancelled",
                        "skipped",
                    },
                    "local_neon_operation_state_refused",
                )
                if row["status"] == "finished":
                    break
                need(
                    row["status"] in {"scheduling", "running"},
                    "local_neon_operation_not_successful",
                )
                next_row = self.call("GET", f"/projects/{PROJECT}/operations/{ident}")
                need(
                    next_row is not None and type(next_row.get("operation")) is dict,
                    "local_neon_operation_result_missing",
                )
                assert next_row is not None
                row = next_row["operation"]
                need(
                    row.get("id") == ident
                    and row.get("project_id") == PROJECT
                    and row.get("branch_id", BRANCH) == BRANCH
                    and row.get("endpoint_id", ENDPOINT) == ENDPOINT,
                    "local_neon_operation_scope_refused",
                )
            else:
                need(row.get("status") == "finished", "local_neon_operation_pending_reconcile_only")


def perform(route: Route) -> dict[str, Any]:
    user = route.call("GET", "/users/me")
    need(user is not None and user.get("id") == ACTOR, "local_neon_current_actor_drift")
    project = route.call("GET", "/projects/" + PROJECT)
    need(project is not None and type(project.get("project")) is dict, "local_neon_project_missing")
    assert project is not None
    value = project["project"]
    need(
        value.get("id") == PROJECT and value.get("org_id", value.get("owner_id")) == ORG,
        "local_neon_current_project_org_drift",
    )
    endpoints = route.call("GET", "/projects/" + PROJECT + "/endpoints")
    need(
        endpoints is not None
        and type(endpoints.get("endpoints")) is list
        and len(endpoints["endpoints"]) <= 64,
        "local_neon_endpoint_inventory_refused",
    )
    assert endpoints is not None
    matched = [
        row for row in endpoints["endpoints"] if type(row) is dict and row.get("id") == ENDPOINT
    ]
    need(
        len(matched) == 1
        and matched[0].get("project_id") == PROJECT
        and matched[0].get("branch_id") == BRANCH
        and matched[0].get("host") == HOST
        and matched[0].get("type") == "read_write"
        and matched[0].get("disabled") is False,
        "local_neon_current_endpoint_host_drift",
    )
    role = route.call("GET", PREFIX + "/roles/" + ROLE)
    need(role is not None and type(role.get("role")) is dict, "local_neon_current_role_missing")
    assert role is not None
    value = role["role"]
    need(
        value.get("name") == ROLE
        and value.get("branch_id") == BRANCH
        and (
            "protected" not in value
            or type(value["protected"]) is bool
            and value["protected"] is False
        ),
        "local_neon_current_role_scope_or_protection_refused",
    )
    database = route.call("GET", PREFIX + "/databases/neondb")
    need(
        database is not None and type(database.get("database")) is dict,
        "local_neon_current_database_missing",
    )
    assert database is not None
    value = database["database"]
    need(
        value.get("name") == "neondb"
        and value.get("branch_id") == BRANCH
        and value.get("owner_name") in {ROLE, OWNER},
        "local_neon_current_database_scope_refused",
    )
    if value["owner_name"] == ROLE:
        ack = route.call(
            "PATCH",
            PREFIX + "/databases/neondb",
            field="database.owner_name=" + OWNER,
            phase="neon-source-owner-metadata",
        )
        route.completed(ack)
        observed = route.call("GET", PREFIX + "/databases/neondb")
        need(
            observed is not None
            and type(observed.get("database")) is dict
            and observed["database"].get("name") == "neondb"
            and observed["database"].get("branch_id") == BRANCH
            and observed["database"].get("owner_name") == OWNER,
            "local_neon_owner_metadata_unconfirmed",
        )
    acknowledgement = route.call(
        "DELETE", PREFIX + "/roles/" + ROLE, phase="neon-source-role-delete"
    )
    route.completed(acknowledgement)
    roles = route.call("GET", PREFIX + "/roles")
    need(
        roles is not None
        and type(roles.get("roles")) is list
        and len(roles["roles"]) <= 256
        and all(
            type(row) is dict
            and row.get("branch_id") == BRANCH
            and type(row.get("name")) is str
            and row["name"] != ROLE
            for row in roles["roles"]
        ),
        "local_neon_provider_role_absence_unconfirmed",
    )
    return {
        "status": "local_normal_neon_delete_completed_requires_sql_absence",
        "actor_id": ACTOR,
        "project_id": PROJECT,
        "branch_id": BRANCH,
        "endpoint_id": ENDPOINT,
        "requested_cli_commands": route.commands,
        "http_request_count_known": False,
        "credential_manager_bodies_read_by_agent": False,
        "source_sql_absence_verified": False,
        "native_fence_complete": False,
    }


def bootstrap_password(base: Any, absolute: float) -> str:
    """One fixed normal-manager GET; credential output stays in a private pipe."""
    argv = [
        str(base.NODE),
        str(base.ENTRY),
        "api",
        "/projects/shiny-cell-70214683/connection_uri",
        *base.SHARED,
        "--force-auth=false",
        "--method",
        "GET",
        "--query",
        "branch_id=br-square-mode-b4g6sxrz",
        "--query",
        "endpoint_id=ep-weathered-tooth-b43oa1n6",
        "--query",
        "database_name=neondb",
        "--query",
        "role_name=hirewiz_bootstrap_20261013",
        "--query",
        "pooled=false",
    ]
    category, raw, _ = base.owned(
        argv,
        base.environment("phase1"),
        base.PACKET,
        total_seconds=57,
        reap_seconds=2,
        absolute=absolute,
        stdout_cap=65536,
        stderr_cap=65536,
        retain_stdout=True,
    )
    need(category == "exit_zero", "local_neon_bootstrap_native_get_refused")
    value = base.parse_json(raw)
    need(
        type(value) is dict
        and set(value) == {"uri"}
        and type(value["uri"]) is str
        and len(value["uri"]) <= 32768,
        "local_neon_bootstrap_schema_refused",
    )
    uri = urlsplit(value["uri"])
    parts = parse_qsl(uri.query, keep_blank_values=True, strict_parsing=True)
    need(
        len(parts) == len({key for key, _ in parts}), "local_neon_bootstrap_duplicate_query_refused"
    )
    options = dict(parts)
    need(
        uri.scheme in {"postgres", "postgresql"}
        and uri.hostname == "ep-weathered-tooth-b43oa1n6.c-6.us-east-2.aws.neon.tech"
        and uri.port in {None, 5432}
        and uri.username is not None
        and unquote(uri.username) == "hirewiz_bootstrap_20261013"
        and unquote(uri.path) == "/neondb"
        and not uri.fragment
        and uri.password is not None
        and set(options) <= {"sslmode", "channel_binding"}
        and options.get("sslmode", "require") in {"require", "verify-ca", "verify-full"}
        and options.get("channel_binding", "prefer") in {"prefer", "require"},
        "local_neon_bootstrap_target_refused",
    )
    password = unquote(uri.password)
    need(
        0 < len(password.encode()) <= 8192 and "\x00" not in password,
        "local_neon_bootstrap_password_shape",
    )
    return password


def main() -> None:
    absolute = time.monotonic() + 57
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", type=Path)
    parser.add_argument("--bootstrap-credential-fd", type=int)
    args = parser.parse_args()
    base = load_base()
    base.preflight("phase1", absolute - 2)
    if args.bootstrap_credential_fd is not None:
        descriptor = args.bootstrap_credential_fd
        need(
            args.workdir is None and 3 <= descriptor <= 1024,
            "local_neon_bootstrap_private_pipe_required",
        )
        info = os.fstat(descriptor)
        need(
            stat.S_ISFIFO(info.st_mode) and info.st_uid == os.getuid(),
            "local_neon_bootstrap_private_pipe_required",
        )
        password = bootstrap_password(base, absolute)
        body = json.dumps({"password": password}, separators=(",", ":")).encode()
        need(
            len(body) <= 16384 and os.write(descriptor, body) == len(body),
            "local_neon_bootstrap_pipe_write_refused",
        )
        os.close(descriptor)
        print(
            '{"status":"local_neon_bootstrap_private_pipe_complete","http_request_count_known":false}'
        )
        return
    need(args.workdir is not None, "local_neon_delete_workdir_missing")
    route = Route(base, args.workdir, absolute)
    result = perform(route)
    need(time.monotonic() < absolute - 2, "local_neon_action_deadline")
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        print('{"status":"local_neon_action_unknown_reconcile_only","operator_replay":false}')
        raise SystemExit(2) from None
