"""Synthetic native-shaped fixtures only: no GCP, SQL or actual deployment calls."""
from __future__ import annotations

import copy
import importlib
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/gcp"))
rolling = importlib.import_module("rolling_release")
native = importlib.import_module("rolling_native")
SOURCE, PREVIOUS = "a" * 40, "b" * 40
API = rolling.release.SERVICES[0]


class FixtureBoundary:
    """Not a Cloud Run emulator or an auth/financial acceptance substitute."""
    def __init__(self):
        self.states, self.revisions, self.policies, self.operations = {}, {}, {}, {}
        self.writes, self.gates, self.health_checks, self.polls = [], [], [], []
        self.pause, self.lose_ack, self.finance_drift, self.old_lost = False, False, False, False
        pins = []
        for index, service in enumerate(rolling.release.SERVICES):
            previous = service + "-00001-synthetic"
            env = [{"name": "SERVICE_ROLE", "value": "api" if index == 0 else "worker"},
                   {"name": "APP_RELEASE", "value": PREVIOUS},
                   {"name": "AUTO_DB_MIGRATE", "value": "false"},
                   {"name": "DATABASE_URL", "valueSource": {"secretKeyRef": {"secret": "synthetic-runtime", "version": "3"}}},
                   {"name": "HIREWIZ_EXPENSE_POLICY_JSON", "valueSource": {
                       "secretKeyRef": {"secret": "synthetic-expense", "version": "1"}}},
                   {"name": "RAZORPAY_KEY_SECRET", "value": "synthetic-sensitive-literal"}]
            if index < 2:
                env.extend([
                    {"name": "LLM_MODEL", "value": "gemini-3.6-flash"},
                    {"name": "LLM_API_BASE", "value": rolling.release.MODEL_API_BASE},
                    {"name": "LLM_MODEL_COST_POLICY_JSON", "valueSource": {
                        "secretKeyRef": {"secret": "synthetic-model-policy", "version": "1"}}},
                    {"name": "LLM_API_KEY", "valueSource": {
                        "secretKeyRef": {"secret": "synthetic-model-key", "version": "2"}}},
                ])
            if index:
                env.append({"name": "WORKER_LABEL", "value": "analysis-worker" if index == 1 else "employer-worker"})
            template = {"labels": {"hirewiz-source": PREVIOUS}, "serviceAccount": rolling.RUNTIME,
                "containers": [{"image": "us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz@sha256:" + "f" * 64,
                                "env": env, "ports": [{"containerPort": 8080}],
                                "resources": {"limits": {"cpu": "1", "memory": "1Gi"}}}],
                "executionEnvironment": "EXECUTION_ENVIRONMENT_GEN2", "maxInstanceRequestConcurrency": 4}
            traffic = [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": previous, "percent": 100}]
            resource = rolling.PARENT + "/services/" + service
            value = {"name": resource, "uid": f"00000000-0000-4000-8000-00000000000{index}", "etag": "etag-42",
                "generation": "42", "observedGeneration": "42", "reconciling": False,
                "latestReadyRevision": resource + "/revisions/" + previous,
                "latestCreatedRevision": resource + "/revisions/" + previous,
                "terminalCondition": {"state": "CONDITION_SUCCEEDED"}, "ingress": "INGRESS_TRAFFIC_ALL",
                "template": template, "traffic": traffic, "trafficStatuses": copy.deepcopy(traffic)}
            members = ["allUsers"] if index == 0 else ["serviceAccount:hirewiz-tasks@ai-resume-parser-482412.iam.gserviceaccount.com"]
            if index == 1:
                members.append("serviceAccount:hirewiz-scheduler@ai-resume-parser-482412.iam.gserviceaccount.com")
            policy = {"version": 3, "etag": "synthetic-policy", "bindings": [{"role": "roles/run.invoker", "members": members}]}
            revision = {**copy.deepcopy(template), "name": resource + "/revisions/" + previous,
                        "conditions": [{"type": "Ready", "state": "CONDITION_SUCCEEDED"}]}
            keys = tuple(sorted(template))
            pin = rolling.ServicePin(service, value["uid"], 42, previous, PREVIOUS,
                rolling.template_sha(template, service), rolling.static_sha(value), rolling.sha(policy),
                rolling.sha({key: revision.get(key) for key in keys}), keys,
                tuple(rolling.pinned_traffic(service, value)),
                tuple(rolling.SecretPin(row["name"], row["valueSource"]["secretKeyRef"]["secret"],
                    row["valueSource"]["secretKeyRef"]["version"]) for row in env if "valueSource" in row))
            self.states[service], self.policies[service] = value, policy
            self.revisions[(service, previous)] = revision
            pins.append(pin)
        policies = rolling.PolicyPins("synthetic-expense:1", "a" * 64, "synthetic-model-policy:1", "c" * 64,
                                     "synthetic-model-key:2", "gemini-3.6-flash", rolling.release.MODEL_API_BASE)
        self.plan = rolling.RollingPlan(SOURCE, "sha256:" + "d" * 64, "11111111-1111-4111-8111-111111111111", 123,
            "c" * 32, "123456789012345678901", "synthetic-runtime:3", "synthetic-observer:4",
            "synthetic-migration:5", PREVIOUS, "e" * 64, policies, tuple(pins))

    def verify_global(self, plan):
        self.gates.append(plan.release)
        if self.finance_drift:
            raise rolling.RollingDenied("rolling_financial_state_changed_reconcile_only")

    def service(self, service):
        return copy.deepcopy(self.states[service])

    def iam(self, service):
        return copy.deepcopy(self.policies[service])

    def revision(self, service, name):
        if self.old_lost and name == self.plan.pin(service).prior_revision:
            return None
        return copy.deepcopy(self.revisions.get((service, rolling.short_revision(name, service))))

    def patch(self, service, body, mask):
        value = self.states[service]
        assert body["etag"] == value["etag"]
        self.writes.append((service, copy.deepcopy(body), mask))
        if "template" in body:
            value["template"] = copy.deepcopy(body["template"])
            name = rolling.short_revision(body["template"]["revision"], service)
            value["latestReadyRevision"] = value["name"] + "/revisions/" + name
            value["latestCreatedRevision"] = value["latestReadyRevision"]
            self.revisions[(service, name)] = {**copy.deepcopy(body["template"]),
                "name": value["latestReadyRevision"], "conditions": [{"type": "Ready", "state": "CONDITION_SUCCEEDED"}]}
        value["traffic"], value["trafficStatuses"] = copy.deepcopy(body["traffic"]), copy.deepcopy(body["traffic"])
        generation = int(value["generation"]) + 1
        value["generation"], value["observedGeneration"], value["etag"] = str(generation), str(generation), "etag-" + str(generation)
        operation = rolling.PARENT + "/operations/synthetic-" + str(len(self.writes))
        self.operations[operation] = {"name": operation, "done": not self.pause}
        if self.lose_ack:
            self.lose_ack = False
            raise RuntimeError("synthetic-sensitive-provider-error")
        return {"name": operation}

    def operation(self, name):
        self.polls.append(name)
        return copy.deepcopy(self.operations[name])

    def health(self, plan, service, revision, release_id, tag):
        self.health_checks.append((service, revision, release_id, tag))
        value = self.revisions[(service, revision)]
        env = rolling.environment(value)
        assert env["APP_RELEASE"]["value"] == release_id


@pytest.fixture
def context(tmp_path):
    tmp_path.chmod(0o700)
    boundary = FixtureBoundary()
    return boundary, rolling.Journal(tmp_path, boundary.plan)


def test_full_joined_stage_then_workers_then_api_without_migration_or_retirement(context):
    boundary, journal = context
    result = rolling.advance(boundary.plan, boundary, journal)
    assert result["rolling_release_complete"] is True and result["goal_complete"] is False
    assert [name for name, _, _ in boundary.writes] == [*rolling.release.SERVICES,
        *rolling.release.SERVICES[1:], rolling.release.SERVICES[0]]
    assert [mask for _, _, mask in boundary.writes] == ["template,traffic"] * 3 + ["traffic"] * 3
    for name, body, _mask in boundary.writes[:3]:
        assert rolling.environment(body["template"])["RAZORPAY_KEY_SECRET"]["value"] == "synthetic-sensitive-literal"
        active = [row for row in body["traffic"] if row.get("percent")]
        assert active == list(boundary.plan.pin(name).traffic)
        assert body["template"]["containers"][0]["command"] == ["python"]
        assert "app.migrate" not in json.dumps(body)
    assert all(result[key] == 0 for key in ("migrations", "data_transfers", "credential_retirements", "source_owner_creations"))
    assert all("synthetic-sensitive" not in file.read_text() for file in journal.directory.glob("*.json"))
    assert all((name, boundary.plan.pin(name).prior_revision) in boundary.revisions for name in rolling.release.SERVICES)


def test_each_new_patch_has_a_fresh_global_proof(context, monkeypatch):
    boundary, journal = context
    original_patch = boundary.patch
    proof_counts = []

    def patch(service, body, mask):
        proof_counts.append(len(boundary.gates))
        return original_patch(service, body, mask)

    monkeypatch.setattr(boundary, "patch", patch)
    assert rolling.advance(boundary.plan, boundary, journal)["rolling_release_complete"]
    assert proof_counts == [1, 2, 3, 4, 5, 6]
    assert len(boundary.gates) == 7  # The joined result has its own final proof.


def test_checked_actions_reobserve_live_state_with_one_joined_proof(context):
    boundary, journal = context
    assert rolling.advance(boundary.plan, boundary, journal)["rolling_release_complete"]
    boundary.gates.clear()
    boundary.health_checks.clear()

    assert rolling.advance(boundary.plan, boundary, journal)["rolling_release_complete"]
    assert len(boundary.gates) == 1
    assert len(boundary.health_checks) >= len(rolling.release.SERVICES)
    assert len(boundary.writes) == 6

    boundary.policies[API]["etag"] = "drifted-policy"
    with pytest.raises(rolling.RollingDenied):
        rolling.advance(boundary.plan, boundary, journal)
    assert len(boundary.writes) == 6


def test_checked_stage_does_not_bypass_global_gate_for_next_patch(context):
    boundary, journal = context
    first = boundary.plan.pin(API)
    assert rolling.action(boundary.plan, boundary, journal, first, "stage")
    assert journal.read("stage." + API + ".checked") is not None
    assert len(boundary.writes) == 1

    boundary.gates.clear()
    boundary.finance_drift = True
    next_service = rolling.release.SERVICES[1]
    with pytest.raises(rolling.RollingDenied, match="financial"):
        rolling.advance(boundary.plan, boundary, journal)
    assert len(boundary.gates) == 1
    assert len(boundary.writes) == 1
    assert journal.read("stage." + next_service + ".started") is None


def test_checked_only_advance_refuses_final_financial_drift(context):
    boundary, journal = context
    assert rolling.advance(boundary.plan, boundary, journal)["rolling_release_complete"]
    boundary.gates.clear()
    boundary.finance_drift = True

    with pytest.raises(rolling.RollingDenied, match="financial"):
        rolling.advance(boundary.plan, boundary, journal)
    assert len(boundary.gates) == 1
    assert len(boundary.writes) == 6


def test_checked_only_reconcile_keeps_one_joined_global_proof(context):
    boundary, journal = context
    assert rolling.advance(boundary.plan, boundary, journal)["rolling_release_complete"]
    boundary.gates.clear()
    boundary.health_checks.clear()

    assert rolling.reconcile(boundary.plan, boundary, journal)["native_mutations"] == 0
    assert len(boundary.gates) == 1
    assert len(boundary.health_checks) == len(rolling.release.SERVICES)
    assert len(boundary.writes) == 6

    boundary.finance_drift = True
    with pytest.raises(rolling.RollingDenied, match="financial"):
        rolling.reconcile(boundary.plan, boundary, journal)
    assert len(boundary.writes) == 6


def test_pending_same_lro_is_polled_without_another_patch(context):
    boundary, journal = context
    boundary.pause = True
    first = rolling.advance(boundary.plan, boundary, journal)
    assert first["status"] == "same_native_operation_pending" and len(boundary.writes) == 1
    boundary.gates.clear()
    assert rolling.advance(boundary.plan, boundary, journal) == first
    assert len(boundary.gates) == 1  # A known ACK remains unresolved and needs a fresh proof.
    assert len(boundary.writes) == 1 and len(set(boundary.polls)) == 1
    boundary.pause = False
    for value in boundary.operations.values():
        value["done"] = True
    assert rolling.advance(boundary.plan, boundary, journal)["rolling_release_complete"] is True
    assert len(boundary.writes) == 6


def test_unknown_ack_is_never_replayed_and_explicit_reconcile_starts_no_next_action(context):
    boundary, journal = context
    boundary.lose_ack = True
    with pytest.raises(RuntimeError):
        rolling.advance(boundary.plan, boundary, journal)
    assert len(boundary.writes) == 1
    with pytest.raises(rolling.RollingDenied, match="unknown_mutation"):
        rolling.advance(boundary.plan, boundary, journal)
    assert len(boundary.writes) == 1
    assert rolling.reconcile(boundary.plan, boundary, journal)["native_mutations"] == 0
    assert len(boundary.writes) == 1
    assert rolling.advance(boundary.plan, boundary, journal)["rolling_release_complete"] is True
    assert len(boundary.writes) == 6


def test_unknown_ack_reconcile_still_requires_global_proof(context):
    boundary, journal = context
    boundary.lose_ack = True
    with pytest.raises(RuntimeError):
        rolling.advance(boundary.plan, boundary, journal)
    assert len(boundary.writes) == 1

    boundary.gates.clear()
    boundary.finance_drift = True
    with pytest.raises(rolling.RollingDenied, match="financial"):
        rolling.reconcile(boundary.plan, boundary, journal)
    assert len(boundary.gates) == 1
    assert len(boundary.writes) == 1


def test_native_failed_operation_does_not_become_successful_resource_reconciliation(context):
    boundary, journal = context
    boundary.pause = True
    rolling.advance(boundary.plan, boundary, journal)
    next(iter(boundary.operations.values())).update(done=True, error={"synthetic_error": True})
    for method in (rolling.advance, rolling.reconcile):
        with pytest.raises(rolling.RollingDenied, match="operation_failed"):
            method(boundary.plan, boundary, journal)
    assert len(boundary.writes) == 1


def test_finance_drift_and_missing_rollback_revision_refuse_before_patch(context):
    boundary, journal = context
    boundary.finance_drift = True
    with pytest.raises(rolling.RollingDenied, match="financial"):
        rolling.advance(boundary.plan, boundary, journal)
    boundary.finance_drift, boundary.old_lost = False, True
    with pytest.raises(rolling.RollingDenied, match="rollback_revision_missing"):
        rolling.advance(boundary.plan, boundary, journal)
    assert boundary.writes == []


def test_explicit_rollback_is_traffic_only_and_cannot_silently_promote_again(context):
    boundary, journal = context
    rolling.advance(boundary.plan, boundary, journal)
    assert rolling.rollback(boundary.plan, boundary, journal)["status"] == "prior_traffic_restored_schema18"
    assert [name for name, _, _ in boundary.writes[6:]] == [rolling.release.SERVICES[0],
        *reversed(rolling.release.SERVICES[1:])]
    assert all(mask == "traffic" and "template" not in body for _, body, mask in boundary.writes[6:])
    for pin in boundary.plan.services:
        active = [row for row in boundary.states[pin.service]["traffic"] if row.get("percent")]
        assert active == list(pin.traffic)
    with pytest.raises(rolling.RollingDenied, match="rollback_in_progress"):
        rolling.advance(boundary.plan, boundary, journal)
    assert len(boundary.writes) == 9


@pytest.mark.parametrize("change", [
    lambda value: value["template"].update(serviceAccount="wrong@ai-resume-parser-482412.iam.gserviceaccount.com"),
    lambda value: value["template"]["containers"][0]["env"].append({"name": "OPTIONAL_AI_GENERATION_ENABLED", "value": "true"}),
    lambda value: value["template"]["containers"][0]["env"].append({"name": "DATABASE_URL", "value": "synthetic-duplicate"}),
    lambda value: value["template"]["containers"][0]["env"][3]["valueSource"]["secretKeyRef"].update(secret="other-secret"),
    lambda value: value["template"]["containers"][0]["ports"][0].update(containerPort=1234),
    lambda value: value.update(reconciling=True),
    lambda value: value.update(uid="11111111-1111-4111-8111-111111111111"),
    lambda value: value.update(generation="43"),
])
def test_baseline_config_identity_duplicate_and_secret_drift_refused(context, change):
    boundary, journal = context
    change(boundary.states[rolling.release.SERVICES[0]])
    with pytest.raises(rolling.RollingDenied):
        rolling.advance(boundary.plan, boundary, journal)
    assert boundary.writes == []


def test_changed_native_preimage_refuses_before_once_marker_and_patch(context, monkeypatch):
    boundary, journal = context
    original, calls = boundary.service, []
    def read(service):
        calls.append(service)
        value = original(service)
        if len(calls) == 2:
            value["etag"] = "concurrent-edit"
        return value
    monkeypatch.setattr(boundary, "service", read)
    with pytest.raises(rolling.RollingDenied, match="preimage_changed"):
        rolling.advance(boundary.plan, boundary, journal)
    assert not list(journal.directory.glob("*.started.json")) and boundary.writes == []


@pytest.mark.parametrize("change", [
    {"runtime_secret": "synthetic-runtime:latest"}, {"runtime_uid": "123"}, {"ci_run_id": True},
    {"financial_sha256": ""}, {"workers_release": "latest"}, {"image_digest": "latest"},
])
def test_no_blank_or_caller_waiver_plan_can_bypass_fixed_native_classes(context, change):
    boundary, _ = context
    with pytest.raises(rolling.RollingDenied):
        replace(boundary.plan, **change).validate()


def test_plan_round_trip_keeps_only_public_metadata(context):
    boundary, _ = context
    raw = rolling.canonical(asdict(boundary.plan))
    assert native.load_plan(raw) == boundary.plan
    assert b"synthetic-sensitive" not in raw


def test_journal_different_plan_private_modes_symlink_and_once_collision(context, tmp_path):
    boundary, journal = context
    journal.record("synthetic", {"native_write_outcome": "unknown", "replay_authorized": False})
    with pytest.raises(FileExistsError):
        journal.record("synthetic", {"replay_authorized": False})
    other = rolling.Journal(tmp_path, replace(boundary.plan, nonce="d" * 32))
    with pytest.raises(rolling.RollingDenied):
        other.read("synthetic")
    (tmp_path / "linked.json").symlink_to(tmp_path / "synthetic.json")
    with pytest.raises(OSError):
        journal.read("linked")
    (tmp_path / "synthetic.json").chmod(0o644)
    with pytest.raises(rolling.RollingDenied):
        journal.read("synthetic")


@pytest.mark.parametrize("member", ["allUsers", "allAuthenticatedUsers", "serviceAccount:other@example.test"])
def test_worker_iam_cannot_be_made_public_or_arbitrary(context, member):
    boundary, _ = context
    for service in rolling.release.SERVICES[1:]:
        policy = copy.deepcopy(boundary.policies[service])
        policy["bindings"][0]["members"].append(member)
        with pytest.raises(rolling.RollingDenied):
            rolling.worker_iam(service, policy)


def test_reconcile_on_unused_journal_has_no_provider_mutations(context):
    boundary, journal = context
    assert rolling.reconcile(boundary.plan, boundary, journal)["native_mutations"] == 0
    assert boundary.writes == []


def test_financial_preservation_digest_covers_duplicate_rows_without_exposing_values():
    class Result:
        def __init__(self, rows):
            self.rows = rows
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return None
        def __iter__(self):
            return iter(self.rows)
    class Connection:
        def __init__(self, changed=False):
            self.changed, self.queries, self.count = changed, [], 0
        def execution_options(self, **kwargs):
            assert kwargs == {"stream_results": True}
            return self
        def execute(self, statement):
            query = str(statement)
            self.queries.append(query)
            assert query.startswith("SELECT ") and ";" not in query
            self.count += 1
            return Result([("a" * 64,), ("b" * 64 if self.changed and self.count == 1 else "a" * 64,)])
    first, same, changed = Connection(), Connection(), Connection(True)
    assert native.financial_digest(first) == native.financial_digest(same)
    assert native.financial_digest(first) != native.financial_digest(changed)
    assert len(first.queries) == 2 * (len(native.FINANCIAL_TABLES) + 1)
    assert "SELECT id,ai_credits,job_service_credits,tier,premium_until FROM public.users" in first.queries[-1]
    assert "password_hash" not in first.queries[-1]


def test_source_and_policy_reuse_does_not_require_enabling_retired_migration_identity(context, monkeypatch):
    boundary, _ = context
    adapter = native.NativeRollingBoundary(ROOT)
    calls = []
    for name in ("verify_source_image", "verify_policy_configuration", "verify_admission_closed", "verify_document_configuration"):
        monkeypatch.setattr(adapter.observer, name, lambda *args, _name=name, **kwargs: calls.append((_name, args, kwargs)))
    monkeypatch.setattr(adapter, "_identity", lambda uid: calls.append(("identity", uid)))
    monkeypatch.setattr(adapter, "_target", lambda *refs: boundary.plan.financial_sha256)
    monkeypatch.setattr(adapter, "service", boundary.service)
    monkeypatch.setattr(adapter, "revision", boundary.revision)
    monkeypatch.setattr(adapter, "iam", boundary.iam)
    monkeypatch.setattr(adapter, "_reference", lambda secret, version: version)
    monkeypatch.setattr(adapter.observer, "verify_identity_and_policy",
                        lambda *args: pytest.fail("Fresh/migration actor admission must not be reused"))
    adapter.verify_global(boundary.plan)
    assert [call[0] for call in calls] == ["verify_source_image", "verify_policy_configuration",
        "verify_admission_closed", "identity", "verify_document_configuration"]
    assert calls[0][2]["release"] == SOURCE
    assert calls[-1][1][0].release == PREVIOUS


def test_enabled_migration_secret_stops_before_any_database_credential_access(context, monkeypatch):
    boundary, _ = context
    adapter = native.NativeRollingBoundary(ROOT)
    monkeypatch.setattr(adapter.observer, "_module", lambda *args: SimpleNamespace(neon_identity=SimpleNamespace()))
    monkeypatch.setattr(adapter.observer, "describe_release_secret", lambda *args: {
        "name": "projects/157225724590/secrets/synthetic-migration/versions/5", "state": "ENABLED"})
    monkeypatch.setattr(adapter.observer, "access_release_secret", lambda *args: pytest.fail("No password may be accessed"))
    with pytest.raises(rolling.RollingDenied, match="migrator_must_remain_retired"):
        adapter._target(boundary.plan.runtime_secret, boundary.plan.observer_secret,
                        boundary.plan.retired_migration_secret)


def test_native_secret_alias_pinning_uses_actual_numeric_resource_and_refuses_drift(monkeypatch):
    adapter = native.NativeRollingBoundary(ROOT)
    metadata = {"name": "projects/157225724590/secrets/synthetic-runtime/versions/8", "state": "ENABLED", "etag": "synthetic"}
    monkeypatch.setattr(adapter.observer, "_cloud", lambda *args: metadata)
    assert adapter._reference("synthetic-runtime", "latest") == "8"
    metadata["name"] = "projects/157225724590/secrets/different-secret/versions/8"
    with pytest.raises(rolling.RollingDenied):
        adapter._reference("synthetic-runtime", "latest")


def _replace_service_pin(plan, index, **changes):
    pins = list(plan.services)
    pins[index] = replace(pins[index], **changes)
    return replace(plan, services=tuple(pins))


@pytest.mark.parametrize("traffic", [
    [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": API + "-unchecked", "percent": 100}],
    [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": API + "-00001-synthetic", "percent": 90},
     {"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": API + "-unchecked", "percent": 10}],
    [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST", "revision": API + "-00001-synthetic", "percent": 100}],
    [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": API + "-00001-synthetic", "tag": "preview"}],
])
def test_caller_json_cannot_restore_unchecked_latest_split_or_no_active_traffic(context, traffic):
    boundary, _ = context
    bad = _replace_service_pin(boundary.plan, 0, traffic=tuple(traffic))
    with pytest.raises(rolling.RollingDenied):
        native.load_plan(rolling.canonical(asdict(bad)))


def test_rollback_refuses_unchecked_active_revision_before_any_further_write(context):
    boundary, journal = context
    rolling.advance(boundary.plan, boundary, journal)
    traffic = copy.deepcopy(list(boundary.plan.services[0].traffic))
    traffic[0]["revision"] = API + "-unchecked"
    bad = _replace_service_pin(boundary.plan, 0, traffic=tuple(traffic))
    with pytest.raises(rolling.RollingDenied, match="active_traffic_not_prior_revision"):
        rolling.rollback(bad, boundary, journal)
    assert len(boundary.writes) == 6
    assert not list(journal.directory.glob("rollback.*.started.json"))


def test_existing_zero_percent_tags_preserved_while_prior_revision_remains_rollback_target(context):
    boundary, old_journal = context
    for index, pin in enumerate(boundary.plan.services):
        rows = copy.deepcopy(list(pin.traffic))
        rows[0]["tag"] = "retained-active"
        rows.append({"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
                     "revision": pin.service + "-older-preview", "tag": "retained-preview"})
        rows = sorted(rows, key=rolling.canonical)
        boundary.plan = _replace_service_pin(boundary.plan, index, traffic=tuple(rows))
        boundary.states[pin.service]["traffic"] = copy.deepcopy(rows)
        boundary.states[pin.service]["trafficStatuses"] = copy.deepcopy(rows)
    journal = rolling.Journal(old_journal.directory, boundary.plan)
    rolling.advance(boundary.plan, boundary, journal)
    rolling.rollback(boundary.plan, boundary, journal)
    for service, body, _ in boundary.writes:
        tags = {row["tag"]: row["revision"] for row in body["traffic"] if "tag" in row}
        assert tags["retained-active"] == boundary.plan.pin(service).prior_revision
        assert tags["retained-preview"] == service + "-older-preview"
    for service in rolling.release.SERVICES:
        active = [row for row in boundary.states[service]["traffic"] if row.get("percent")]
        assert len(active) == 1 and active[0]["revision"] == boundary.plan.pin(service).prior_revision


@pytest.fixture
def capture_context(context, monkeypatch):
    boundary, _ = context
    adapter = native.NativeRollingBoundary(ROOT)
    monkeypatch.setattr(adapter.observer, "verify_source_image", lambda **kwargs: None)
    monkeypatch.setattr(adapter.observer, "verify_admission_closed", lambda: None)
    monkeypatch.setattr(adapter.observer, "_cloud", lambda *args: {"uniqueId": boundary.plan.runtime_uid})
    monkeypatch.setattr(adapter.observer, "_secret", lambda reference: "synthetic-policy-payload")
    monkeypatch.setattr(adapter, "_identity", lambda uid: None)
    monkeypatch.setattr(adapter, "service", boundary.service)
    monkeypatch.setattr(adapter, "iam", boundary.iam)
    monkeypatch.setattr(adapter, "revision", boundary.revision)
    monkeypatch.setattr(adapter, "_reference", lambda secret, selector: selector)
    monkeypatch.setattr(adapter, "_target", lambda *args: pytest.fail("Invalid capture must not access SQL"))
    monkeypatch.setattr(adapter, "verify_global", lambda plan: pytest.fail("Invalid capture must stop first"))
    monkeypatch.setattr(adapter, "patch", lambda *args: pytest.fail("Capture never writes providers"))
    plan = boundary.plan
    kwargs = dict(commit=plan.release, image_digest=plan.image_digest, build_id=plan.build_id,
        ci_run_id=plan.ci_run_id, nonce=plan.nonce, observer_secret=plan.observer_secret,
        retired_migration_secret=plan.retired_migration_secret, workers_release=plan.workers_release)
    return boundary, adapter, kwargs


@pytest.mark.parametrize("index", range(3))
def test_native_capture_refuses_latest_ready_that_is_not_the_single_active_target(capture_context, index):
    boundary, adapter, kwargs = capture_context
    service = rolling.release.SERVICES[index]
    current = boundary.states[service]
    current["latestReadyRevision"] = current["name"] + "/revisions/" + service + "-new-no-traffic"
    current["latestCreatedRevision"] = current["latestReadyRevision"]
    with pytest.raises(rolling.RollingDenied, match="active_traffic_not_prior_revision"):
        adapter.capture(**kwargs)
    assert boundary.writes == []


POLICY_SECRET_CASES = [
    *((index, "HIREWIZ_EXPENSE_POLICY_JSON") for index in range(3)),
    *((index, name) for index in range(2) for name in ("LLM_MODEL_COST_POLICY_JSON", "LLM_API_KEY")),
]


@pytest.mark.parametrize("index,name", POLICY_SECRET_CASES)
def test_plan_secret_pins_cannot_bypass_each_service_reviewed_policy(context, index, name):
    boundary, _ = context
    secrets = tuple(replace(row, secret="synthetic-unreviewed") if row.environment_name == name else row
                    for row in boundary.plan.services[index].secrets)
    bad = _replace_service_pin(boundary.plan, index, secrets=secrets)
    with pytest.raises(rolling.RollingDenied, match="service_policy_binding_invalid"):
        native.load_plan(rolling.canonical(asdict(bad)))


@pytest.mark.parametrize("name", rolling.release.MODEL_ENV)
def test_employer_plan_cannot_pin_model_fields_even_when_they_match_the_reviewed_model(context, name):
    boundary, _ = context
    pin = boundary.plan.services[2]
    bad = _replace_service_pin(boundary.plan, 2,
        secrets=(*pin.secrets, rolling.SecretPin(name, "synthetic-model-key", "2")))
    with pytest.raises(rolling.RollingDenied, match="service_policy_binding_invalid"):
        bad.validate()


@pytest.mark.parametrize("index,name", POLICY_SECRET_CASES + [
    (0, "LLM_MODEL"), (1, "LLM_MODEL"), (0, "LLM_API_BASE"), (1, "LLM_API_BASE"),
    *((2, name) for name in rolling.release.MODEL_ENV),
])
def test_stage_cannot_preserve_unreviewed_per_service_policy_or_model(context, index, name):
    boundary, _ = context
    pin = boundary.plan.services[index]
    value = boundary.service(pin.service)
    env = rolling.environment(value["template"])
    if name not in env:
        value["template"]["containers"][0]["env"].append({"name": name, "value": "synthetic-unreviewed"})
    elif "valueSource" in env[name]:
        env[name]["valueSource"]["secretKeyRef"]["secret"] = "synthetic-unreviewed"
    else:
        env[name]["value"] = "synthetic-unreviewed"
    pin = replace(pin, template_sha256=rolling.template_sha(value["template"], pin.service))
    with pytest.raises(rolling.RollingDenied, match="rolling_service_(policy|model)"):
        rolling.stage_body(boundary.plan, pin, value)
    assert boundary.writes == []


@pytest.mark.parametrize("index,name", [(1, "HIREWIZ_EXPENSE_POLICY_JSON"),
    (2, "HIREWIZ_EXPENSE_POLICY_JSON"), (1, "LLM_MODEL_COST_POLICY_JSON"),
    (1, "LLM_API_KEY"), (1, "LLM_MODEL"), (1, "LLM_API_BASE"), (2, "LLM_MODEL")])
def test_native_capture_refuses_service_policy_drift_before_sql_or_plan_publication(capture_context, index, name):
    boundary, adapter, kwargs = capture_context
    value = boundary.states[rolling.release.SERVICES[index]]
    env = rolling.environment(value["template"])
    if name not in env:
        value["template"]["containers"][0]["env"].append({"name": name, "value": "synthetic-unreviewed"})
    elif "valueSource" in env[name]:
        env[name]["valueSource"]["secretKeyRef"]["secret"] = "synthetic-unreviewed"
    else:
        env[name]["value"] = "synthetic-unreviewed"
    with pytest.raises(rolling.RollingDenied, match="rolling_service_(policy|model)"):
        adapter.capture(**kwargs)
    assert boundary.writes == []


@pytest.mark.parametrize("old_revision", [False, True])
@pytest.mark.parametrize("index,name", [(0, "HIREWIZ_EXPENSE_POLICY_JSON"),
    (1, "LLM_MODEL_COST_POLICY_JSON"), (1, "LLM_API_KEY"), (1, "LLM_MODEL"),
    (1, "LLM_API_BASE"), (2, "HIREWIZ_EXPENSE_POLICY_JSON"), (2, "LLM_MODEL")])
def test_global_rechecks_current_and_rollback_policy_bindings_before_sql(context, monkeypatch, index, name, old_revision):
    boundary, _ = context
    adapter = native.NativeRollingBoundary(ROOT)
    for method in ("verify_source_image", "verify_policy_configuration", "verify_admission_closed",
                   "verify_document_configuration"):
        monkeypatch.setattr(adapter.observer, method, lambda *args, **kwargs: None)
    monkeypatch.setattr(adapter, "_identity", lambda uid: None)
    monkeypatch.setattr(adapter, "service", boundary.service)
    monkeypatch.setattr(adapter, "iam", boundary.iam)
    monkeypatch.setattr(adapter, "revision", boundary.revision)
    monkeypatch.setattr(adapter, "_reference", lambda secret, selector: selector)
    monkeypatch.setattr(adapter, "_target", lambda *args: pytest.fail("Drift must be refused before SQL"))
    pin = boundary.plan.services[index]
    template = (boundary.revisions[(pin.service, pin.prior_revision)] if old_revision
                else boundary.states[pin.service]["template"])
    env = rolling.environment(template)
    if name not in env:
        template["containers"][0]["env"].append({"name": name, "value": "synthetic-unreviewed"})
    elif "valueSource" in env[name]:
        env[name]["valueSource"]["secretKeyRef"]["secret"] = "synthetic-unreviewed"
    else:
        env[name]["value"] = "synthetic-unreviewed"
    with pytest.raises(rolling.RollingDenied, match="rolling_service_(policy|model)"):
        adapter.verify_global(boundary.plan)
    assert boundary.writes == []


def _candidate_policy_plan(boundary):
    candidate = replace(boundary.plan.policies, expense_secret="synthetic-expense:2",
                        expense_sha256="2" * 64, model_policy_secret="synthetic-model-policy:2",
                        model_policy_sha256="3" * 64)
    plan = replace(boundary.plan, candidate_policies=candidate)
    pins = tuple(replace(pin, candidate_template_sha256=rolling.template_sha(
        rolling.stage_body(plan, pin, boundary.service(pin.service))["template"], pin.service))
        for pin in plan.services)
    return replace(plan, services=pins)


def _native_policy_adapter(boundary, monkeypatch):
    adapter = native.NativeRollingBoundary(ROOT)
    for method in ("verify_source_image", "verify_admission_closed", "verify_document_configuration"):
        monkeypatch.setattr(adapter.observer, method, lambda *args, **kwargs: None)
    monkeypatch.setattr(adapter, "_identity", lambda uid: None)
    monkeypatch.setattr(adapter, "service", boundary.service)
    monkeypatch.setattr(adapter, "iam", boundary.iam)
    monkeypatch.setattr(adapter, "revision", boundary.revision)
    monkeypatch.setattr(adapter, "_reference", lambda secret, selector: selector)
    monkeypatch.setattr(adapter, "_target", lambda *args: boundary.plan.financial_sha256)
    return adapter


def test_candidate_policy_release_preserves_prior_refs_and_rollback_without_revalidating_expired_prior(context, monkeypatch):
    boundary, old_journal = context
    original = copy.deepcopy(boundary.revisions)
    boundary.plan = _candidate_policy_plan(boundary)
    boundary.plan.validate()
    assert native.load_plan(rolling.canonical(asdict(boundary.plan))) == boundary.plan
    adapter = _native_policy_adapter(boundary, monkeypatch)
    validated = []
    def policy_check(policies):
        # The historical policy has expired; it may be retained, never fund work.
        assert policies == boundary.plan.candidate_policies
        validated.append(policies)
    monkeypatch.setattr(adapter.observer, "verify_policy_configuration", policy_check)
    monkeypatch.setattr(boundary, "verify_global", adapter.verify_global)
    journal = rolling.Journal(old_journal.directory, boundary.plan)
    assert rolling.advance(boundary.plan, boundary, journal)["rolling_release_complete"]
    for service, body, mask in boundary.writes[:3]:
        assert mask == "template,traffic"
        env = rolling.environment(body["template"])
        assert env["HIREWIZ_EXPENSE_POLICY_JSON"]["valueSource"]["secretKeyRef"]["version"] == "2"
        if service != rolling.release.SERVICES[2]:
            assert env["LLM_MODEL_COST_POLICY_JSON"]["valueSource"]["secretKeyRef"]["version"] == "2"
        assert all(env.get(flag, {"value": "false"})["value"] == "false" for flag in rolling.OFF)
    assert rolling.rollback(boundary.plan, boundary, journal)["queue_admission"] == "closed"
    assert validated
    assert all(boundary.revisions[key] == value for key, value in original.items())
    assert all(mask == "traffic" for _, _, mask in boundary.writes[6:])


@pytest.mark.parametrize("phase", ["prior", "candidate", "rollback"])
def test_candidate_policy_global_refuses_wrong_phase_or_any_template_drift(context, monkeypatch, phase):
    boundary, _ = context
    boundary.plan = _candidate_policy_plan(boundary)
    adapter = _native_policy_adapter(boundary, monkeypatch)
    monkeypatch.setattr(adapter.observer, "verify_policy_configuration", lambda policies: None)
    pin = boundary.plan.services[0]
    if phase == "candidate":
        boundary.states[pin.service]["template"] = rolling.stage_body(
            boundary.plan, pin, boundary.service(pin.service))["template"]
    template = (boundary.revisions[(pin.service, pin.prior_revision)] if phase == "rollback"
                else boundary.states[pin.service]["template"])
    rolling.environment(template)["HIREWIZ_EXPENSE_POLICY_JSON"]["valueSource"]["secretKeyRef"]["version"] = "9"
    monkeypatch.setattr(adapter, "_target", lambda *args: pytest.fail("Drift precedes SQL"))
    with pytest.raises(rolling.RollingDenied):
        adapter.verify_global(boundary.plan)
    assert boundary.writes == []


@pytest.mark.parametrize("kwargs", [
    {"candidate_expense_secret": "synthetic-expense:2"},
    {"candidate_model_policy_secret": "synthetic-model-policy:2"},
    {"candidate_expense_secret": "synthetic-expense:latest", "candidate_model_policy_secret": "synthetic-model-policy:2"},
])
def test_capture_candidate_pair_is_explicit_numeric_and_complete_before_native_access(capture_context, monkeypatch, kwargs):
    _, adapter, base = capture_context
    monkeypatch.setattr(adapter.observer, "verify_source_image", lambda **kwargs: pytest.fail("No native access"))
    with pytest.raises(rolling.RollingDenied, match="candidate_policy"):
        adapter.capture(**base, **kwargs)


def test_capture_validates_candidate_before_sql_and_preserves_observed_prior_refs(capture_context, monkeypatch):
    boundary, adapter, kwargs = capture_context
    validations = []
    monkeypatch.setattr(adapter.observer, "verify_policy_configuration", lambda policies: validations.append(policies))
    monkeypatch.setattr(adapter, "_target", lambda *args: boundary.plan.financial_sha256 if validations else pytest.fail("Policy first"))
    monkeypatch.setattr(adapter, "verify_global", lambda plan: None)
    plan = adapter.capture(**kwargs, candidate_expense_secret="synthetic-expense:2",
                           candidate_model_policy_secret="synthetic-model-policy:2")
    plan.validate()
    assert len(validations) == 1 and validations[0] == plan.candidate_policies
    assert plan.policies.expense_secret == "synthetic-expense:1"
    assert plan.candidate_policies.expense_secret == "synthetic-expense:2"
    assert all(pin.candidate_template_sha256 for pin in plan.services)
    assert all(next(row for row in pin.secrets if row.environment_name == "HIREWIZ_EXPENSE_POLICY_JSON").version == "1"
               for pin in plan.services)
    assert boundary.writes == []


@pytest.mark.parametrize("reason", ["expired_policy", "source_fallback_missing_quote", "hash_mismatch"])
def test_candidate_payload_refusal_stops_capture_before_sql_or_plan(capture_context, monkeypatch, reason):
    boundary, adapter, kwargs = capture_context
    def reject(_policies):
        raise rolling.release.ReleaseDenied(reason)
    monkeypatch.setattr(adapter.observer, "verify_policy_configuration", reject)
    with pytest.raises(rolling.release.ReleaseDenied, match=reason):
        adapter.capture(**kwargs, candidate_expense_secret="synthetic-expense:2",
                        candidate_model_policy_secret="synthetic-model-policy:2")
    assert boundary.writes == []


@pytest.mark.parametrize("change", [
    {"model_key_secret": "synthetic-replacement-key:3"}, {"model_primary": "gemini-unreviewed"},
    {"model_api_base": "https://unreviewed.example"}, {"expense_secret": "synthetic-expense:latest"},
])
def test_candidate_policy_pair_cannot_replace_model_or_credential_contract(context, change):
    boundary, _ = context
    plan = _candidate_policy_plan(boundary)
    with pytest.raises(rolling.RollingDenied, match="candidate_policy"):
        replace(plan, candidate_policies=replace(plan.candidate_policies, **change)).validate()


def test_candidate_unknown_patch_remains_reconcile_only_with_its_original_plan(context, monkeypatch):
    boundary, old_journal = context
    boundary.plan = _candidate_policy_plan(boundary)
    adapter = _native_policy_adapter(boundary, monkeypatch)
    monkeypatch.setattr(adapter.observer, "verify_policy_configuration", lambda policies: None)
    monkeypatch.setattr(boundary, "verify_global", adapter.verify_global)
    journal = rolling.Journal(old_journal.directory, boundary.plan)
    boundary.lose_ack = True
    with pytest.raises(RuntimeError):
        rolling.advance(boundary.plan, boundary, journal)
    with pytest.raises(rolling.RollingDenied, match="unknown_mutation"):
        rolling.advance(boundary.plan, boundary, journal)
    assert rolling.reconcile(boundary.plan, boundary, journal)["native_mutations"] == 0
    assert len(boundary.writes) == 1


@pytest.mark.parametrize("service", rolling.release.SERVICES)
def test_maintenance_updater_is_optional_only_on_reviewed_consumers_and_never_an_invoker(context, service):
    boundary, _ = context
    documents = rolling.release.documents
    policy = boundary.iam(service)
    policy["bindings"].append({"role": documents.MAINTENANCE_ROLE,
        "members": ["serviceAccount:" + documents.MAINTENANCE_ACCOUNT]})
    if service == rolling.release.SERVICES[2]:
        with pytest.raises(rolling.RollingDenied):
            rolling.worker_iam(service, policy)
    else:
        rolling.worker_iam(service, policy)
        policy["bindings"][-1]["role"] = "roles/run.invoker"
        with pytest.raises(rolling.RollingDenied):
            rolling.worker_iam(service, policy)


@pytest.mark.parametrize("broaden_role", [False, True])
def test_native_iam_reader_requires_real_keyless_account_and_exact_role_definition(context, monkeypatch, broaden_role):
    from backend.tests.test_document_worker_deployment import maintenance_snapshot

    boundary, _ = context
    documents = rolling.release.documents
    metadata = maintenance_snapshot(documents)
    if broaden_role:
        metadata["maintenance_role"]["includedPermissions"].append("run.services.setIamPolicy")
    service = rolling.release.SERVICES[1]
    policy = boundary.iam(service)
    policy["bindings"].append({"role": documents.MAINTENANCE_ROLE,
        "members": ["serviceAccount:" + documents.MAINTENANCE_ACCOUNT]})
    adapter, reads = native.NativeRollingBoundary(ROOT), []
    def cloud(*args):
        reads.append(args)
        if args[:3] == ("run", "services", "get-iam-policy"):
            return copy.deepcopy(policy)
        if args[:3] == ("iam", "service-accounts", "describe"):
            assert args[3] == documents.MAINTENANCE_ACCOUNT
            return metadata["maintenance_service_account"]
        if args[:4] == ("iam", "service-accounts", "keys", "list"):
            assert args[-1] == documents.MAINTENANCE_ACCOUNT
            return metadata["maintenance_service_account_keys"]["keys"]
        assert args == ("iam", "roles", "describe", "hirewizDocumentServiceMaintenance")
        return metadata["maintenance_role"]
    monkeypatch.setattr(adapter.observer, "_cloud", cloud)
    if broaden_role:
        with pytest.raises(rolling.RollingDenied, match="maintenance_authority"):
            adapter.iam(service)
    else:
        assert adapter.iam(service) == policy
        assert rolling.sha(policy) != boundary.plan.pin(service).iam_sha256
    assert len(reads) == 4 and boundary.writes == []


def test_maintenance_grant_after_capture_changes_full_iam_hash_and_refuses_patch(context):
    boundary, journal = context
    documents = rolling.release.documents
    boundary.policies[rolling.release.SERVICES[1]]["bindings"].append({"role": documents.MAINTENANCE_ROLE,
        "members": ["serviceAccount:" + documents.MAINTENANCE_ACCOUNT]})
    with pytest.raises(rolling.RollingDenied):
        rolling.advance(boundary.plan, boundary, journal)
    # API may stage before its sibling's changed policy is encountered; no
    # action is sent for the service whose captured native IAM changed.
    assert all(service != rolling.release.SERVICES[1] for service, _, _ in boundary.writes)
