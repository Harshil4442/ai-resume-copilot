"""Native-only adapter for a reviewed schema17 rolling release.

No admission-opening, migration, fresh transfer, role/key/password change, secret
publication, IAM grant, revision deletion, arbitrary transport/plugin, or retry.
This candidate has not been used against GCP or a database.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

sys.path[:0] = [str(Path(__file__).resolve().parent),
               str(Path(__file__).resolve().parents[2] / "backend")]
import fresh_cutover_host_auth as host_auth  # noqa: E402 -- explicit standalone module roots
import fresh_monetary_release as fresh  # noqa: E402
import native_fresh_fence as native  # noqa: E402
import rolling_release as rolling  # noqa: E402

release = rolling.release
FINANCIAL_TABLES = ("payment_orders", "payment_transactions", "payment_refunds", "payment_events",
    "entitlement_ledger", "usage_events", "service_credit_events", "service_credit_reservations",
    "model_cost_liabilities", "model_call_events", "analysis_runs")


class NativeRun:
    """Stock normal-manager auth; one physical PATCH send per explicitly armed call."""
    def __init__(self):
        self.stock = native.SDKGoogleReads(host_auth.SDK, host_auth.ACCOUNT)._http()
        from apitools.base.py import encoding
        from googlecloudsdk.generated_clients.apis.run.v2 import run_v2_client, run_v2_messages

        self.encoding, self.messages = encoding, run_v2_messages
        self.connections = self.stock.connections
        session = self.stock.session
        session.trust_env, session.max_redirects = False, 0
        for adapter in session.adapters.values():
            adapter.max_retries = adapter.max_retries.__class__(total=0, connect=0, read=0, redirect=0, status=0)
        self.armed: tuple[str, dict[str, Any], str] | None = None
        self.sent = 0
        original = session.send

        def send(request, **kwargs):
            target = urlsplit(request.url)
            path, query = unquote(target.path), parse_qs(target.query, strict_parsing=True)
            rolling.need(target.scheme == "https" and target.username is None and target.password is None
                         and target.port is None and not target.fragment)
            if target.hostname == "oauth2.googleapis.com":
                rolling.need(request.method == "POST" and path == "/token" and not query)
            else:
                rolling.need(target.hostname == "run.googleapis.com" and query.get("alt", ["json"]) == ["json"])
                resources = {"/v2/" + rolling.PARENT + "/services/" + service for service in release.SERVICES}
                if request.method == "PATCH":
                    rolling.need(self.armed is not None)
                    assert self.armed is not None
                    service, expected, mask = self.armed
                    rolling.need(path == "/v2/" + rolling.PARENT + "/services/" + service
                                 and self.sent == 0 and query.get("updateMask") == [mask]
                                 and set(query) <= {"alt", "updateMask", "allowMissing", "forceNewRevision", "validateOnly"}
                                 and all(query.get(key, ["false"])[0].lower() == "false"
                                         for key in ("allowMissing", "forceNewRevision", "validateOnly"))
                                 and json.loads(request.body) == expected)
                    self.sent += 1
                else:
                    rolling.need(request.method == "GET" and set(query) <= {"alt"}
                                 and (path in resources or any(re.fullmatch(re.escape(resource + "/revisions/")
                                      + r"[a-z0-9-]{1,63}", path) for resource in resources)
                                      or rolling.OPERATION.fullmatch(path.removeprefix("/v2/"))))
            kwargs["allow_redirects"] = False
            response = original(request, **kwargs)
            rolling.need(not 300 <= response.status_code < 400)
            return response

        session.send = send
        self.client = run_v2_client.RunV2(url="https://run.googleapis.com/", get_credentials=False,
            http=self, log_request=False, log_response=False)
        self.client.num_retries = 0

    def request(self, *args, **kwargs):
        kwargs["redirections"] = 0
        return self.stock.request(*args, **kwargs)

    def decode(self, value):
        result = self.encoding.MessageToDict(value)
        rolling.need(len(rolling.canonical(result)) <= 1048576)
        return result

    def service(self, service):
        rolling.need(service in release.SERVICES)
        return self.decode(self.client.projects_locations_services.Get(
            self.messages.RunProjectsLocationsServicesGetRequest(name=rolling.PARENT + "/services/" + service)))

    def revision(self, service, name):
        from apitools.base.py.exceptions import HttpNotFoundError
        rolling.need(service in release.SERVICES)
        name = rolling.short_revision(name, service)
        try:
            return self.decode(self.client.projects_locations_services_revisions.Get(
                self.messages.RunProjectsLocationsServicesRevisionsGetRequest(
                    name=rolling.PARENT + "/services/" + service + "/revisions/" + name)))
        except HttpNotFoundError:
            return None

    def patch(self, service, body, mask):
        rolling.need(service in release.SERVICES and mask in {"template,traffic", "traffic"}
                     and self.armed is None and type(body) is dict
                     and body.get("name") == rolling.PARENT + "/services/" + service
                     and set(body) == ({"name", "etag", "traffic", "template"}
                                       if mask == "template,traffic" else {"name", "etag", "traffic"}))
        self.armed, self.sent = (service, copy.deepcopy(body), mask), 0
        try:
            result = self.client.projects_locations_services.Patch(
                self.messages.RunProjectsLocationsServicesPatchRequest(
                    name=rolling.PARENT + "/services/" + service,
                    googleCloudRunV2Service=self.encoding.DictToMessage(body, self.messages.GoogleCloudRunV2Service),
                    updateMask=mask, allowMissing=False, forceNewRevision=False, validateOnly=False))
            # Never retain an LRO's full Service response: it may contain literal
            # credential fields inherited from the existing native environment.
            value = self.decode(result)
            rolling.need(type(value.get("name")) is str and rolling.OPERATION.fullmatch(value["name"]))
            return {"name": value["name"]}
        finally:
            self.armed = None

    def operation(self, name):
        rolling.need(rolling.OPERATION.fullmatch(name))
        value = self.decode(self.client.projects_locations_operations.Get(
            self.messages.RunProjectsLocationsOperationsGetRequest(name=name)))
        result = {"name": value.get("name"), "done": value.get("done", False)}
        if value.get("error") is not None:
            result["error"] = {"native_failure": True}
        return result


def financial_digest(connection: Any) -> str:
    """Exact bounded financial row multiset, with no row/customer data returned.

    This is a preservation check in a closed-admission deployment window, not a
    replacement for the existing financial invariant or invoice reconciliation.
    A late legitimate webhook changing finances stops further traffic changes.
    """
    from sqlalchemy import text
    digest, count, deadline = hashlib.sha256(), 0, time.monotonic() + 30
    sources = [(name, "SELECT * FROM public." + name) for name in FINANCIAL_TABLES]
    sources.append(("user_entitlements", "SELECT id,ai_credits,job_service_credits,tier,premium_until FROM public.users"))
    for name, source in sources:
        digest.update(name.encode() + b"\0")
        query = ("SELECT encode(pg_catalog.sha256(pg_catalog.convert_to(row_to_json(t)::text,'UTF8')),'hex') "
                 "AS digest FROM (" + source + ") AS t ORDER BY digest LIMIT 250001")
        with connection.execution_options(stream_results=True).execute(text(query)) as result:
            for row in result:
                count += 1
                rolling.need(count <= 250000 and time.monotonic() < deadline, "rolling_financial_snapshot_bound")
                value = row[0]
                rolling.need(type(value) is str and rolling.HASH.fullmatch(value))
                digest.update(bytes.fromhex(value))
        digest.update(b"\0")
    digest.update(str(count).encode())
    return digest.hexdigest()


class NativeRollingBoundary:
    """No dynamic production adapter: CLI constructs this exact native class."""
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.observer = fresh.FreshNativeBoundary(self.root)
        self._run: NativeRun | None = None

    @property
    def run(self):
        if self._run is None:
            self._run = NativeRun()
        return self._run

    def service(self, service):
        return self.run.service(service)

    def revision(self, service, name):
        return self.run.revision(service, name)

    def iam(self, service):
        rolling.need(service in release.SERVICES)
        return self.observer._cloud("run", "services", "get-iam-policy", service, "--region", release.REGION)

    def patch(self, service, body, mask):
        return self.run.patch(service, body, mask)

    def operation(self, name):
        return self.run.operation(name)

    def health(self, plan, service, revision, release_id, tag):
        self.observer._verify_http_health(SimpleNamespace(release=release_id), service, revision,
                                          candidate_tag=tag)

    def _reference(self, secret: str, selector: str) -> str:
        rolling.need(re.fullmatch(r"[a-zA-Z0-9_-]{1,255}", secret)
                     and re.fullmatch(r"latest|[1-9][0-9]{0,18}", selector))
        value = self.observer._cloud("secrets", "versions", "describe", selector, "--secret", secret)
        name = value.get("name", "")
        match = re.fullmatch(r"projects/157225724590/secrets/" + re.escape(secret) + r"/versions/([1-9][0-9]{0,18})", name)
        rolling.need(match is not None and value.get("state") == "ENABLED"
                     and type(value.get("etag")) is str and bool(value["etag"]), "rolling_secret_native_version_unverified")
        assert match is not None
        return match[1]

    def _identity(self, uid):
        rolling.need(type(uid) is str and re.fullmatch(r"[1-9][0-9]{20}", uid))
        account = self.observer._cloud("iam", "service-accounts", "describe", rolling.RUNTIME)
        keys = self.observer._cloud("iam", "service-accounts", "keys", "list", "--iam-account", rolling.RUNTIME)
        rolling.need(account.get("email") == rolling.RUNTIME and account.get("uniqueId") == uid
                     and account.get("disabled", False) is False and type(keys) is list
                     and all(key.get("keyType") == "SYSTEM_MANAGED" for key in keys), "rolling_runtime_identity_drift")

    def _target(self, runtime_secret, observer_secret, retired_migration_secret) -> str:
        module = self.observer._module("backend/scripts/monetary_cutover_preflight.py", "hirewiz_rolling_database_observer")
        neon = module.neon_identity
        metadata = self.observer.describe_release_secret(retired_migration_secret)
        name, version = retired_migration_secret.split(":")
        rolling.need(metadata.get("name") == f"projects/157225724590/secrets/{name}/versions/{version}"
                     and metadata.get("state") == "DISABLED", "rolling_migrator_must_remain_retired")
        financial = None
        for alias, reference in (("observer", observer_secret), ("replacement_runtime", runtime_secret)):
            binding, target = neon.collect_fresh_release_target(self.observer, reference, alias=alias,
                database_owner_oid=neon.FRESH_DATABASE_OWNER_OID, expected_schema=rolling.SCHEMA)
            engine = target.engine()
            try:
                with engine.connect() as connection, connection.begin():
                    connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                    connection.exec_driver_sql("SET LOCAL search_path=pg_catalog")
                    connection.exec_driver_sql("SET LOCAL statement_timeout='1000ms'")
                    connection.exec_driver_sql("SET LOCAL lock_timeout='1000ms'")
                    connection.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")
                    neon.verify_transport(connection, binding)
                    neon.verify_native_target(connection, binding, neon.FRESH_DATABASE_OID)
                    sessions, _ = module._native_session_ids(connection, neon.FRESH_DATABASE_OID, neon_binding=binding)
                    if alias == "observer":
                        release.verify_fresh_role_phase(connection, neon.FRESH_RELEASE_ACTORS, "serving", sessions)
                        release.verify_runtime_permissions(connection, "public",
                            neon.FRESH_RELEASE_ACTORS["replacement_runtime"][0],
                            neon.FRESH_RELEASE_ACTORS["replacement_migration"][0])
                        fresh.verify_financial_state(connection, module, rolling.SCHEMA)
                        financial = financial_digest(connection)
            finally:
                engine.dispose()
        rolling.need(financial is not None)
        return financial

    def verify_global(self, plan):
        plan.validate()
        self.observer.verify_source_image(release=plan.release, image_digest=plan.image_digest,
            build_id=plan.build_id, ci_run_id=plan.ci_run_id)
        self.observer.verify_policy_configuration(plan.policies)
        self.observer.verify_admission_closed()
        self._identity(plan.runtime_uid)
        # Worker images/provenance retain their actual separate native source
        # release. Do not relabel them as the new application commit.
        self.observer.verify_document_configuration(SimpleNamespace(release=plan.workers_release,
            runtime_service_account=rolling.RUNTIME))
        for pin in plan.services:
            value = self.service(pin.service)
            rolling.worker_iam(pin.service, self.iam(pin.service))
            env = rolling.environment(value["template"])
            rolling.need(value["template"].get("serviceAccount") == rolling.RUNTIME
                         and all(env.get(name, {"value": "false"}).get("value") == "false" for name in rolling.OFF))
            # Both current and rollback secret capabilities remain pinned to the
            # same actual version; a moved latest alias refuses the rollback too.
            old = self.revision(pin.service, pin.prior_revision)
            rolling.need(old is not None)
            assert old is not None
            for template in (value["template"], old):
                rolling.verify_policy_bindings(plan.policies, pin, template)
                rows = rolling.environment(template)
                for secret in pin.secrets:
                    ref = rows.get(secret.environment_name, {}).get("valueSource", {}).get("secretKeyRef", {})
                    rolling.need(ref.get("secret") == secret.secret
                                 and self._reference(secret.secret, ref.get("version", "")) == secret.version,
                                 "rolling_secret_reference_drift")
        rolling.need(self._target(plan.runtime_secret, plan.observer_secret,
                                  plan.retired_migration_secret) == plan.financial_sha256,
                     "rolling_financial_state_changed_reconcile_only")

    def capture(self, *, commit, image_digest, build_id, ci_run_id, nonce,
                observer_secret, retired_migration_secret, workers_release):
        """Read real post-cutover fields, never fabricate a ready plan from blanks."""
        self.observer.verify_source_image(release=commit, image_digest=image_digest,
                                         build_id=build_id, ci_run_id=ci_run_id)
        self.observer.verify_admission_closed()
        account = self.observer._cloud("iam", "service-accounts", "describe", rolling.RUNTIME)
        uid = account.get("uniqueId")
        self._identity(uid)
        pins, templates, api_env, runtime_reference = [], {}, None, None
        for service in release.SERVICES:
            value = self.service(service)
            rolling.worker_iam(service, self.iam(service))
            template, env = value["template"], rolling.environment(value["template"])
            templates[service] = template
            current = rolling.short_revision(value["latestReadyRevision"], service)
            rolling.need(current == rolling.short_revision(value["latestCreatedRevision"], service))
            # A ready, newer no-traffic revision is not the rollback target. Only
            # capture the supported state where all active traffic is on the
            # exact revision whose configuration and health will be checked.
            traffic = rolling.pinned_traffic(service, value, prior_revision=current)
            secrets = []
            for name, row in env.items():
                if "valueSource" in row:
                    ref = row["valueSource"].get("secretKeyRef", {})
                    secrets.append(rolling.SecretPin(name, ref.get("secret", ""),
                        self._reference(ref.get("secret", ""), ref.get("version", ""))))
            revision = self.revision(service, current)
            rolling.need(revision is not None)
            assert revision is not None
            keys = tuple(sorted(set(template) - {"revision"}))
            pin = rolling.ServicePin(service, value["uid"], int(value["generation"]), current,
                env.get("APP_RELEASE", {}).get("value", ""), rolling.template_sha(template, service),
                rolling.static_sha(value), rolling.sha(self.iam(service)),
                rolling.sha({key: revision.get(key) for key in keys}), keys,
                tuple(traffic), tuple(sorted(secrets, key=lambda row: row.environment_name)))
            database = next(row for row in pin.secrets if row.environment_name == "DATABASE_URL")
            reference = database.secret + ":" + database.version
            rolling.need(runtime_reference in {None, reference})
            runtime_reference = reference
            pins.append(pin)
            if service == release.SERVICES[0]:
                api_env = env
        rolling.need(api_env is not None and runtime_reference is not None)
        assert api_env is not None and runtime_reference is not None
        refs = {row.environment_name: row.secret + ":" + row.version for row in pins[0].secrets}
        expense, model = refs["HIREWIZ_EXPENSE_POLICY_JSON"], refs["LLM_MODEL_COST_POLICY_JSON"]
        policies = rolling.PolicyPins(expense, hashlib.sha256(self.observer._secret(expense).encode()).hexdigest(),
            model, hashlib.sha256(self.observer._secret(model).encode()).hexdigest(), refs["LLM_API_KEY"],
            api_env["LLM_MODEL"]["value"], api_env["LLM_API_BASE"]["value"])
        for pin in pins:
            rolling.verify_policy_bindings(policies, pin, templates[pin.service])
        plan = rolling.RollingPlan(commit, image_digest, build_id, ci_run_id, nonce, uid,
            runtime_reference, observer_secret, retired_migration_secret, workers_release,
            self._target(runtime_reference, observer_secret, retired_migration_secret), policies, tuple(pins))
        plan.validate()
        self.verify_global(plan)
        for pin in plan.services:
            rolling.stage_body(plan, pin, self.service(pin.service))
            rolling.need(self.revision(pin.service, plan.revision(pin.service)) is None)
        return plan


def load_plan(raw: bytes) -> rolling.RollingPlan:
    value = release._json(raw)
    value["policies"] = rolling.PolicyPins(**value["policies"])
    services = []
    for item in value["services"]:
        item["secrets"] = tuple(rolling.SecretPin(**row) for row in item["secrets"])
        item["revision_keys"], item["traffic"] = tuple(item["revision_keys"]), tuple(item["traffic"])
        services.append(rolling.ServicePin(**item))
    value["services"] = tuple(services)
    plan = rolling.RollingPlan(**value)
    plan.validate()
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("capture", "observe", "advance", "reconcile", "rollback"), default="observe")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--journal", type=Path)
    for name in ("release", "image-digest", "build-id", "nonce", "observer-secret", "retired-migration-secret", "workers-release"):
        parser.add_argument("--" + name)
    parser.add_argument("--ci-run-id", type=int)
    args = parser.parse_args()
    adapter = NativeRollingBoundary(Path(__file__).resolve().parents[2])
    if args.mode == "capture":
        rolling.need(all(getattr(args, key) is not None for key in ("release", "image_digest", "build_id", "nonce",
            "observer_secret", "retired_migration_secret", "workers_release", "ci_run_id")))
        plan = adapter.capture(commit=args.release, image_digest=args.image_digest, build_id=args.build_id,
            ci_run_id=args.ci_run_id, nonce=args.nonce, observer_secret=args.observer_secret,
            retired_migration_secret=args.retired_migration_secret, workers_release=args.workers_release)
        rolling.need(args.plan.is_absolute() and args.plan.parent.resolve() == args.plan.parent
                     and args.plan.parent.stat().st_uid == os.getuid()
                     and args.plan.parent.stat().st_mode & 0o077 == 0)
        raw = rolling.canonical(asdict(plan)) + b"\n"
        fd = os.open(args.plan, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        print(json.dumps({"status": "native_post_cutover_plan_captured", "provider_mutations": 0,
                          "plan_sha256": hashlib.sha256(raw).hexdigest(), "rolling_release_complete": False}))
        return
    rolling.need(args.plan.is_file() and not args.plan.is_symlink()
                 and args.plan.stat().st_size <= 131072 and rolling.HASH.fullmatch(args.plan_sha256 or ""))
    raw = args.plan.read_bytes()
    rolling.need(hashlib.sha256(raw).hexdigest() == args.plan_sha256)
    plan = load_plan(raw)
    if args.mode == "observe":
        adapter.verify_global(plan)
        print(json.dumps({"status": "read_only_gates_observed", "provider_mutations": 0,
                          "rolling_release_complete": False}))
        return
    rolling.need(args.journal is not None)
    journal = rolling.Journal(args.journal, plan)
    method = {"advance": rolling.advance, "reconcile": rolling.reconcile, "rollback": rolling.rollback}[args.mode]
    print(json.dumps(method(plan, adapter, journal), sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:
        if isinstance(error, SystemExit):
            raise
        # Do not print exceptions, native responses, service env, DSNs, tokens,
        # provider key fields or even an unreviewed message from a vendor error.
        print('{"status":"rolling_stopped_reconcile_only","replay_authorized":false,"goal_complete":false}')
        sys.exit(2)
