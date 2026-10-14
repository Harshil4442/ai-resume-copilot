"""One Linux process retains native authority through the local Neon barrier.

No provider operations at import. Invoke only in the reviewed owned Docker
container after concrete cutover approval. Stdin credentials never enter files,
argv, receipts or stdout. The supported RAM wrapper supplies short-lived SDK
tokens in process memory; the host barrier is a wakeup, not proof.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import signal
import sys
import time
import warnings
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

ROOT = Path("/opt/hirewiz-release/checkout")
sys.path[:0] = [str(ROOT / "infra/gcp"), str(ROOT / "backend")]

import fresh_cutover_writes as writes  # noqa: E402 -- explicit standalone checkout imports under -I
import fresh_monetary_release as fresh  # noqa: E402 -- explicit standalone checkout imports under -I
import fresh_source_authority as source_sql  # noqa: E402 -- explicit standalone checkout imports under -I
import native_fresh_fence as native  # noqa: E402 -- explicit standalone checkout imports under -I

from scripts.neon_execution_files import (  # noqa: E402 -- standalone checkout
    LinuxExecutionFiles,  # noqa: E402 -- explicit standalone checkout imports under -I
)

INPUT_FIELDS = frozenset(
    {
        "release",
        "image_digest",
        "build_id",
        "ci_run_id",
        "backup_uri",
        "inventory_sha256",
        "model_primary",
        "role_environment",
        "bootstrap_password",
        "operation",
    }
)
TARGET = native.release.FreshTargetPlan(
    "hirewiz-fresh-runtime-db-20261013:1",
    "hirewiz-fresh-migrator-db-20261013:1",
    "hirewiz-fresh-observer-db-20261013:1",
)
WORK = Path("/operator-work")


def need(value: Any, code: str) -> None:
    if not value:
        raise native.release.ReleaseDenied(code)


def decode_input(raw: bytes) -> dict[str, Any]:
    need(type(raw) is bytes and 0 < len(raw) <= 131072, "cutover_input_bound")
    try:
        value = json.loads(raw, object_pairs_hook=native.object_pairs)
    except Exception:
        raise native.release.ReleaseDenied("cutover_input_json_refused") from None
    need(type(value) is dict and set(value) == INPUT_FIELDS, "cutover_input_fields_refused")
    need(
        type(value["bootstrap_password"]) is str
        and 1 <= len(value["bootstrap_password"].encode()) <= 8192
        and "\x00" not in value["bootstrap_password"],
        "cutover_bootstrap_password_shape",
    )
    need(value["operation"] in {"preflight", "execute"}, "cutover_operation_required")
    roles = value["role_environment"]
    need(
        type(roles) is dict
        and 4 <= len(roles) <= 19
        and all(
            type(k) is str
            and re.fullmatch(r"HIREWIZ_CUTOVER_(?:ROLE|OPERATOR)_[A-Z_]{1,32}", k)
            and type(v) is str
            and v
            in {
                source_sql.OLD_NAME,
                source_sql.RUNTIME_NAME,
                source_sql.MIGRATOR_NAME,
                source_sql.OWNER_NAME,
                "cloud_admin",
                "neon_service",
            }
            for k, v in roles.items()
        ),
        "cutover_role_registry_shape",
    )
    return value


def plan(value: dict[str, Any], policies: dict[str, str]) -> fresh.FreshSourcePlan:
    result = fresh.FreshSourcePlan(
        release=value["release"],
        image_digest=value["image_digest"],
        build_id=value["build_id"],
        ci_run_id=value["ci_run_id"],
        runtime_secret="",
        migration_secret="",
        observer_secret="",
        database_owner_oid=16387,
        expense_secret=policies["expense"],
        expense_sha256=writes.POLICIES["expense"][2],
        model_policy_secret=policies["model"],
        model_policy_sha256=writes.POLICIES["model"][2],
        model_key_secret="",
        model_primary=value["model_primary"],
        model_api_base=native.release.MODEL_API_BASE,
        runtime_service_account=writes.NEW_ACCOUNTS[0]
        + "@"
        + writes.PROJECT
        + ".iam.gserviceaccount.com",
        migration_service_account=writes.NEW_ACCOUNTS[1]
        + "@"
        + writes.PROJECT
        + ".iam.gserviceaccount.com",
        backup_uri=value["backup_uri"],
        inventory_path=Path("/operator-input/inventory.json"),
        inventory_sha256=value["inventory_sha256"],
    )
    result.validate()
    return result


def configure_registry(
    boundary: native.NativeFreshFenceBoundary, source: fresh.FreshSourcePlan, values: dict[str, str]
) -> None:
    module = boundary._module(
        "backend/scripts/monetary_cutover_preflight.py", "hirewiz_cutover_registry"
    )
    inventory, _ = module.load_inventory(
        source.inventory_path, source.inventory_sha256, native.datetime.now(native.UTC)
    )
    expected = set(inventory.role_env.values()) | set(inventory.database_operator_role_env)
    need(
        set(values) == expected
        and inventory.retired_role_mode == "deleted"
        and inventory.retired_role_oid == 16387
        and inventory.expected_database_oid == 16391
        and inventory.expected_system_identifier_sha256 == source_sql.SOURCE_CLUSTER
        and inventory.database_namespace == "public"
        and inventory.candidate_commit == source.release
        and inventory.candidate_image == source.image_digest,
        "cutover_registry_native_identity_mismatch",
    )
    expected_roles = {
        "retired": source_sql.OLD_NAME,
        "replacement_runtime": source_sql.RUNTIME_NAME,
        "replacement_migration": source_sql.MIGRATOR_NAME,
    }
    need(
        all(values[name] == expected_roles[alias] for alias, name in inventory.role_env.items())
        and source_sql.OWNER_NAME
        in {values[name] for name in inventory.database_operator_role_env},
        "cutover_registry_role_mismatch",
    )
    os.environ.update(values)


def execute(
    value: dict[str, Any],
    boundary: native.NativeFreshFenceBoundary,
    event: Callable[[dict[str, Any]], None],
    barrier: Callable[[], None],
) -> dict[str, Any]:
    """Real forward path. Any exception stops; no roll-forward/replay fallback."""
    files = LinuxExecutionFiles()
    files.validate()
    need(type(boundary) is native.NativeFreshFenceBoundary, "cutover_native_boundary_required")
    need(
        os.environ.get("HIREWIZ_OPERATOR_COMMIT") == value["release"],
        "cutover_image_commit_mismatch",
    )
    # Existing full Git/CI/image gate runs before old authority retirement.
    # Replacement identities are created only after captured issuers are isolated.
    bootstrap = writes.transfer.BoundDatabase(
        writes.transfer.NativeDatabaseIdentity(
            writes.TARGET_HOST,
            5432,
            "neondb",
            writes.TARGET_CLUSTER,
            16395,
            "hirewiz_bootstrap_20261013",
            16391,
            files.root_certificate,
        ),
        value["bootstrap_password"],
    )
    connection = bootstrap.connect()
    try:
        with connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            writes.transfer._empty(connection)
    finally:
        connection.close()
    writer = writes.OfficialWrites(boundary, WORK)
    policies = {
        "expense": "hirewiz-expense-policy-20261014:1",
        "model": "hirewiz-model-cost-policy-20261014:1",
    }
    source = plan(value, policies)
    # These published versions were observed by root with native CRC/hash
    # readback; the existing live price/hash gate must still pass now.
    configure_registry(boundary, source, value["role_environment"])
    before_isolation = replace(
        source,
        runtime_service_account="hirewiz-api@" + writes.PROJECT + ".iam.gserviceaccount.com",
        migration_service_account=writes.TASKS_EMAIL,
    )
    boundary.verify_identity_and_policy(before_isolation)
    boundary.verify_admission_closed()
    boundary.verify_document_preparation(before_isolation)
    origin = source_sql.original_owner(boundary, files)
    source_sql.preflight_source(origin)
    readiness = origin.connect()
    try:
        with readiness.transaction():
            readiness.execute("SET TRANSACTION READ ONLY")
            source_sql.verify_source_quiescent(boundary, readiness)
    finally:
        readiness.close()
    owner = source_sql.prepare_owner(origin, WORK)
    source = replace(source, database_owner_oid=owner.oid)
    boundary.bind_authorities_once(source)
    event(
        {"event": "native_scope_bound", "source_database_oid": 16391, "source_owner_oid": owner.oid}
    )
    writer.disable_old_principals()
    writer.delete_old_model_keys()
    boundary.begin_source_observer_isolation()
    accounts = writer.create_accounts()
    boundary.verify_identity_and_policy(source)
    writer.grant_attested_policy_sources(source, accounts[0])
    for (_, _, name), account in zip(writes.TARGET_ROLES, accounts, strict=True):
        writer.grant_secret_access(name, (account,))
    writer.grant_secret_access(writes.REDIS_SECRET, (accounts[0],))
    writer.grant_runtime_resources(accounts[0])
    passwords = source_sql.new_source_passwords()
    need(
        all(
            not writes.hmac.compare_digest(passwords.observer.encode(), old.encode())
            for old in (*boundary._source_old_passwords, *boundary._setup_passwords.values())
        ),
        "cutover_source_password_reuses_precut_value",
    )
    observer_ref = writes.publish_source_observer(writer, origin, passwords)
    transition = source_sql.transition_source(origin, owner, boundary, WORK, passwords)
    observer = source_sql.observer_target(origin, transition)
    writer.grant_secret_access(writes.SOURCE_SECRET, (accounts[2],))
    source_sql.verify_provider_delete_ready(observer, owner)
    event(
        {
            "event": "local_neon_delete_required",
            "source_observer_secret": observer_ref,
            "source_owner_oid": owner.oid,
            "project_id": "little-pond-36610622",
            "branch_id": "br-fancy-sunset-ao8i4a57",
            "old_role_oid": 16387,
        }
    )
    barrier()  # Fixed notification only; fresh native absence is mandatory below.
    source_sql.verify_provider_delete_complete(observer, owner)
    observer_ref, observer = writes.rotate_source_observer_after_delete(writer, observer, owner)
    source = replace(source, observer_secret=observer_ref)
    boundary.bind_source_observer_after_isolation(source)
    boundary.establish_retirement_cut(source)
    target = writes.rotate_empty_target(writer, bootstrap)
    model = writer.create_model_replacement()
    writer.grant_secret_access(writes.MODEL_SECRET, (accounts[0],))
    source = replace(source, model_key_secret=model)
    boundary.bind_replacements_after_isolation(source, target)
    dump = Path("/usr/lib/postgresql/17/bin/pg_dump")
    restore = Path("/usr/lib/postgresql/17/bin/pg_restore")
    transfer_dir = WORK / "transfer"
    transfer_dir.mkdir(mode=0o700)
    transfer = fresh.TransferPlan(
        dump,
        restore,
        hashlib.sha256(dump.read_bytes()).hexdigest(),
        hashlib.sha256(restore.read_bytes()).hexdigest(),
        transfer_dir,
    )
    event(
        {
            "event": "native_cut_established",
            "source_database_oid": 16391,
            "target_database_oid": 16395,
        }
    )
    staged = fresh.stage_fresh_release(source, target, transfer, boundary, execute=True)
    need(
        staged.get("staging_complete") is True and staged.get("traffic_promoted") is False,
        "cutover_stage_result_shape",
    )
    writes.finalize_target_migrator(writer, bootstrap, source, target)
    result = fresh.promote_fresh_release(source, target, staged["joined_revisions"], boundary)
    # Existing feature admission, payment checkout, queue resumption and Vercel
    # acceptance remain separate; this result never says the overall goal ended.
    return {
        "event": "gcp_cutover_complete",
        "release": source.release,
        "joined_revisions": result["joined_revisions"],
        "gcp_traffic_promoted": result["gcp_traffic_promoted"],
        "goal_complete": False,
        "queue_admission": "closed",
        "checkout_enabled": False,
        "frontend_deployment_verified": False,
        "requested_write_calls": writer.requested_calls,
        "exact_http_request_count_known": False,
    }


def preflight(value: dict[str, Any], boundary: native.NativeFreshFenceBoundary) -> dict[str, Any]:
    """Purpose-bound normal-manager and empty-target reads; no release proof."""
    files = LinuxExecutionFiles()
    files.validate()
    need(
        os.environ.get("HIREWIZ_OPERATOR_COMMIT") == value["release"],
        "cutover_image_commit_mismatch",
    )
    actual = boundary._command("git", "rev-parse", "HEAD").decode().strip()
    need(actual == value["release"], "cutover_full_git_checkout_mismatch")
    account = boundary._get("project", "projects/" + writes.PROJECT)
    need(
        account.get("name") == "projects/" + writes.NUMBER
        and account.get("projectId") == writes.PROJECT
        and account.get("state") == "ACTIVE",
        "cutover_normal_google_manager_read_refused",
    )
    user = json.loads(
        boundary._command("gh", "api", "user", "--jq", "{login:.login,id:.id}"),
        object_pairs_hook=native.object_pairs,
    )
    need(
        type(user) is dict
        and set(user) == {"login", "id"}
        and user["login"] == "Harshil4442"
        and type(user["id"]) is int
        and user["id"] > 0,
        "cutover_normal_github_manager_read_refused",
    )
    bootstrap = writes.transfer.BoundDatabase(
        writes.transfer.NativeDatabaseIdentity(
            writes.TARGET_HOST,
            5432,
            "neondb",
            writes.TARGET_CLUSTER,
            16395,
            "hirewiz_bootstrap_20261013",
            16391,
            files.root_certificate,
        ),
        value["bootstrap_password"],
    )
    connection = bootstrap.connect()
    try:
        with connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            writes.transfer._empty(connection)
    finally:
        connection.close()
    origin = source_sql.original_owner(boundary, files)
    source_sql.preflight_source(origin)
    connection = origin.connect()
    try:
        with connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            source_sql.verify_source_quiescent(boundary, connection)
    finally:
        connection.close()
    return {
        "event": "operator_preflight_complete",
        "release": value["release"],
        "github_user_id": user["id"],
        "project_number": writes.NUMBER,
        "target_database_oid": 16395,
        "writes_performed": False,
        "full_release_admission_verified": False,
        "goal_complete": False,
    }


def main() -> None:
    absolute = time.monotonic() + 3540

    def deadline(_signal: int, _frame: Any) -> None:
        raise native.release.ReleaseDenied("cutover_absolute_deadline_reconcile_only")

    signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, 3540)
    protocol = os.dup(1)
    os.set_inheritable(protocol, False)
    with open(os.devnull, "r+") as null:
        os.dup2(null.fileno(), 1)
        os.dup2(null.fileno(), 2)
        sys.stdout = null
        sys.stderr = null
        logging.disable(logging.CRITICAL)
        warnings.simplefilter("ignore")

        def event(value: dict[str, Any]) -> None:
            need(time.monotonic() < absolute, "cutover_absolute_deadline_reconcile_only")
            body = json.dumps(value, separators=(",", ":")).encode() + b"\n"
            need(len(body) <= 4096, "cutover_protocol_bound")
            os.write(protocol, body)

        def barrier() -> None:
            value = sys.stdin.buffer.readline(129)
            need(
                value == b'{"event":"local_neon_action_finished_reobserve_native"}\n',
                "cutover_local_barrier_not_completed",
            )

        try:
            value = decode_input(sys.stdin.buffer.readline(131074))
            need(WORK.is_dir() and WORK.stat().st_uid == 0, "cutover_private_volume_owner")
            WORK.chmod(0o700)
            need(
                sys.platform == "linux"
                and WORK.is_dir()
                and WORK.stat().st_uid == 0
                and WORK.stat().st_mode & 0o777 == 0o700,
                "cutover_linux_private_owner_required",
            )
            boundary = native.NativeFreshFenceBoundary(
                ROOT,
                TARGET,
                native.SDKGoogleReads(
                    Path("/google-cloud-sdk/lib"), "harshilyogeshbhai886@gmail.com"
                ),
                linux_execution_files=LinuxExecutionFiles(),
            )
            event(
                preflight(value, boundary)
                if value["operation"] == "preflight"
                else execute(value, boundary, event, barrier)
            )
        except BaseException:
            os.write(
                protocol,
                b'{"event":"cutover_stopped_reconcile_only","automatic_replay":false,"goal_complete":false}\n',
            )
            raise SystemExit(2) from None
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            os.close(protocol)


if __name__ == "__main__":
    main()
