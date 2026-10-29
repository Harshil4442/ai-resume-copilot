"""Finite native-shaped fixtures, never cloud or deployment acceptance."""
from __future__ import annotations

import copy
import importlib
import json
import sys
from contextlib import contextmanager
from dataclasses import asdict, fields, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from backend.tests.test_rolling_release import FixtureBoundary

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/gcp"))
app_release = importlib.import_module("schema25_app_rolling_release")
rolling = app_release.rolling


class AppFixture(FixtureBoundary):
    def __init__(self):
        super().__init__()
        self.resource_drift = False
        pins = []
        for pin in self.plan.services:
            service = self.states[pin.service]
            template = service["template"]
            template["labels"]["hirewiz-source"] = app_release.PRIOR.release
            container = template["containers"][0]
            container["image"] = self.plan.image.split("@")[0] + "@" + app_release.PRIOR.image_digest
            container["command"] = ["python"]
            module = "app.main:app" if pin.service == app_release.SERVICES[0] else "app.worker_main:app"
            container["args"] = ["-m", "uvicorn", module, "--host", "0.0.0.0", "--port", "8080"]
            env = rolling.environment(template)
            env["APP_RELEASE"]["value"] = app_release.PRIOR.release
            env["PGSSLROOTCERT"] = {"name": "PGSSLROOTCERT", "value": rolling.release.SAFE_ENV["PGSSLROOTCERT"]}
            for flag in app_release.initial.OFF:
                value = "true" if flag in app_release.PILOT_FLAGS and (
                    pin.service == app_release.SERVICES[0] or flag == "EMPLOYER_DISCOVERY_ENABLED") else "false"
                env[flag] = {"name": flag, "value": value}
            container["env"] = list(env.values())
            old = {**copy.deepcopy(template), "name": service["latestReadyRevision"],
                   "conditions": [{"type": "Ready", "state": "CONDITION_SUCCEEDED"}]}
            self.revisions[(pin.service, pin.prior_revision)] = old
            keys = tuple(sorted(template))
            pins.append(replace(pin, prior_release=app_release.PRIOR.release,
                template_sha256=rolling.template_sha(template, pin.service),
                revision_keys=keys, revision_sha256=rolling.sha({key: old.get(key) for key in keys})))
        resources = [app_release.ResourcePin(kind, name, "a" * 64)
                     for kind, names in app_release.maintenance.RESOURCE_NAMES.items() for name in names]
        resources += [app_release.ResourcePin("service", name, "a" * 64) for name in app_release.EXTERNAL_SERVICES]
        base = {field.name: getattr(self.plan, field.name) for field in fields(rolling.RollingPlan)}
        self.plan = app_release.App25Plan(**(base | {"services": tuple(pins)}),
            resources=tuple(resources), compatibility_sha256="b" * 64,
            reachable_revisions=tuple(app_release.reachable_pin(pin.service, pin.prior_revision,
                self.revision(pin.service, pin.prior_revision)) for pin in pins))
        self.plan.validate()
        self.targets = {pin.service: app_release.candidate_template(self.plan, pin, self.service(pin.service))
                        for pin in self.plan.services}

    def verify_global(self, plan):
        plan.validate()
        if self.resource_drift:
            raise rolling.RollingDenied("schema25_app_resource_or_document_state_changed")
        for pin in plan.services:
            app_release.observed_flags(self.states[pin.service]["template"], pin.service)
            app_release.current_phase(plan, pin, self.states[pin.service], self.targets[pin.service])


@pytest.fixture
def context(tmp_path):
    tmp_path.chmod(0o700)
    boundary = AppFixture()
    return boundary, rolling.Journal(tmp_path, boundary.plan)


def test_new_contract_cannot_be_adopted_by_migration_or_old_loader(context):
    boundary, journal = context
    raw = rolling.canonical(asdict(boundary.plan))
    assert app_release.load(raw) == boundary.plan
    with pytest.raises(rolling.RollingDenied):
        app_release.initial.load_plan(raw)
    with pytest.raises(rolling.RollingDenied, match="migration_contract"):
        app_release.initial.migration(boundary.plan, object(), journal)
    assert boundary.writes == []


def test_stage_keeps_prior_traffic_and_only_changes_image_release_revision(context):
    boundary, journal = context
    originals = copy.deepcopy(boundary.states)
    result = app_release.execute("stage", boundary.plan, boundary, journal)
    assert result["status"] == "schema25_app_staged_zero_traffic"
    assert [mask for _, _, mask in boundary.writes] == ["template,traffic"] * 3
    for name, body, _ in boundary.writes:
        old = originals[name]["template"]
        expected = copy.deepcopy(old)
        expected["revision"] = boundary.plan.revision(name)
        expected["labels"]["hirewiz-source"] = boundary.plan.release
        expected["containers"][0]["image"] = boundary.plan.image
        for row in expected["containers"][0]["env"]:
            if row["name"] == "APP_RELEASE":
                row["value"] = boundary.plan.release
        assert body["template"] == expected
        assert [row for row in body["traffic"] if row.get("percent")] == list(boundary.plan.pin(name).traffic)
        assert "synthetic-sensitive" not in repr([file.read_text() for file in journal.directory.glob("*.json")])


def test_full_workers_then_api_promotion_and_traffic_only_rollback(context):
    boundary, journal = context
    assert app_release.execute("advance", boundary.plan, boundary, journal)["rolling_release_complete"]
    assert [name for name, _, _ in boundary.writes[3:]] == [*app_release.SERVICES[1:], app_release.SERVICES[0]]
    result = app_release.execute("rollback", boundary.plan, boundary, journal)
    assert result["status"] == "schema25_app_prior_traffic_restored"
    assert result["schema"] == "20261028_0025" and result["disaster_recovery_rto_qualified"] is False
    for name, body, mask in boundary.writes[-3:]:
        assert mask == "traffic" and set(body) == {"name", "etag", "traffic"}
        assert next(row["revision"] for row in body["traffic"] if row.get("percent") == 100) == boundary.plan.pin(name).prior_revision
    with pytest.raises(rolling.RollingDenied, match="rollback_in_progress"):
        app_release.execute("advance", boundary.plan, boundary, journal)


def test_pending_rollback_never_reports_restoration_complete(context):
    boundary, journal = context
    app_release.execute("advance", boundary.plan, boundary, journal)
    boundary.pause = True
    result = app_release.execute("rollback", boundary.plan, boundary, journal)
    assert result["status"] == "schema25_app_same_operation_pending"
    assert result["phase"] == "rollback"


def test_unknown_send_reconcile_never_starts_next_or_repeats_patch(context):
    boundary, journal = context
    boundary.lose_ack = True
    with pytest.raises(RuntimeError):
        app_release.execute("advance", boundary.plan, boundary, journal)
    with pytest.raises(rolling.RollingDenied, match="reconcile_only"):
        app_release.execute("advance", boundary.plan, boundary, journal)
    assert len(boundary.writes) == 1
    result = app_release.execute("reconcile", boundary.plan, boundary, journal)
    assert result["native_mutations"] == 0 and len(boundary.writes) == 1
    app_release.execute("advance", boundary.plan, boundary, journal)
    assert len(boundary.writes) == 6


@pytest.mark.parametrize("change", ["closed_flag", "pilot_flag", "secret", "environment", "resource", "iam", "traffic"])
def test_drift_blocks_before_new_mutation(context, change):
    boundary, journal = context
    service = app_release.SERVICES[0]
    current = boundary.states[service]
    env = rolling.environment(current["template"])
    if change == "closed_flag":
        env["UNIFIED_CREDIT_EXECUTION_ENABLED"]["value"] = "true"
    elif change == "pilot_flag":
        env["CANDIDATE_ACCOUNT_LIFECYCLE_ENABLED"]["value"] = "false"
    elif change == "secret":
        env["DATABASE_URL"]["valueSource"]["secretKeyRef"]["version"] = "9"
    elif change == "environment":
        env["RAZORPAY_KEY_SECRET"]["value"] = "changed"
    elif change == "resource":
        boundary.resource_drift = True
    elif change == "iam":
        boundary.policies[service]["bindings"].append({"role": "roles/run.invoker", "members": ["extra"]})
    else:
        current["trafficStatuses"][0]["tag"] = "changed"
    with pytest.raises(rolling.RollingDenied):
        app_release.execute("advance", boundary.plan, boundary, journal)
    assert boundary.writes == []


def test_pure_candidate_refuses_normalization_of_unrelated_configuration(context):
    boundary, _ = context
    pin = boundary.plan.pin(app_release.SERVICES[0])
    current = boundary.service(pin.service)
    current["template"]["containers"][0]["command"] = ["unreviewed-custom-launcher"]
    altered = replace(pin, template_sha256=rolling.template_sha(current["template"], pin.service))
    with pytest.raises(rolling.RollingDenied, match="unrelated_candidate"):
        app_release.candidate_template(boundary.plan, altered, current)


def test_prior_image_source_proof_cannot_be_replaced_by_labels(context):
    boundary, _ = context
    for changed in (replace(boundary.plan, prior_source=replace(app_release.PRIOR, ci_run_id=1)),
                    replace(boundary.plan, database_schema="20261023_0021"),
                    replace(boundary.plan, schema_contract=app_release.initial.contract.CONTRACT)):
        with pytest.raises(rolling.RollingDenied):
            changed.validate()


@pytest.mark.parametrize("change", ["extra_generation", "latest_created", "foreign_traffic", "foreign_revision"])
def test_observation_refuses_resource_shapes_outside_exact_app_graph(context, change):
    boundary, journal = context
    app_release.execute("stage", boundary.plan, boundary, journal)
    current = boundary.states[app_release.SERVICES[0]]
    if change == "extra_generation":
        current["generation"] = current["observedGeneration"] = "99"
    elif change == "latest_created":
        current["latestCreatedRevision"] = current["latestReadyRevision"] + "-extra"
    else:
        current["trafficStatuses"][0]["tag" if change == "foreign_traffic" else "revision"] = "foreign"
    with pytest.raises(rolling.RollingDenied):
        boundary.verify_global(boundary.plan)


def test_native_global_reuses_prior_and_candidate_build_and_current_funding_proofs(context, monkeypatch):
    fixture, _ = context
    adapter = app_release.NativeApp25Boundary(ROOT)
    calls = []
    monkeypatch.setattr(adapter.observer, "verify_source_image", lambda **kwargs: calls.append(("candidate", kwargs)))
    monkeypatch.setattr(adapter.observer, "_verify_source_image_provenance", lambda **kwargs: calls.append(("prior", kwargs)))
    monkeypatch.setattr(adapter.observer, "verify_policy_configuration", lambda value: calls.append(("funding", value)))
    monkeypatch.setattr(adapter.observer, "verify_admission_closed", lambda: pytest.fail("Do not drain the open pilot"))
    monkeypatch.setattr(adapter, "_compatibility", lambda plan: calls.append(("compatibility", plan.compatibility_sha256)))
    monkeypatch.setattr(adapter, "_identity", lambda value: calls.append(("identity", value)))
    monkeypatch.setattr(adapter, "_resources", lambda value: fixture.plan.resources)
    monkeypatch.setattr(adapter, "_database", lambda *args, **kwargs: (fixture.plan.financial_sha256, ()))
    monkeypatch.setattr(adapter, "service", fixture.service)
    monkeypatch.setattr(adapter, "revision", fixture.revision)
    monkeypatch.setattr(adapter, "iam", fixture.iam)
    monkeypatch.setattr(adapter, "_reference", lambda secret, version: version)
    adapter.verify_global(fixture.plan)
    assert calls[0] == ("candidate", {"release": fixture.plan.release, "image_digest": fixture.plan.image_digest,
        "build_id": fixture.plan.build_id, "ci_run_id": fixture.plan.ci_run_id})
    assert calls[1] == ("prior", asdict(app_release.PRIOR))
    assert [item[0] for item in calls] == ["candidate", "prior", "compatibility", "funding", "identity"]


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped", "success"])
def test_native_requires_explicit_successful_compatibility_step_in_candidate_ci(context, monkeypatch, conclusion):
    fixture, _ = context
    adapter = app_release.NativeApp25Boundary(ROOT)
    monkeypatch.setattr(app_release, "compatibility_source", lambda root: fixture.plan.compatibility_sha256)
    calls = []
    def command(*args):
        calls.append(args)
        return json.dumps({"jobs": [{"name": "backend", "steps": [{"name": app_release.contract.COMPATIBILITY_STEP,
            "status": "completed", "conclusion": conclusion}]}]}).encode()
    monkeypatch.setattr(adapter.observer, "_command", command)
    if conclusion == "success":
        adapter._compatibility(fixture.plan)
    else:
        with pytest.raises(rolling.RollingDenied, match="rehearsal_not_successful"):
            adapter._compatibility(fixture.plan)
    assert calls == [("gh", "run", "view", str(fixture.plan.ci_run_id), "--repo", rolling.release.REPOSITORY, "--json", "jobs")]


def test_native_database_proof_preserves_exact_tls_oid_catalog_and_acl_readonly_boundary(context, monkeypatch):
    fixture, _ = context
    adapter = app_release.NativeApp25Boundary(ROOT)
    from scripts import neon_direct_identity as neon
    observed = []

    @contextmanager
    def transaction():
        yield

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def begin(self): return transaction()
        def exec_driver_sql(self, sql): observed.append(sql)

    class Engine:
        def connect(self): return Connection()
        def dispose(self): observed.append("dispose")

    def collect(reader, reference, **values):
        assert values["expected_schema"] == "20261028_0025"
        assert values["database_owner_oid"] == neon.FRESH_DATABASE_OWNER_OID
        observed.append(values["alias"])
        return values["alias"], SimpleNamespace(engine=lambda: Engine())

    module = SimpleNamespace(neon_identity=neon, _native_session_ids=lambda *args, **kwargs: ([], {}))
    monkeypatch.setattr(adapter.observer, "_module", lambda *args: module)
    monkeypatch.setattr(adapter.observer, "describe_release_secret", lambda reference: {
        "name": "projects/157225724590/secrets/synthetic-migration/versions/5", "state": "DISABLED"})
    monkeypatch.setattr(neon, "collect_schema25_release_target", collect)
    monkeypatch.setattr(neon, "verify_transport", lambda *args: observed.append("tls"))
    monkeypatch.setattr(neon, "verify_native_target", lambda *args: observed.append("native_oid"))
    monkeypatch.setattr(rolling.release, "verify_fresh_role_phase", lambda *args: observed.append("roles"))
    monkeypatch.setattr(app_release.initial.identity_step, "verify_financial_catalog", lambda *args: observed.append("old_catalog"))
    monkeypatch.setattr(app_release, "verify_permissions", lambda *args: observed.append("old_acl"))
    monkeypatch.setattr(app_release.initial.previous.contract, "verify_reset_permissions", lambda *args: observed.append("reset_acl"))
    monkeypatch.setattr(app_release.initial.step, "verify_new_permissions", lambda *args: observed.append("new_acl"))
    monkeypatch.setattr(app_release.contract, "observe", lambda *args: fixture.plan.financial_sha256)
    monkeypatch.setattr(app_release.contract, "liability_pins", lambda *args: ())
    monkeypatch.setattr(app_release.contract, "verify_liability_pins", lambda *args: observed.append("liabilities"))
    monkeypatch.setattr(app_release.initial.identity_step, "drained", lambda *args: pytest.fail("No DDL drain"))
    monkeypatch.setattr(app_release.native, "financial_digest", lambda *args: pytest.fail("No full live financial snapshot"))
    assert adapter._database(fixture.plan.runtime_secret, fixture.plan.observer_secret,
        fixture.plan.retired_migration_secret, expected=()) == (fixture.plan.financial_sha256, ())
    assert observed.count("tls") == observed.count("native_oid") == observed.count("dispose") == 2
    assert all(name in observed for name in ("roles", "old_catalog", "old_acl", "reset_acl", "new_acl", "liabilities"))
    assert observed.count("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY") == 2


def test_resources_read_every_current_native_pin_without_pausing_or_reopening(context, monkeypatch):
    fixture, _ = context
    adapter = app_release.NativeApp25Boundary(ROOT)
    controls, services, revisions, calls = {}, {}, {}, []
    for kind, names in app_release.maintenance.RESOURCE_NAMES.items():
        for name in names:
            controls[(kind, name)] = {"name": name, "state": "RUNNING" if kind == "queue" else "ENABLED",
                "schedule": "* * * * *"} if kind == "schedule" else {"name": name, "state": "RUNNING"}
    for name in app_release.EXTERNAL_SERVICES:
        value = copy.deepcopy(fixture.states[app_release.SERVICES[0]])
        prefix = rolling.PARENT + "/services/" + name
        active, created = name + "-active", name + "-restored"
        value.update(name=prefix, latestCreatedRevision=prefix + "/revisions/" + created,
            latestReadyRevision=prefix + "/revisions/" + name + "-stale-paused")
        value["trafficStatuses"] = [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": active, "percent": 100}]
        value["traffic"] = copy.deepcopy(value["trafficStatuses"])
        if name == app_release.coordinator.SERVICE:
            value["trafficStatuses"].append({"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": name + "-old-tag", "tag": "retained-old"})
        services[name] = value
        for identity in {active, created} | {row["revision"] for row in value["trafficStatuses"]}:
            revisions[(name, identity)] = {**copy.deepcopy(value["template"]), "name": prefix + "/revisions/" + identity,
                "conditions": [{"type": "Ready", "state": "CONDITION_SUCCEEDED"}]}

    class Controls:
        def __init__(self):
            self.session = SimpleNamespace(close=lambda: calls.append("control_close"))
        def get(self, kind, name):
            calls.append((kind, name))
            return copy.deepcopy(controls[(kind, name)])
        def set_state(self, *args): pytest.fail("No resource writes")

    class Coordinator:
        def __init__(self):
            self.stock = SimpleNamespace(session=SimpleNamespace(close=lambda: calls.append("coordinator_close")))
        def service(self, name):
            assert name == app_release.coordinator.SERVICE
            return copy.deepcopy(services[name])
        def revision(self, name, identity):
            assert name == app_release.coordinator.SERVICE
            calls.append((name, identity))
            return copy.deepcopy(revisions[(name, identity)])
        def patch(self, *args): pytest.fail("No coordinator write")

    def document_get(api, path):
        assert api == "run"
        parts = path.split("/services/")[1].split("/revisions/")
        assert parts[0] in app_release.EXTERNAL_SERVICES[:2]
        return copy.deepcopy(services[parts[0]] if len(parts) == 1 else revisions[tuple(parts)])

    monkeypatch.setattr(app_release.maintenance, "NativeControls", Controls)
    monkeypatch.setattr(app_release.coordinator, "NativeCoordinatorRun", Coordinator)
    monkeypatch.setattr(adapter.observer, "_get", document_get)
    monkeypatch.setattr(adapter.observer, "_cloud", lambda *args: {"bindings": []})
    monkeypatch.setattr(adapter.observer, "verify_document_configuration", lambda proof: calls.append(("document_proof", proof.release)))
    before = adapter._resources(fixture.plan.workers_release)
    assert len(before) == 10 and ("document_proof", fixture.plan.workers_release) in calls
    assert calls.count("control_close") == calls.count("coordinator_close") == 1
    schedule = controls[("schedule", app_release.maintenance.RESOURCE_NAMES["schedule"][0])]
    schedule.update(lastAttemptTime="synthetic-now", scheduleTime="synthetic-next", status={"code": 0})
    assert adapter._resources(fixture.plan.workers_release) == before
    # A separately reachable retained tag is pinned even though latestReady is
    # a harmless stale unserved pointer after the previous maintenance restore.
    revisions[(app_release.coordinator.SERVICE, app_release.coordinator.SERVICE + "-old-tag")]["containers"][0]["image"] += "changed"
    assert adapter._resources(fixture.plan.workers_release) != before
    schedule["state"] = "PAUSED"
    assert adapter._resources(fixture.plan.workers_release) != before
    services[app_release.coordinator.SERVICE]["reconciling"] = True
    with pytest.raises(rolling.RollingDenied, match="external_service_unready"):
        adapter._resources(fixture.plan.workers_release)
    assert calls.count("control_close") == calls.count("coordinator_close") == 5


def test_native_retired_migration_secret_is_never_enabled_for_an_app_release(context, monkeypatch):
    fixture, _ = context
    adapter = app_release.NativeApp25Boundary(ROOT)
    monkeypatch.setattr(adapter.observer, "_module", lambda *args: SimpleNamespace(neon_identity=SimpleNamespace()))
    monkeypatch.setattr(adapter.observer, "describe_release_secret", lambda *args: {
        "name": "projects/157225724590/secrets/synthetic-migration/versions/5", "state": "ENABLED"})
    monkeypatch.setattr(adapter.observer, "access_release_secret", lambda *args: pytest.fail("No credential read after invalid retirement"))
    with pytest.raises(rolling.RollingDenied, match="retired_migrator_changed"):
        adapter._database(fixture.plan.runtime_secret, fixture.plan.observer_secret, fixture.plan.retired_migration_secret)


def reject_candidate_health(boundary, monkeypatch):
    original = boundary.health
    def health(plan, service, revision, release_id, tag):
        if release_id == plan.release:
            raise rolling.RollingDenied("synthetic_candidate_http_health_failure")
        return original(plan, service, revision, release_id, tag)
    monkeypatch.setattr(boundary, "health", health)


def test_failed_first_staged_candidate_can_restore_prior_without_staging_untouched_workers(context, monkeypatch):
    boundary, journal = context
    reject_candidate_health(boundary, monkeypatch)
    with pytest.raises(rolling.RollingDenied, match="candidate_http_health_failure"):
        app_release.execute("advance", boundary.plan, boundary, journal)
    assert len(boundary.writes) == 1 and journal.read("stage." + app_release.SERVICES[0] + ".checked") is None
    result = app_release.execute("rollback", boundary.plan, boundary, journal)
    assert result["status"] == "schema25_app_prior_traffic_restored" and result["native_mutations"] == 1
    assert len(boundary.writes) == 2 and boundary.writes[-1][2] == "traffic"
    assert journal.read("stage." + app_release.SERVICES[0] + ".checked") is None
    assert all(journal.read("stage." + name + ".started") is None for name in app_release.SERVICES[1:])
    with pytest.raises(rolling.RollingDenied, match="rollback_in_progress"):
        app_release.execute("advance", boundary.plan, boundary, journal)
    # The shared forward engine is fenced too, while holding the same lock.
    with pytest.raises(rolling.RollingDenied, match="rollback_in_progress"):
        rolling.advance(boundary.plan, boundary, journal)
    assert app_release.execute("reconcile", boundary.plan, boundary, journal)["native_mutations"] == 0
    assert len(boundary.writes) == 2


def test_unknown_promotion_ack_is_metadata_reconciled_before_distinct_rollback_send(context, monkeypatch):
    boundary, journal = context
    app_release.execute("stage", boundary.plan, boundary, journal)
    boundary.lose_ack = True
    with pytest.raises(RuntimeError):
        app_release.execute("advance", boundary.plan, boundary, journal)
    name = app_release.SERVICES[1]
    assert journal.read("promote." + name + ".started") and journal.read("promote." + name + ".ack") is None
    reject_candidate_health(boundary, monkeypatch)
    result = app_release.execute("rollback", boundary.plan, boundary, journal)
    assert result["status"] == "schema25_app_prior_traffic_restored"
    evidence = journal.read("app25.rollback.predecessor." + name)
    assert evidence["phase"] == "promote" and evidence["operation_ack_retained"] is False
    assert evidence["native_metadata_reobserved"] is True and evidence["candidate_http_health_required"] is False
    assert journal.read("promote." + name + ".checked") is None
    assert len(boundary.writes) == 7  # three stages, one promotion, three distinct traffic rollbacks
    assert app_release.execute("reconcile", boundary.plan, boundary, journal)["native_mutations"] == 0
    assert len(boundary.writes) == 7


def test_post_promotion_candidate_health_regression_does_not_block_prior_recovery(context, monkeypatch):
    boundary, journal = context
    app_release.execute("advance", boundary.plan, boundary, journal)
    reject_candidate_health(boundary, monkeypatch)
    result = app_release.execute("rollback", boundary.plan, boundary, journal)
    assert result["status"] == "schema25_app_prior_traffic_restored" and len(boundary.writes) == 9
    assert all(item[2] == app_release.PRIOR.release and item[3] == boundary.plan.rollback_tag
               for item in boundary.health_checks[-3:])


def test_unknown_rollback_ack_has_no_second_send_and_needs_exact_readonly_reconciliation(context):
    boundary, journal = context
    app_release.execute("advance", boundary.plan, boundary, journal)
    boundary.lose_ack = True
    with pytest.raises(RuntimeError):
        app_release.execute("rollback", boundary.plan, boundary, journal)
    assert len(boundary.writes) == 7
    with pytest.raises(rolling.RollingDenied, match="unknown_rollback_mutation_reconcile_only"):
        app_release.execute("rollback", boundary.plan, boundary, journal)
    assert len(boundary.writes) == 7
    assert app_release.execute("reconcile", boundary.plan, boundary, journal)["native_mutations"] == 0
    assert len(boundary.writes) == 7
    assert app_release.execute("rollback", boundary.plan, boundary, journal)["status"] == "schema25_app_prior_traffic_restored"
    assert len(boundary.writes) == 9


def test_failed_prior_health_never_records_completed_rollback_or_repeats_traffic_send(context, monkeypatch):
    boundary, journal = context
    app_release.execute("advance", boundary.plan, boundary, journal)
    monkeypatch.setattr(boundary, "health", lambda *args: (_ for _ in ()).throw(rolling.RollingDenied("synthetic_prior_unavailable")))
    with pytest.raises(rolling.RollingDenied, match="prior_unavailable"):
        app_release.execute("rollback", boundary.plan, boundary, journal)
    assert len(boundary.writes) == 7 and journal.read("rollback." + app_release.SERVICES[0] + ".checked") is None
    with pytest.raises(rolling.RollingDenied, match="prior_unavailable"):
        app_release.execute("reconcile", boundary.plan, boundary, journal)
    assert len(boundary.writes) == 7


@pytest.mark.parametrize("change", ["source", "image", "ai", "checkout", "command", "secret"])
def test_existing_tagged_revision_cannot_hide_unknown_source_or_paid_admission(context, change):
    boundary, _ = context
    pin = boundary.plan.pin(app_release.SERVICES[0])
    baseline = boundary.revision(pin.service, pin.prior_revision)
    tagged = copy.deepcopy(baseline)
    identity = pin.service + "-retained-tag"
    tagged["name"] = rolling.PARENT + "/services/" + pin.service + "/revisions/" + identity
    env = rolling.environment(tagged)
    if change == "source":
        env["APP_RELEASE"]["value"] = "f" * 40
    elif change == "image":
        tagged["containers"][0]["image"] = boundary.plan.image
    elif change in {"ai", "checkout"}:
        env["OPTIONAL_AI_GENERATION_ENABLED" if change == "ai" else "RAZORPAY_CHECKOUT_ENABLED"]["value"] = "true"
    elif change == "command":
        tagged["containers"][0]["command"] = ["unreviewed-launcher"]
    else:
        env["DATABASE_URL"]["valueSource"]["secretKeyRef"]["version"] = "9"
    with pytest.raises(rolling.RollingDenied):
        app_release.reachable_pin(pin.service, identity, tagged, baseline)


def test_reachable_tag_may_retain_only_less_enabled_pilot_flags_and_plan_cannot_drop_pin(context):
    boundary, _ = context
    pin = boundary.plan.pin(app_release.SERVICES[0])
    baseline = boundary.revision(pin.service, pin.prior_revision)
    tagged = copy.deepcopy(baseline)
    identity = pin.service + "-retained-tag"
    tagged["name"] = rolling.PARENT + "/services/" + pin.service + "/revisions/" + identity
    rolling.environment(tagged)["CANDIDATE_ACCOUNT_LIFECYCLE_ENABLED"]["value"] = "false"
    assert app_release.reachable_pin(pin.service, identity, tagged, baseline).revision == identity
    with pytest.raises(rolling.RollingDenied, match="reachable_prior_pins_invalid"):
        replace(boundary.plan, reachable_revisions=boundary.plan.reachable_revisions[1:]).validate()
