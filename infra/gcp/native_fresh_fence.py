"""Fresh-only native credential fence; no mutation or assertion-file admission.

The retained census is explicit and runs once, before the retirement action.
Repeated checks reread only current authority, never the historical census.
Unsupported relevant model issuers refuse rather than produce a partial Fence.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from threading import local
from typing import Any, cast
from urllib.parse import urlencode

import fresh_monetary_release as fresh
import monetary_release as release

PROJECT_NUMBER = "157225724590"
PARENT = f"projects/{release.PROJECT}/locations/{release.REGION}"
MAX_BYTES = 4 * 1024 * 1024
MAX_READS = 128
WINDOW = 30
KEY = re.compile(r"projects/([1-9][0-9]{0,19})/locations/global/keys/[A-Za-z0-9_-]{1,63}")
CONTAINER = re.compile(r"(?:projects|folders|organizations)/[1-9][0-9]{0,19}")
SETUP_REFERENCES = {
    "replacement_runtime": "hirewiz-fresh-runtime-db-20261013:1",
    "replacement_migration": "hirewiz-fresh-migrator-db-20261013:1",
    "observer": "hirewiz-fresh-observer-db-20261013:1",
}
SA_EMAIL = re.compile(r"[a-z0-9_-]{1,63}@[a-z0-9.-]{1,100}\.gserviceaccount\.com")


def need(value: Any, code: str) -> None:
    if not value:
        raise release.ReleaseDenied(code)


def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        need(key not in result, "fence_duplicate_native_json_key")
        result[key] = value
    return result


class SDKGoogleReads:
    """Normal retained SDK manager; read-only, canonical origins, no token reads.

    Each thread owns its stock HTTP transport. The SDK may refresh the retained
    manager and replay its normal authentication challenge; no operator retry.
    This does not create a login, enable an API or install the SDK.
    """

    def __init__(self, sdk_library: Path, account: str):
        need(sdk_library.is_absolute(), "fence_sdk_location_invalid")
        self.sdk_library, self.account = sdk_library, account
        self.thread = local()

    def _http(self) -> Any:
        if not hasattr(self.thread, "http") or self.thread.pid != os.getpid():
            sys.path[:0] = [str(self.sdk_library), str(self.sdk_library / "third_party")]
            from googlecloudsdk.core import properties
            from googlecloudsdk.core.credentials import transports

            p = properties.VALUES
            need(
                p.core.account.Get() == self.account
                and p.core.project.Get() == release.PROJECT
                and p.core.universe_domain.Get() == "googleapis.com"
                and p.core.disable_file_logging.GetBool() and not p.core.log_http.GetBool()
                and not p.core.custom_ca_certs_file.Get()
                and not p.auth.disable_ssl_validation.GetBool()
                and not p.auth.disable_credentials.GetBool()
                and not p.auth.credential_file_override.Get()
                and not p.auth.access_token_file.Get()
                and p.auth.token_host.Get() in (None, "", "https://oauth2.googleapis.com/token")
                and not p.auth.impersonate_service_account.Get()
                and not p.proxy.address.Get() and not p.proxy.port.Get()
                and not p.proxy.proxy_type.Get(),
                "fence_normal_manager_configuration_refused",
            )
            self.thread.http = transports.GetApitoolsTransport(
                timeout=3, enable_resource_quota=False,
                allow_account_impersonation=False,
                redact_request_body_reason="native HireWiz retirement metadata",
            )
            self.thread.http.session.max_redirects = 0
            self.thread.http.session.trust_env = False
            self.thread.pid = os.getpid()
        return self.thread.http

    def get(self, api: str, path: str, params: Mapping[str, Any]) -> dict[str, Any]:
        origins = {
            "run": "https://run.googleapis.com/v2/",
            "project": "https://cloudresourcemanager.googleapis.com/v3/",
            "secret": "https://secretmanager.googleapis.com/v1/",
            "keys": "https://apikeys.googleapis.com/v2/",
            "iam": "https://iam.googleapis.com/v1/",
            "tasks": "https://cloudtasks.googleapis.com/v2/",
            "scheduler": "https://cloudscheduler.googleapis.com/v1/",
        }
        need(api in origins and not path.startswith("/") and ".." not in path,
             "fence_native_route_refused")
        allowed = (
            api == "run" and re.fullmatch(re.escape(PARENT) + r"/(?:services|jobs)(?:/(?:[a-z0-9-]+|-)(?:/(?:revisions|executions)(?:/[a-z0-9-]+)?)?)?", path)
            or api == "project" and (CONTAINER.fullmatch(path) or path == "projects/" + release.PROJECT)
            or api == "secret" and re.fullmatch(r"projects/" + PROJECT_NUMBER + r"/secrets/[A-Za-z0-9_-]{1,255}/versions/[1-9][0-9]{0,18}(?::access)?", path)
            or api == "keys" and (KEY.fullmatch(path) or path == "keys:lookupKey")
            or api == "iam" and re.fullmatch(r"projects/-/serviceAccounts/" + SA_EMAIL.pattern, path)
            or api == "tasks" and re.fullmatch(re.escape(PARENT) + r"/queues/hirewiz-[a-z-]+(?:/tasks)?", path)
            or api == "scheduler" and path == PARENT + "/jobs"
        )
        need(allowed, "fence_native_route_refused")
        url = origins[api] + path + ("?" + urlencode(params, doseq=True) if params else "")
        try:
            response, raw = self._http().request(
                url, method="GET", body=None, headers={"Cache-Control": "no-cache"},
                redirections=0,
            )
            need(int(response.status) == 200 and type(raw) is bytes and len(raw) <= MAX_BYTES,
                 "fence_native_read_unavailable")
            value = json.loads(raw, object_pairs_hook=object_pairs)
            need(type(value) is dict, "fence_native_response_invalid")
            return value
        except release.ReleaseDenied:
            raise
        except Exception:
            raise release.ReleaseDenied("fence_native_read_unavailable") from None


@dataclass(frozen=True)
class NativeKey:
    name: str
    uid: str
    issuer: str | None


@dataclass(frozen=True)
class NativePrincipal:
    email: str
    uid: str


@dataclass(frozen=True)
class BoundScope:
    """Created only by bind_authorities_once from authenticated native results."""
    source: release.Plan
    target: release.FreshTargetPlan
    service_uids: tuple[tuple[str, str], ...]
    keys: tuple[NativeKey, ...]
    new_key: NativeKey | None
    principals: tuple[NativePrincipal, ...]
    retired_source_actors: tuple[str, ...]


class NativeFreshFenceBoundary(fresh.FreshNativeBoundary):
    """Positive native composition for fresh orchestration, never in-place DDL."""

    def __init__(
        self,
        root: Path,
        target: release.FreshTargetPlan,
        reads: SDKGoogleReads,
        **native_options: Any,
    ):
        super().__init__(root, **native_options)
        self.target, self.reads = target, reads
        self.scope: BoundScope | None = None
        self.retired_since: datetime | None = None
        self._setup_passwords: dict[str, str] = {}
        self._source_old_passwords: tuple[str, ...] = ()
        self._source_isolated_at: datetime | None = None
        self._authenticated_replacements: tuple[str, str, str] | None = None

    def migrate(self, plan: release.Plan, expected: str, target: str) -> None:
        raise release.ReleaseDenied("fresh_native_fence_cannot_authorize_inplace_migration")

    def _get(self, api: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.reads.get(api, path, {} if params is None else params)

    def _key(self, name: str, module: Any) -> NativeKey:
        need(KEY.fullmatch(name), "fence_key_resource_unmapped")
        raw = self._get("keys", name)
        metadata = module._metadata(raw, name.rsplit("/keys/", 1)[0], datetime.now(UTC))
        issuer = raw.get("serviceAccountEmail") or None
        need(issuer is None or type(issuer) is str and SA_EMAIL.fullmatch(issuer),
             "fence_key_issuer_identity_unmapped")
        return NativeKey(metadata["name"], metadata["uid"], issuer)

    def bind_authorities_once(self, source: release.Plan) -> None:
        """Explicit pre-cut acquisition; not invoked by verify_fences.

        It reuses the existing complete retained collector and its UID/config
        drift checks. Credentials/configs remain RAM-only. No supplied scope
        file, accepted booleans, assumed provider actor or historic-host redirect.
        """
        need(self.scope is None, "fence_authority_scope_already_bound")
        source.validate()
        self.target.validate(source)
        inventory_module = self._module(
            "backend/scripts/legacy_credential_inventory.py", "hirewiz_native_fence_retained"
        )
        key_module = self._module(
            "backend/scripts/model_key_revocation_observer.py", "hirewiz_native_fence_keys"
        )
        captured: dict[tuple[str, str], dict[str, Any]] = {}
        owner = self

        class Capture:
            def get(self, api: str, path: str, params: dict[str, str]) -> dict[str, Any]:
                value = owner._get(api, path, params)
                if api == "run" and not params:
                    captured[(api, path)] = value
                return value

        retained = inventory_module.collect_retained_credentials(Capture())
        relevant_resources: set[str] = set()
        neon = self._module("backend/scripts/neon_direct_identity.py", "hirewiz_native_fence_neon")
        for credential in retained.credentials:
            if credential.kind == "model":
                relevant_resources.update(origin.resource for origin in credential.origins)
            elif inventory_module.make_url(credential.value).host in {
                neon.RELEASE_DIRECT_HOST,
                neon.RELEASE_POOLER_HOST,
            }:
                relevant_resources.update(origin.resource for origin in credential.origins)
        # SQL closure independently covers every selected-source application
        # writer. A historical DSN alone grants no new-cluster/model authority.
        # A model alias or a mount in a relevant credential producer is unresolved.
        unresolved = [
            row
            for row in retained.unresolved
            if (
                row.origin.environment_name in inventory_module.MODEL_NAMES
                or row.origin.environment_name
                and not row.origin.environment_name.startswith(
                    ("DB_", "DATABASE_", "POSTGRES_", "PG_")
                )
                or not row.origin.environment_name
                and row.origin.resource in relevant_resources
            )
        ]
        need(not unresolved, "fence_relevant_model_credential_source_unmapped")
        old_keys: dict[str, NativeKey] = {}
        for credential in retained.credentials:
            if credential.kind != "model":
                continue
            names = {origin.environment_name for origin in credential.origins}
            need(
                names <= {"LLM_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"},
                "fence_non_google_model_issuer_native_retirement_unavailable",
            )
            need(
                re.fullmatch(r"AIza[A-Za-z0-9_-]{35}", credential.value),
                "fence_model_issuer_native_binding_unavailable",
            )
            lookup = self._get("keys", "keys:lookupKey", {"keyString": credential.value})
            name = lookup.get("name")
            need(
                type(name) is str
                and KEY.fullmatch(name)
                and lookup.get("parent") == name.rsplit("/keys/", 1)[0],
                "fence_model_credential_native_resource_unmapped",
            )
            key = self._key(cast(str, name), key_module)
            old_keys[key.name] = key
        need(1 <= len(old_keys) <= 32, "fence_model_credential_scope_unavailable")
        emails = {key.issuer for key in old_keys.values() if key.issuer}
        service_uids: list[tuple[str, str]] = []
        for kind, name, uid in retained.resources:
            raw = captured[("run", name)]
            config = inventory_module._config(raw, kind)
            if name in relevant_resources:
                email = config.get("serviceAccount")
                need(
                    type(email) is str and SA_EMAIL.fullmatch(email),
                    "fence_retained_automated_principal_unmapped",
                )
                emails.add(email)
            if kind == "service" and name.rsplit("/", 1)[-1] in release.SERVICES:
                service_uids.append((name, uid))
        need(
            1 <= len(emails) <= 16 and len(service_uids) <= 16,
            "fence_relevant_authority_bound_exceeded",
        )
        need(
            not emails & {source.runtime_service_account, source.migration_service_account},
            "fence_old_and_new_automated_principals_overlap",
        )
        principals: list[NativePrincipal] = []
        for email in sorted(emails):
            raw = self._get("iam", "projects/-/serviceAccounts/" + email)
            uid = raw.get("uniqueId")
            need(
                raw.get("email") == email
                and type(uid) is str
                and re.fullmatch(r"[1-9][0-9]{5,24}", uid),
                "fence_native_principal_identity_unmapped",
            )
            principals.append(NativePrincipal(email, cast(str, uid)))
        observer = self._module(
            "backend/scripts/monetary_cutover_preflight.py", "hirewiz_native_fence_source"
        )
        inventory, _ = observer.load_inventory(
            source.inventory_path, source.inventory_sha256, datetime.now(UTC)
        )
        roles = {str(alias): os.environ[name] for alias, name in inventory.role_env.items()}
        operators = {os.environ[name] for name in inventory.database_operator_role_env}
        # Names come from actual captured source DSNs, not the classification
        # file. SQL authority/OIDs/ownership are freshly checked only after cut.
        old_actors = tuple(
            sorted(
                {
                    inventory_module.make_url(credential.value).username
                    for credential in retained.credentials
                    if credential.kind == "database"
                    and inventory_module.make_url(credential.value).host
                    in {neon.RELEASE_DIRECT_HOST, neon.RELEASE_POOLER_HOST}
                }
            )
        )
        need(old_actors and not set(old_actors) & operators, "fence_legacy_actor_operator_overlap")
        need(
            set(old_actors) <= set(roles.values()),
            "fence_captured_source_actor_missing_sql_registry",
        )
        self._source_old_passwords = tuple(
            {
                inventory_module.make_url(credential.value).password
                for credential in retained.credentials
                if credential.kind == "database"
                and inventory_module.make_url(credential.value).host
                in {neon.RELEASE_DIRECT_HOST, neon.RELEASE_POOLER_HOST}
                and type(inventory_module.make_url(credential.value).password) is str
            }
        )
        need(self._source_old_passwords, "fence_actual_source_precut_passwords_unavailable")
        self._capture_setup_passwords(neon)
        self.scope = BoundScope(
            source,
            self.target,
            tuple(sorted(service_uids)),
            tuple(old_keys.values()),
            None,
            tuple(principals),
            old_actors,
        )
        retained = None
        captured.clear()

    def _capture_setup_passwords(self, neon: Any) -> None:
        """Actual pre-cut Secret Manager access; values never leave this process."""
        need(self.retired_since is None and not self._setup_passwords,
             "fence_precut_password_capture_not_available")
        captured: dict[str, str] = {}
        for alias, reference in SETUP_REFERENCES.items():
            _, target = neon.collect_fresh_release_target(
                self, reference, alias=alias, database_owner_oid=neon.FRESH_DATABASE_OWNER_OID,
                expected_schema=release.CUTOVER_CHAIN[0],
            )
            captured[alias] = target.dump_environment()["PGPASSWORD"]
        self._setup_passwords = captured

    def _authenticate_empty_replacement(self, neon: Any, binding: Any, target: Any) -> None:
        """Genuine role auth on the fixed empty target; no pre-restore schema marker.

        The issued transport authenticates and proves effective verify-full TLS
        before dialect SQL. These catalog reads retain the exact native target
        and role proof but intentionally do not call verify_release_schema.
        """
        engine = None
        try:
            engine = target.engine()
            with engine.connect() as connection, connection.begin():
                connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                connection.exec_driver_sql("SET LOCAL search_path=pg_catalog")
                connection.exec_driver_sql("SET LOCAL statement_timeout='1000ms'")
                neon.verify_transport(connection, binding)
                control = connection.execute(neon.text(
                    "SELECT p.oid,p.proowner,p.prosecdef,p.prosrc,l.lanname FROM pg_catalog.pg_proc p "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
                    "JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
                    "WHERE n.nspname='pg_catalog' AND p.proname='pg_control_system' AND p.pronargs=0 LIMIT 2"
                )).all()
                need([tuple(row) for row in control] == [(3441, 10, False, "pg_control_system", "internal")],
                     "fence_replacement_native_control_mismatch")
                row = connection.execute(neon.text(
                    "SELECT d.oid,d.datdba,r.oid,current_user,session_user,n.oid,n.nspowner,"
                    "(SELECT system_identifier::text FROM pg_catalog.pg_control_system()) "
                    "FROM pg_catalog.pg_database d JOIN pg_catalog.pg_roles r ON r.rolname=current_user "
                    "JOIN pg_catalog.pg_namespace n ON n.nspname='public' "
                    "WHERE d.datname=pg_catalog.current_database()"
                )).one()
                need(tuple(row[:7]) == (neon.FRESH_DATABASE_OID, neon.FRESH_DATABASE_OWNER_OID,
                     binding.actor_oid, binding.actor, binding.actor, neon.NAMESPACE_OID,
                     neon.FRESH_NAMESPACE_OWNER_OID) and type(row[7]) is str
                     and hashlib.sha256(row[7].encode()).hexdigest() == neon.FRESH_CLUSTER,
                     "fence_replacement_native_target_mismatch")
                counts = connection.execute(neon.text(
                    "SELECT (SELECT count(*) FROM pg_catalog.pg_class c "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'),"
                    "(SELECT count(*) FROM pg_catalog.pg_proc p "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'),"
                    "(SELECT count(*) FROM pg_catalog.pg_stat_activity WHERE usename IN "
                    "('hirewiz_runtime_20261013_r1','hirewiz_migrator_20261013_r1','hirewiz_observer_20261013_r1') "
                    "AND pid <> pg_catalog.pg_backend_pid())"
                )).one()
                need(tuple(counts) == (0, 0, 0), "fence_replacement_target_not_empty_or_sessions_present")
        except release.ReleaseDenied:
            raise
        except Exception:
            raise release.ReleaseDenied("fence_replacement_native_authentication_unavailable") from None
        finally:
            if engine is not None:
                engine.dispose()

    def _bind_distinct_replacement_passwords(self, target: release.FreshTargetPlan) -> None:
        need(set(self._setup_passwords) == set(SETUP_REFERENCES),
             "fence_actual_precut_passwords_unavailable")
        neon = self._module("backend/scripts/neon_direct_identity.py", "hirewiz_native_fence_replacement_neon")
        references = (target.runtime_secret, target.migration_secret, target.observer_secret)
        candidates = []
        for alias, reference in zip(SETUP_REFERENCES, references, strict=True):
            need(reference.split(":")[0] == SETUP_REFERENCES[alias].split(":")[0],
                 "fence_replacement_secret_name_drift")
            binding, observed = neon.collect_fresh_release_target(
                self, reference, alias=alias, database_owner_oid=neon.FRESH_DATABASE_OWNER_OID,
                expected_schema=release.CUTOVER_CHAIN[0],
            )
            password = observed.dump_environment()["PGPASSWORD"]
            need(all(not hmac.compare_digest(password.encode(), old_password.encode())
                     for old_password in (*getattr(self, "_source_old_passwords", ()),
                                          *self._setup_passwords.values())),
                 "fence_replacement_reuses_precut_password")
            candidates.append((binding, observed))
        # Prove every payload is distinct before authenticating any replacement.
        # These connections run once here, before restore; migrator is retired
        # after migration and is deliberately not authenticated by later fences.
        for binding, observed in candidates:
            self._authenticate_empty_replacement(neon, binding, observed)
        self._authenticated_replacements = references
        self._setup_passwords.clear()
        self._source_old_passwords = ()

    def describe_release_secret(self, reference: str) -> dict[str, Any]:
        need(re.fullmatch(r"[a-z][a-z0-9-]{2,62}:[1-9][0-9]{0,8}", reference),
             "fence_exact_secret_reference_invalid")
        name, version = reference.split(":")
        path = f"projects/{PROJECT_NUMBER}/secrets/{name}/versions/{version}"
        value = self._get("secret", path)
        need(value.get("name") == path, "fence_exact_secret_identity_drift")
        return value

    def access_release_secret(self, reference: str) -> str:
        need(re.fullmatch(r"[a-z][a-z0-9-]{2,62}:[1-9][0-9]{0,8}", reference),
             "fence_exact_secret_reference_invalid")
        module = self._module("backend/scripts/legacy_credential_inventory.py", "hirewiz_native_fence_secret")
        name, version = reference.split(":")
        _, value = module._secret_value(self.reads, f"projects/{PROJECT_NUMBER}/secrets/{name}/versions/{version}")
        need(value is not None, "fence_source_observer_secret_unavailable")
        return value

    def verify_admission_closed(self) -> None:
        """Same native predicates as the existing observer, with fixed SDK GETs.

        Complete lists are mandatory: no limit flag that silently truncates a
        nonempty page. Any queued task, schedule or incomplete execution refuses.
        """
        calls: list[tuple[str, str, dict[str, Any]]] = []
        for queue in release.QUEUES:
            path = PARENT + "/queues/" + queue
            calls.extend((("tasks", path, {}), ("tasks", path + "/tasks", {"pageSize": "100"})))
        calls.extend((("scheduler", PARENT + "/jobs", {"pageSize": "100"}),
                      ("run", PARENT + "/jobs/-/executions", {"pageSize": "100"})))
        with ThreadPoolExecutor(max_workers=4) as pool:
            values = list(pool.map(lambda call: self._get(*call), calls))
        for index, queue in enumerate(release.QUEUES):
            path = PARENT + "/queues/" + queue
            row, tasks = values[2 * index:2 * index + 2]
            entries = tasks.get("tasks", [])
            need(row.get("name") == path and row.get("state") == "PAUSED"
                 and type(entries) is list and entries == []
                 and tasks.get("nextPageToken") in (None, ""),
                 "fence_queue_not_paused_and_drained")
        for row, field in zip(values[-2:], ("jobs", "executions"), strict=True):
            entries = row.get(field, [])
            need(type(entries) is list and len(entries) <= 100 and row.get("nextPageToken") in (None, "")
                 and not row.get("unreachable"), "fence_admission_inventory_partial")
            need(all(type(item) is dict and (
                item.get("state") == "PAUSED" if field == "jobs" else item.get("completionTime")
            ) for item in entries), "fence_schedule_or_execution_not_drained")

    def begin_source_observer_isolation(self) -> None:
        """Conservative cloud-only issuance boundary; never a full writer Fence."""
        from fresh_source_authority import verify_cloud_isolated

        need(
            self.scope is not None
            and self._source_isolated_at is None
            and self.retired_since is None,
            "fence_source_observer_isolation_not_available",
        )
        verify_cloud_isolated(self)
        self._source_isolated_at = datetime.now(UTC)

    def bind_source_observer_after_isolation(self, source: release.Plan) -> None:
        """One explicit source-ref transition, with native readback and auth.

        Scope IDs/OIDs/owner/inventory and all prior authority stay immutable.
        Full source retirement is observed only after provider role deletion.
        """
        scope, cut = self.scope, self._source_isolated_at
        need(
            scope is not None
            and cut is not None
            and self.retired_since is None
            and not scope.source.observer_secret,
            "fence_source_observer_rebind_not_available",
        )
        assert scope is not None and cut is not None
        source.validate()
        need(
            type(source) is fresh.FreshSourcePlan
            and replace(source, observer_secret=scope.source.observer_secret) == scope.source
            and re.fullmatch(
                r"hirewiz-source-observer-db-20261014:[1-9][0-9]{0,8}", source.observer_secret
            ),
            "fence_source_observer_plan_or_name_drift",
        )
        metadata = self.describe_release_secret(source.observer_secret)
        try:
            created = datetime.fromisoformat(
                str(metadata.get("createTime", "")).replace("Z", "+00:00")
            )
        except (ValueError, TypeError):
            raise release.ReleaseDenied("fence_source_observer_issuance_unavailable") from None
        need(
            metadata.get("state") == "ENABLED"
            and created.tzinfo is not None
            and cut <= created <= datetime.now(UTC),
            "fence_source_observer_issued_before_isolation",
        )
        neon = self._module(
            "backend/scripts/neon_direct_identity.py", "hirewiz_native_source_rebind_neon"
        )
        binding, target = neon.collect_source_release_target(
            self,
            source.observer_secret,
            alias="observer",
            database_owner_oid=source.database_owner_oid,
            expected_schema=release.CUTOVER_CHAIN[0],
        )
        password = target.dump_environment()["PGPASSWORD"]
        old = (*self._source_old_passwords, *self._setup_passwords.values())
        need(
            old
            and all(not hmac.compare_digest(password.encode(), value.encode()) for value in old),
            "fence_source_observer_reuses_precut_password",
        )
        engine = None
        try:
            engine = target.engine()
            with engine.connect() as connection, connection.begin():
                connection.exec_driver_sql(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
                connection.exec_driver_sql("SET LOCAL search_path=pg_catalog")
                connection.exec_driver_sql("SET LOCAL statement_timeout='1000ms'")
                neon.verify_transport(connection, binding)
                neon.verify_native_target(connection, binding, 16391)
                module = self._module(
                    "backend/scripts/monetary_cutover_preflight.py", "hirewiz_source_rebind_session"
                )
                module._native_session_ids(connection, 16391, neon_binding=binding)
                absence = connection.execute(
                    neon.text(
                        "SELECT "
                        "(SELECT count(*) FROM pg_catalog.pg_roles WHERE oid=16387 OR rolname='neondb_owner'),"
                        "(SELECT count(*) FROM pg_catalog.pg_auth_members WHERE roleid=16387 OR member=16387 OR grantor=16387),"
                        "(SELECT count(*) FROM pg_catalog.pg_stat_activity WHERE usesysid=16387)"
                    )
                ).one()
                need(
                    tuple(absence) == (0, 0, 0),
                    "fence_source_observer_old_sql_authority_still_present",
                )
                fresh._source_owner_closed(connection, source.database_owner_oid, neon.text)
        except release.ReleaseDenied:
            raise
        except Exception:
            raise release.ReleaseDenied(
                "fence_source_observer_native_authentication_unavailable"
            ) from None
        finally:
            if engine is not None:
                engine.dispose()
        self.scope = replace(scope, source=source)
        # Retain every actual source/temporary-observer value until all three
        # target comparisons and genuine authentication have completed.

    def bind_replacements_after_isolation(self, source: release.Plan,
                                          target: release.FreshTargetPlan) -> None:
        """Native issuance after isolation rules out cached old-holder passwords.

        The :1 setup receipt is historical provenance, not present authority.
        This method changes only in-memory numeric replacement references; it
        does not rotate, write, disable a version or change source credentials.
        """
        scope = self.scope
        if scope is None or self.retired_since is None:
            raise release.ReleaseDenied("fence_old_authority_isolation_not_established")
        source.validate()
        target.validate(source)
        need(replace(source, model_key_secret=scope.source.model_key_secret) == scope.source,
             "fence_source_plan_drift")
        inventory_module = self._module("backend/scripts/legacy_credential_inventory.py", "hirewiz_native_fence_retained")
        key_module = self._module("backend/scripts/model_key_revocation_observer.py", "hirewiz_native_fence_keys")
        name, version = source.model_key_secret.split(":")
        reference = f"projects/{PROJECT_NUMBER}/secrets/{name}/versions/{version}"
        _, value = inventory_module._secret_value(self.reads, reference)
        need(value is not None, "fence_replacement_model_secret_unavailable")
        lookup = self._get("keys", "keys:lookupKey", {"keyString": value})
        value = None
        need(type(lookup.get("name")) is str and KEY.fullmatch(lookup["name"]),
             "fence_replacement_model_key_unmapped")
        key = self._key(lookup["name"], key_module)
        need(key.name not in {item.name for item in scope.keys}
             and key.issuer not in {item.email for item in scope.principals},
             "fence_replacement_model_key_reuses_retired_authority")
        candidate = replace(scope, source=source, target=target, new_key=key)
        self._verify_issuance_after_isolation(candidate, key_module)
        self._bind_distinct_replacement_passwords(target)
        self.scope, self.target = candidate, target

    def _verify_issuance_after_isolation(self, scope: BoundScope, module: Any) -> None:
        cut = self.retired_since
        if cut is None or scope.new_key is None:
            raise release.ReleaseDenied("fence_replacement_native_binding_unavailable")
        paths = []
        for ref in (scope.target.runtime_secret, scope.target.migration_secret,
                    scope.target.observer_secret, scope.source.model_key_secret):
            name, version = ref.split(":")
            paths.append(("secret", f"projects/{PROJECT_NUMBER}/secrets/{name}/versions/{version}"))
        paths.append(("keys", scope.new_key.name))
        for api, path in paths:
            raw = self._get(api, path)
            normalized = module._timestamp(raw.get("createTime"), datetime.now(UTC))
            need(raw.get("name") == path and normalized is not None,
                 "fence_replacement_issuance_unavailable")
            created = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
            need(created >= cut,
                 "fence_replacement_credential_issued_before_native_isolation")

    def _cloud_closure(self, scope: BoundScope) -> None:
        inventory_module = self._module("backend/scripts/legacy_credential_inventory.py", "hirewiz_native_fence_retained")
        for path, uid in scope.service_uids:
            raw = self._get("run", path)
            need(raw.get("name") == path and raw.get("uid") == uid,
                 "fence_current_service_uid_drift")
            # Service template is prospective authority too, even if traffic0.
            configs = [inventory_module._config(raw, "service")]
            traffic = raw.get("trafficStatuses", [])
            need(type(traffic) is list and len(traffic) <= 64,
                 "fence_route_inventory_unavailable")
            for route in traffic:
                need(type(route) is dict and type(route.get("revision")) is str,
                     "fence_routed_revision_unmapped")
                revision = route["revision"]
                revision_path = revision if "/" in revision else path + "/revisions/" + revision
                value = self._get("run", revision_path)
                need(value.get("name") == revision_path,
                     "fence_routed_revision_identity_drift")
                configs.append(inventory_module._config(value, "revision"))
            for config in configs:
                email = config.get("serviceAccount")
                if email in {item.email for item in scope.principals}:
                    continue  # Exact old actor is independently observed disabled.
                need(email == scope.source.runtime_service_account,
                     "fence_new_reachable_principal_unmapped")
                containers = config["containers"]
                need(len(containers) == 1 and containers[0].get("image") == scope.source.image,
                     "fence_reachable_release_image_unmapped")
                env = containers[0].get("env", [])
                names = [row.get("name") for row in env if type(row) is dict]
                need(len(names) == len(env) == len(set(names)), "fence_reachable_env_unmapped")
                values = {row["name"]: row.get("value") for row in env}
                need(all(values.get(key) == release.SAFE_ENV[key] for key in (
                    "OPTIONAL_AI_GENERATION_ENABLED", "EMPLOYER_AUTO_SUBMIT_ENABLED",
                    "RAZORPAY_CHECKOUT_ENABLED", "AUTO_DB_MIGRATE")),
                    "fence_new_reachable_admission_not_closed")
        # Native admission separately proves paused/drained queues, scheduler
        # state and completed executions; SQL closure is observed in parallel.
        self.verify_admission_closed()

    def _observe_closure(self, source: release.Plan, *, replacements: bool) -> datetime:
        scope = self.scope
        if scope is None:
            raise release.ReleaseDenied("fence_native_scope_not_bound_or_plan_drift")
        need(scope.source == source and scope.target == self.target,
             "fence_native_scope_not_bound_or_plan_drift")
        started, deadline = datetime.now(UTC), time.monotonic() + WINDOW
        key_module = self._module("backend/scripts/model_key_revocation_observer.py", "hirewiz_native_fence_keys")
        calls: list[tuple[str, str, dict[str, Any]]] = []
        keys = scope.keys + ((scope.new_key,) if replacements and scope.new_key else ())
        need(not replacements or scope.new_key is not None,
             "fence_replacement_native_binding_unavailable")
        need(not replacements or self._authenticated_replacements == (
            scope.target.runtime_secret, scope.target.migration_secret, scope.target.observer_secret),
            "fence_replacement_native_authentication_not_bound")
        for key in keys:
            calls.append(("keys", key.name, {}))
        for principal in scope.principals:
            calls.append(("iam", "projects/-/serviceAccounts/" + principal.email, {}))
        refs = (scope.target.runtime_secret, scope.target.observer_secret) if replacements else ()
        for ref in refs:
            name, version = ref.split(":")
            calls.append(("secret", f"projects/{PROJECT_NUMBER}/secrets/{name}/versions/{version}", {}))
        need(len(calls) <= MAX_READS, "fence_fresh_read_bound_exceeded")
        with ThreadPoolExecutor(max_workers=4) as pool:
            sql = pool.submit(fresh.FreshNativeBoundary.verify_database, self, source, release.CUTOVER_CHAIN[0])
            fleet = pool.submit(self._cloud_closure, scope)
            futures = [pool.submit(self._get, *call) for call in calls]
            try:
                values = [future.result(timeout=max(0.001, deadline - time.monotonic())) for future in futures]
                sql.result(timeout=max(0.001, deadline - time.monotonic()))
                fleet.result(timeout=max(0.001, deadline - time.monotonic()))
            except TimeoutError:
                # A concurrent wait may expire before the final freshness check.
                # Refuse with the same fixed code; never refresh the start time.
                raise release.ReleaseDenied("fence_native_observation_expired") from None
        need(time.monotonic() < deadline, "fence_native_observation_expired")
        offset = 0
        for key in keys:
            raw = values[offset]
            offset += 1
            metadata = key_module._metadata(raw, key.name.rsplit("/keys/", 1)[0], datetime.now(UTC))
            need(metadata["name"] == key.name and metadata["uid"] == key.uid
                 and (raw.get("serviceAccountEmail") or None) == key.issuer,
                 "fence_native_key_uid_or_issuer_drift")
            need((metadata["delete_time"] is not None) == (key != scope.new_key),
                 "fence_old_provider_key_active_or_replacement_retired")
        for principal in scope.principals:
            raw = values[offset]
            offset += 1
            need(raw.get("email") == principal.email and raw.get("uniqueId") == principal.uid,
                 "fence_native_principal_uid_drift")
            need(raw.get("disabled") is True, "fence_old_automated_principal_still_enabled")
        for ref in refs:
            raw = values[offset]
            offset += 1
            name, version = ref.split(":")
            need(raw.get("name") == f"projects/{PROJECT_NUMBER}/secrets/{name}/versions/{version}"
                 and raw.get("state") == "ENABLED", "fence_exact_replacement_secret_not_enabled")
        need(time.monotonic() < deadline, "fence_native_observation_expired")
        return started

    def establish_retirement_cut(self, source: release.Plan) -> datetime:
        """First complete native observation establishes conservative cutoff.

        It returns no Fence: completion follows the oldest observation. A new
        complete fresh read is required before backup/transfer admission.
        """
        need(self.retired_since is None, "fence_retirement_cut_already_established")
        self._observe_closure(source, replacements=False)
        self.retired_since = datetime.now(UTC)
        return self.retired_since

    def verify_fences(self, plan: release.Plan) -> release.Fence:
        cut = self.retired_since
        if cut is None:
            raise release.ReleaseDenied("fence_native_retirement_cut_not_established")
        try:
            observed = self._observe_closure(plan, replacements=True)
            need(cut <= observed, "fence_retirement_cut_after_oldest_observation")
            return release.Fence(observed, cut, True, True, True)
        except Exception:
            self.retired_since = None
            raise
