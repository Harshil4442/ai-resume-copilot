"""One Linux process retains native authority through the local Neon barrier.

No provider operations at import. Invoke only in the reviewed owned Docker
container after concrete cutover approval. Stdin credentials never enter files,
argv, receipts or stdout. The supported RAM wrapper supplies short-lived SDK
tokens in process memory; the host barrier is a wakeup, not proof.
"""

from __future__ import annotations

import ast
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
from functools import lru_cache
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
    LinuxExecutionFilesDenied,
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
RESUME_FIELD = "prepared_owner_resume"
TARGET = native.release.FreshTargetPlan(
    "hirewiz-fresh-runtime-db-20261013:1",
    "hirewiz-fresh-migrator-db-20261013:1",
    "hirewiz-fresh-observer-db-20261013:1",
)
WORK = Path("/operator-work")

STAGES = frozenset({
    "decode_input", "native_boundary", "readonly_preflight", "execute_admission",
    "execution_files", "empty_target", "registry_binding", "identity_and_policy",
    "admission_closed", "document_preparation", "original_source",
    "source_quiescence", "source_owner_prepare", "retained_authority_binding",
    "prepared_owner_pristine", "source_owner_resume", "old_principal_isolation",
    "old_model_retirement", "runtime_accounts", "runtime_policy_grants",
    "runtime_resource_grants", "source_observer_issue", "source_owner_transition",
    "provider_role_delete", "provider_role_reobserve", "observer_rotation",
    "source_retirement_cut", "target_rotation", "model_replacement",
    "replacement_binding", "transfer_migration_staging", "migrator_retirement",
    "traffic_promotion", "unclassified",
})
_BASE_STOPPED = {"event": "cutover_stopped_reconcile_only", "automatic_replay": False, "goal_complete": False}
_CODE = re.compile(r"[a-z][a-z0-9_]{3,159}\Z")


@lru_cache(maxsize=2)
def literal_codes(root: Path) -> frozenset[str]:
    """Only literals in the immutable checkout; observation cannot repair a gate."""
    codes: set[str] = set()
    try:
        if not root.is_absolute():
            return frozenset()
        for folder in (root / "infra/gcp", root / "backend/scripts"):
            for source in folder.glob("*.py"):
                if source.is_symlink() or source.stat().st_size > 1024 * 1024:
                    return frozenset()
                for node in ast.walk(ast.parse(source.read_bytes())):
                    if not isinstance(node, ast.Call):
                        continue
                    name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
                    if name in {"need", "require", "_need", "ensure"} or name.endswith(("Denied", "Error", "Violation")):
                        for argument in node.args:
                            if isinstance(argument, ast.Constant) and type(argument.value) is str and _CODE.fullmatch(argument.value):
                                codes.add(argument.value)
    except (OSError, SyntaxError, ValueError):
        return frozenset()
    return frozenset(codes)


def stage_event(stage: str) -> dict[str, Any]:
    return stage_projection({"event": "cutover_stage", "stage": stage})


def stage_projection(value: Any) -> dict[str, Any]:
    if (type(value) is not dict or set(value) != {"event", "stage"}
            or value.get("event") != "cutover_stage" or type(value.get("stage")) is not str
            or value["stage"] not in STAGES):
        raise ValueError("cutover_observability_projection_refused")
    return {"event": "cutover_stage", "stage": value["stage"]}


def stopped_event(error: BaseException, stage: str, codes: frozenset[str],
                  controlled_types: tuple[type[BaseException], ...]) -> dict[str, Any]:
    """Exact trusted exception classes and one known literal; no str(error)."""
    result = dict(_BASE_STOPPED)
    if (type(error) in controlled_types and len(error.args) == 1
            and type(error.args[0]) is str and error.args[0] in codes
            and _CODE.fullmatch(error.args[0])):
        result.update(stage=stage if type(stage) is str and stage in STAGES else "unclassified",
                      fixed_code=error.args[0])
        if (result["stage"] == "transfer_migration_staging"
                and result["fixed_code"] == "fresh_final_transfer_not_verified_reconcile_only"):
            try:
                result["transfer"] = fresh.transfer_projection(error.__dict__.get("transfer"))
            except ValueError:
                pass  # The fixed failure remains observable without raw diagnostics.
    return result


def stopped_projection(value: Any, codes: frozenset[str]) -> dict[str, Any]:
    if (type(value) is not dict or value.get("event") != _BASE_STOPPED["event"]
            or value.get("automatic_replay") is not False or value.get("goal_complete") is not False):
        raise ValueError("cutover_observability_projection_refused")
    if set(value) == set(_BASE_STOPPED):
        return dict(_BASE_STOPPED)
    fields = set(_BASE_STOPPED) | {"stage", "fixed_code"}
    if (set(value) not in (fields, fields | {"transfer"})
            or type(value.get("stage")) is not str or value["stage"] not in STAGES
            or type(value.get("fixed_code")) is not str or value["fixed_code"] not in codes
            or not _CODE.fullmatch(value["fixed_code"])):
        raise ValueError("cutover_observability_projection_refused")
    result = {**_BASE_STOPPED, "stage": value["stage"], "fixed_code": value["fixed_code"]}
    if (value["stage"] == "transfer_migration_staging"
            and value["fixed_code"] == "fresh_final_transfer_not_verified_reconcile_only"
            and "transfer" in value):
        try:
            result["transfer"] = fresh.transfer_projection(value["transfer"])
        except ValueError:
            pass  # Never reflect a malformed observation from the child protocol.
    return result


def need(value: Any, code: str) -> None:
    if not value:
        raise native.release.ReleaseDenied(code)


def decode_input(raw: bytes) -> dict[str, Any]:
    need(type(raw) is bytes and 0 < len(raw) <= 131072, "cutover_input_bound")
    try:
        value = json.loads(raw, object_pairs_hook=native.object_pairs)
    except Exception:
        raise native.release.ReleaseDenied("cutover_input_json_refused") from None
    need(type(value) is dict and frozenset(value) in {
        INPUT_FIELDS, INPUT_FIELDS | {RESUME_FIELD},
    }, "cutover_input_fields_refused")
    if RESUME_FIELD in value:
        source_sql.PreparedOwnerResume.from_payload(value[RESUME_FIELD])
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
    event(stage_event("execution_files"))
    files = LinuxExecutionFiles()
    files.validate()
    need(type(boundary) is native.NativeFreshFenceBoundary, "cutover_native_boundary_required")
    need(
        os.environ.get("HIREWIZ_OPERATOR_COMMIT") == value["release"],
        "cutover_image_commit_mismatch",
    )
    # Existing full Git/CI/image gate runs before old authority retirement.
    # Replacement identities are created only after captured issuers are isolated.
    event(stage_event("empty_target"))
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
    event(stage_event("registry_binding"))
    configure_registry(boundary, source, value["role_environment"])
    before_isolation = replace(
        source,
        runtime_service_account="hirewiz-api@" + writes.PROJECT + ".iam.gserviceaccount.com",
        migration_service_account=writes.TASKS_EMAIL,
    )
    event(stage_event("identity_and_policy"))
    boundary.verify_identity_and_policy(before_isolation)
    event(stage_event("admission_closed"))
    boundary.verify_admission_closed()
    event(stage_event("document_preparation"))
    boundary.verify_document_preparation(before_isolation)
    event(stage_event("original_source"))
    origin = source_sql.original_owner(boundary, files)
    source_sql.preflight_source(origin)
    event(stage_event("source_quiescence"))
    readiness = origin.connect()
    try:
        with readiness.transaction():
            readiness.execute("SET TRANSACTION READ ONLY")
            source_sql.verify_source_quiescent(boundary, readiness)
    finally:
        readiness.close()
    if RESUME_FIELD not in value:
        event(stage_event("source_owner_prepare"))
        owner = source_sql.prepare_owner(origin, WORK)
        source = replace(source, database_owner_oid=owner.oid)
        event(stage_event("retained_authority_binding"))
        boundary.bind_authorities_once(source)
    else:
        resume = source_sql.PreparedOwnerResume.from_payload(value[RESUME_FIELD])
        # The reviewed OID is a planned input until complete fresh native census
        # and the actual unused-role observation both succeed. No CREATE here.
        source = replace(source, database_owner_oid=resume.owner_oid)
        event(stage_event("retained_authority_binding"))
        boundary.bind_authorities_once(source)
        event(stage_event("prepared_owner_pristine"))
        writer.verify_prepared_owner_resume_pristine()
        event(stage_event("source_owner_resume"))
        owner = source_sql.resume_prepared_owner(origin, boundary, resume, WORK)
    event(
        {"event": "native_scope_bound", "source_database_oid": 16391, "source_owner_oid": owner.oid}
    )
    event(stage_event("old_principal_isolation"))
    writer.disable_old_principals()
    event(stage_event("old_model_retirement"))
    writer.delete_old_model_keys()
    boundary.begin_source_observer_isolation()
    event(stage_event("runtime_accounts"))
    accounts = writer.create_accounts()
    boundary.verify_identity_and_policy(source)
    event(stage_event("runtime_policy_grants"))
    writer.grant_attested_policy_sources(source, accounts[0])
    for (_, _, name), account in zip(writes.TARGET_ROLES, accounts, strict=True):
        writer.grant_secret_access(name, (account,))
    writer.grant_secret_access(writes.REDIS_SECRET, (accounts[0],))
    event(stage_event("runtime_resource_grants"))
    writer.grant_runtime_resources(accounts[0])
    passwords = source_sql.new_source_passwords()
    need(
        all(
            not writes.hmac.compare_digest(passwords.observer.encode(), old.encode())
            for old in (*boundary._source_old_passwords, *boundary._setup_passwords.values())
        ),
        "cutover_source_password_reuses_precut_value",
    )
    event(stage_event("source_observer_issue"))
    observer_ref = writes.publish_source_observer(writer, origin, passwords)
    event(stage_event("source_owner_transition"))
    transition = source_sql.transition_source(origin, owner, boundary, WORK, passwords)
    observer = source_sql.observer_target(origin, transition)
    writer.grant_secret_access(writes.SOURCE_SECRET, (accounts[2],))
    event(stage_event("provider_role_delete"))
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
    event(stage_event("provider_role_reobserve"))
    source_sql.verify_provider_delete_complete(observer, owner)
    event(stage_event("observer_rotation"))
    observer_ref, observer = writes.rotate_source_observer_after_delete(writer, observer, owner)
    source = replace(source, observer_secret=observer_ref)
    boundary.bind_source_observer_after_isolation(source)
    event(stage_event("source_retirement_cut"))
    boundary.establish_retirement_cut(source)
    event(stage_event("target_rotation"))
    target = writes.rotate_empty_target(writer, bootstrap)
    event(stage_event("model_replacement"))
    model = writer.create_model_replacement(accounts[0])
    writer.grant_secret_access(writes.MODEL_SECRET, (accounts[0],))
    source = replace(source, model_key_secret=model)
    event(stage_event("replacement_binding"))
    boundary.bind_replacements_after_isolation(source, target)
    writes.prepare_target_restore(writer, bootstrap)
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
    event(stage_event("transfer_migration_staging"))
    staged = fresh.stage_fresh_release(source, target, transfer, boundary, execute=True)
    need(
        staged.get("staging_complete") is True and staged.get("traffic_promoted") is False,
        "cutover_stage_result_shape",
    )
    event(stage_event("migrator_retirement"))
    writes.finalize_target_migrator(writer, bootstrap, source, target)
    event(stage_event("traffic_promotion"))
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

        last_stage = "decode_input"

        def event(value: dict[str, Any]) -> None:
            nonlocal last_stage
            if value.get("event") == "cutover_stage":
                value = stage_projection(value)
                last_stage = value["stage"]
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
            event(stage_event("decode_input"))
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
            event(stage_event("native_boundary"))
            boundary = native.NativeFreshFenceBoundary(
                ROOT,
                TARGET,
                native.SDKGoogleReads(
                    Path("/google-cloud-sdk/lib"), "harshilyogeshbhai886@gmail.com"
                ),
                linux_execution_files=LinuxExecutionFiles(),
            )
            event(stage_event("readonly_preflight" if value["operation"] == "preflight" else "execute_admission"))
            event(
                preflight(value, boundary)
                if value["operation"] == "preflight"
                else execute(value, boundary, event, barrier)
            )
        except BaseException as error:
            refusal = stopped_event(
                error, last_stage, literal_codes(ROOT),
                (native.release.ReleaseDenied, writes.WriteDenied, source_sql.SourceActionDenied,
                 writes.transfer.TransferDenied, LinuxExecutionFilesDenied),
            )
            os.write(protocol, json.dumps(refusal, separators=(",", ":")).encode() + b"\n")
            raise SystemExit(2) from None
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            os.close(protocol)


if __name__ == "__main__":
    main()
