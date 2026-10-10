"""Sequencing/refusal tests; all cloud observations and mutations are synthetic."""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/gcp"))
spec = importlib.util.spec_from_file_location("hirewiz_release_test", ROOT / "infra/gcp/monetary_release.py")
assert spec and spec.loader
release = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = release
spec.loader.exec_module(release)
STAMP = datetime(2026, 10, 10, 0, 0, tzinfo=UTC)


@pytest.fixture
def plan(tmp_path):
    return release.Plan(
        release="a" * 40, image_digest="sha256:" + "b" * 64,
        build_id="11111111-1111-1111-1111-111111111111", ci_run_id=7,
        runtime_secret="hirewiz-runtime-db:2", migration_secret="hirewiz-migration-db:3",
        expense_secret="hirewiz-expense-policy:4", expense_sha256="c" * 64,
        runtime_service_account=f"hirewiz-runtime@{release.PROJECT}.iam.gserviceaccount.com",
        migration_service_account=f"hirewiz-migration@{release.PROJECT}.iam.gserviceaccount.com",
        backup_uri=f"gs://{release.PROJECT}-hirewiz-application-artifacts/releases/reviewed.dump",
        inventory_path=tmp_path / "untrusted-inventory.json",
        inventory_sha256="d" * 64,
    )


class Boundary:
    def __init__(self):
        self.events = []
        self.schema = "20261008_0009"
        self.failure = None
        self.fence = release.Fence(STAMP, STAMP - timedelta(minutes=5), True, True, True)

    def event(self, name, *args):
        self.events.append((name, *args))
        if self.failure == (name, *args) or self.failure == name:
            raise release.ReleaseDenied("synthetic_observation_refusal")

    def verify_identity_and_policy(self, plan):
        self.event("identity")

    def verify_admission_closed(self):
        self.event("queues")

    def verify_fences(self, plan):
        self.event("fence")
        return self.fence

    def verify_database(self, plan, schema):
        self.event("database", schema)
        assert self.schema == schema

    def create_backup(self, plan, fence):
        self.event("backup-create")

    def verify_backup(self, plan, fence):
        self.event("backup")
        assert fence.retired_since < fence.observed_at

    def migrate(self, plan, expected, target):
        self.event("migrate", expected, target)
        assert self.schema == expected
        self.schema = target

    def stage(self, plan, service):
        self.event("stage", service)
        return service + "-00042-test"

    def verify_revision(self, plan, service, revision):
        self.event("revision", service)
        assert revision == service + "-00042-test"

    def promote(self, service, revision):
        self.event("promote", service)

    def verify_serving(self, plan, service, revision):
        self.event("serving", service)


def run(plan, boundary, *, execute=True):
    return release.run_release(plan, boundary, execute=execute, clock=lambda: STAMP)


def mutations(boundary):
    return [event for event in boundary.events if event[0] in {"backup-create", "migrate", "stage", "promote"}]


def test_observation_only_has_no_mutations(plan):
    boundary = Boundary()
    result = run(plan, boundary, execute=False)
    assert result["sequence_admitted"] is False and result["production_released"] is False
    assert result["post_fence_backup_creation_required"] is True
    assert mutations(boundary) == []
    assert boundary.events == [("identity",), ("queues",), ("fence",),
                               ("database", "20261008_0009")]


def test_complete_sequence_keeps_all_enablement_gates_closed(plan):
    boundary = Boundary()
    result = run(plan, boundary)
    assert result["schema"] == "20261009_0013"
    assert result["queue_admission"] == "closed" and result["goal_complete"] is False
    assert result["optional_generation"] is result["automatic_submission"] is result["native_lifecycle"] is False
    assert mutations(boundary) == [("backup-create",),
        ("migrate", "20261008_0009", "20261009_0010"),
        ("migrate", "20261009_0010", "20261009_0011"),
        ("migrate", "20261009_0011", "20261009_0012"),
        ("migrate", "20261009_0012", "20261009_0013"),
        *(('stage', service) for service in release.SERVICES),
        *(('promote', service) for service in (*release.SERVICES[1:], release.SERVICES[0])),
    ]
    for event in mutations(boundary):
        index = boundary.events.index(event)
        preceding = boundary.events[:index]
        assert ("identity",) in preceding and ("fence",) in preceding
    first_promote = boundary.events.index(("promote", release.SERVICES[1]))
    assert all(boundary.events[:first_promote].count(("revision", service)) == 2 for service in release.SERVICES)


@pytest.mark.parametrize("failure", ["identity", "queues", "fence", "backup", ("database", "20261008_0009")])
def test_precondition_refusal_performs_no_mutation(plan, failure):
    boundary = Boundary()
    boundary.failure = failure
    with pytest.raises(release.ReleaseDenied, match="synthetic_observation_refusal"):
        run(plan, boundary)
    assert mutations(boundary) == ([] if failure != "backup" else [("backup-create",)])


@pytest.mark.parametrize("changes", [
    {"observed_at": STAMP - timedelta(seconds=31)}, {"observed_at": STAMP + timedelta(seconds=1)},
    {"observed_at": STAMP.replace(tzinfo=None)}, {"retired_since": STAMP + timedelta(seconds=1)},
    {"retired_since": STAMP.replace(tzinfo=None)}, {"complete_external_consumers": False},
    {"old_provider_credentials_revoked": False}, {"old_cloud_writers_retired": False},
])
def test_missing_or_stale_fence_never_authorizes_a_mutation(plan, changes):
    boundary = Boundary()
    boundary.fence = replace(boundary.fence, **changes)
    with pytest.raises(release.ReleaseDenied, match="external_writer_or_provider_fence_not_verified"):
        run(plan, boundary)
    assert mutations(boundary) == []


@pytest.mark.parametrize("failure", [
    ("migrate", "20261008_0009", "20261009_0010"),
    ("migrate", "20261009_0012", "20261009_0013"),
    *(('stage', service) for service in release.SERVICES),
    *(('promote', service) for service in release.SERVICES),
])
def test_lost_or_refused_mutation_is_not_retried_or_rolled_back(plan, failure):
    boundary = Boundary()
    boundary.failure = failure
    with pytest.raises(release.ReleaseDenied):
        run(plan, boundary)
    assert boundary.events[-1] == failure
    assert boundary.events.count(failure) == 1
    assert not any(event[0] in {"downgrade", "resume_queue", "restore_database", "enable_generation"} for event in boundary.events)


def test_fence_loss_after_first_migration_stops_forward_execution(plan):
    class LoseFence(Boundary):
        def verify_fences(self, plan):
            fence = super().verify_fences(plan)
            return replace(fence, old_cloud_writers_retired=self.schema == "20261008_0009")
    boundary = LoseFence()
    with pytest.raises(release.ReleaseDenied):
        run(plan, boundary)
    assert mutations(boundary) == [("backup-create",), ("migrate", "20261008_0009", "20261009_0010")]


@pytest.mark.parametrize("changes", [
    {"runtime_secret": "hirewiz-runtime-db:latest"}, {"migration_secret": "hirewiz-runtime-db:3"},
    {"migration_service_account": f"hirewiz-runtime@{release.PROJECT}.iam.gserviceaccount.com"},
    {"release": "a" * 7}, {"image_digest": "latest"}, {"ci_run_id": True},
    {"backup_uri": "https://example.invalid/backup.dump"},
])
def test_ambiguous_or_unbounded_plan_stops_before_any_observation(plan, changes):
    boundary = Boundary()
    with pytest.raises(release.ReleaseDenied, match="release_plan_identity_invalid"):
        run(replace(plan, **changes), boundary)
    assert boundary.events == []


def test_native_fence_refuses_even_if_inventory_claims_everything_is_safe(plan, monkeypatch):
    plan.inventory_path.write_text(json.dumps({"provider_fencing_verified": True,
                                              "consumer_inventory_complete": True, "cutover_ready": True}))
    boundary = release.NativeBoundary(ROOT)
    monkeypatch.setattr(boundary, "_cloud", lambda *args: pytest.fail("A caller assertion must not dispatch cloud action"))
    with pytest.raises(release.ReleaseDenied, match="native_external_consumer_and_provider_fence_verifier_missing"):
        boundary.verify_fences(plan)


@pytest.mark.parametrize("state,tasks", [("RUNNING", []), ("PAUSED", [{}]), ("DISABLED", [])])
def test_native_queues_require_actual_paused_empty_observation(state, tasks, monkeypatch):
    boundary = release.NativeBoundary(ROOT)
    def cloud(*args):
        return {"state": state} if args[:3] == ("tasks", "queues", "describe") else tasks
    monkeypatch.setattr(boundary, "_cloud", cloud)
    with pytest.raises(release.ReleaseDenied, match="queue_not_paused_and_drained"):
        boundary.verify_admission_closed()


def test_raw_input_duplicate_keys_cannot_replace_a_pinned_reference():
    with pytest.raises(release.ReleaseDenied, match="duplicate_input_key"):
        release._json(b'{"runtime_secret":"one:1","runtime_secret":"two:2"}')


def test_legacy_release_still_refuses_the_current_schema14_chain():
    tracked = {path.relative_to(ROOT).as_posix() for path in (ROOT / 'backend/alembic/versions').glob('*.py')}
    # The upload migration extends the source chain; it does not expand either
    # release authority's reviewed production migration allowance.
    assert release.source_guard.migration_chain(ROOT, tracked) == (*release.CHAIN, '20261010_0014')
    with pytest.raises(release.source_guard.PreflightDenied, match="only the reviewed schema0009 chain"):
        release.source_guard.migration_head(ROOT, tracked)


def native_identity(plan, monkeypatch, *, policy_update=None, ci_update=None, build_update=None):
    import hashlib

    from backend.tests.expense_policy_fixtures import estimated_expense_policy
    policy = estimated_expense_policy()
    policy.update(review_status="approved", reviewed_by="independent-synthetic-review")
    policy.update(policy_update or {})
    raw = json.dumps(policy)
    plan = replace(plan, expense_sha256=hashlib.sha256(raw.encode()).hexdigest())
    ci = {"headSha": plan.release, "workflowName": "CI", "status": "completed", "conclusion": "success",
          "jobs": [{"name": job, "status": "completed", "conclusion": "success"} for job in sorted(release.CI_JOBS)]}
    ci.update(ci_update or {})
    tag = plan.image.split('@')[0] + ':' + plan.release
    build = {"status": "SUCCESS", "projectId": release.PROJECT, "id": plan.build_id,
             "sourceProvenance": {"resolvedGitSource": {"url": f"https://github.com/{release.REPOSITORY}.git", "revision": plan.release}},
             "steps": [{"name": "gcr.io/cloud-builders/docker", "args": ["build", "--tag", tag, "."]},
                       {"name": "gcr.io/cloud-builders/docker", "args": ["push", tag]}],
             "results": {"images": [{"name": tag, "digest": plan.image_digest, "artifactRegistryPackage": "synthetic", "ociMediaType": "IMAGE_MANIFEST"}]}}
    build.update(build_update or {})
    boundary = release.NativeBoundary(ROOT)
    calls = []
    def command(executable, *args):
        calls.append((executable, *args))
        if args[0] == "api":
            return json.dumps({"path": ".github/workflows/ci.yml", "head_sha": plan.release,
                               "event": "pull_request", "repository": {"full_name": release.REPOSITORY}}).encode()
        return json.dumps(ci).encode()
    def cloud(*args):
        calls.append(("cloud", *args))
        if args[:2] == ("builds", "describe"):
            return build
        return {"email": args[3], "disabled": False}
    monkeypatch.setattr(release.source_guard, "checked_files", lambda *args: set())
    monkeypatch.setattr(release.source_guard, "migration_chain", lambda *args: release.CHAIN)
    monkeypatch.setattr(boundary, "_command", command)
    monkeypatch.setattr(boundary, "_cloud", cloud)
    monkeypatch.setattr(boundary, "_secret", lambda *args: raw)
    return plan, boundary, calls


def test_native_identity_uses_the_authoritative_cost_guard_and_restores_environment(plan, monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("HIREWIZ_EXPENSE_POLICY_JSON", "synthetic-prior-environment")
    plan, boundary, calls = native_identity(plan, monkeypatch)
    boundary.verify_identity_and_policy(plan)
    import os
    assert os.environ["APP_ENV"] == "test"
    assert os.environ["HIREWIZ_EXPENSE_POLICY_JSON"] == "synthetic-prior-environment"
    assert sum(call[:3] == ("gh", "run", "view") for call in calls) == 1
    assert sum(call[:2] == ("gh", "api") for call in calls) == 1
    assert not any("deploy" in call or "execute" in call for call in calls)


def test_native_image_accepts_reviewed_combined_tag_flag_from_cloudbuild(plan, monkeypatch):
    tag = plan.image.split('@')[0] + ':' + plan.release
    steps = [{"name": "gcr.io/cloud-builders/docker", "args": ["build", "--tag=" + tag, "."]},
             {"name": "gcr.io/cloud-builders/docker", "args": ["push", tag]}]
    plan, boundary, _ = native_identity(plan, monkeypatch, build_update={"steps": steps})
    boundary.verify_identity_and_policy(plan)


@pytest.mark.parametrize("update", [
    {"headSha": "f" * 40}, {"status": "in_progress"}, {"conclusion": "failure"}, {"workflowName": "other"},
    {"jobs": []},
    {"jobs": [{"name": job, "status": "completed", "conclusion": "success"} for job in sorted(release.CI_JOBS)] + [{"name": "backend", "status": "completed", "conclusion": "success"}]},
])
def test_native_ci_head_status_complete_unique_workflow_jobs_are_required(plan, monkeypatch, update):
    plan, boundary, calls = native_identity(plan, monkeypatch, ci_update=update)
    with pytest.raises(release.ReleaseDenied, match="exact_release_five_ci_jobs_not_successful"):
        boundary.verify_identity_and_policy(plan)
    assert not any(call[:2] == ("cloud", "builds") for call in calls)


@pytest.mark.parametrize("update", [
    {"sourceProvenance": {"resolvedStorageSource": {"bucket": "synthetic"}}},
    {"sourceProvenance": {"resolvedGitSource": {"url": "https://example.invalid/other.git", "revision": "a" * 40}}},
    {"results": {"images": []}}, {"status": "WORKING"}, {"projectId": "other"},
    {"steps": [{"name": "gcr.io/cloud-builders/docker", "script": "synthetic unwanted shell", "args": ["build", "backend"]}]},
    {"options": {"env": ["SYNTHETIC=unreviewed"]}},
])
def test_native_image_labels_or_unreviewed_build_inputs_are_not_source_provenance(plan, monkeypatch, update):
    plan, boundary, _ = native_identity(plan, monkeypatch, build_update=update)
    with pytest.raises(release.ReleaseDenied, match="native_exact_source_image_provenance_not_verified"):
        boundary.verify_identity_and_policy(plan)


@pytest.mark.parametrize("update", [
    {"review_status": "candidate"}, {"reviewed_by": "operator-review-required"},
    {"provenance": "synthetic_test"}, {"all_enabled_payment_methods_covered": False},
    {"expires_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat()},
])
def test_native_policy_does_not_weaken_existing_production_funding_guard(plan, monkeypatch, update):
    from app.billing.cost_policy import CostPolicyUnavailable
    plan, boundary, _ = native_identity(plan, monkeypatch, policy_update=update)
    with pytest.raises(CostPolicyUnavailable):
        boundary.verify_identity_and_policy(plan)


def test_native_staging_uses_numeric_roles_false_flags_and_preserves_topic_delimiter(plan, monkeypatch):
    boundary = release.NativeBoundary(ROOT)
    calls = []
    service = release.SERVICES[2]
    def cloud(*args):
        calls.append(args)
        return {"status": {"latestCreatedRevisionName": service + "-00042-test"}}
    monkeypatch.setattr(boundary, "_cloud", cloud)
    assert boundary.stage(plan, service) == service + "-00042-test"
    arguments = calls[0]
    assert "--no-traffic" in arguments
    assert arguments[arguments.index("--image") + 1] == plan.image
    env = arguments[arguments.index("--update-env-vars") + 1]
    assert env.startswith("^|^")
    assert "EMPLOYER_APPLY_CREDITS_PER_JOB=20" in env
    assert "OPTIONAL_AI_GENERATION_ENABLED=false" in env
    assert "WORKER_ALLOWED_TOPICS=employer.search,employer.refresh,employer.apply,employer.artifact-delete" in env
    assert arguments[arguments.index("--update-secrets") + 1] == f"DATABASE_URL={plan.runtime_secret},HIREWIZ_EXPENSE_POLICY_JSON={plan.expense_secret}"
    assert "--no-allow-unauthenticated" in arguments
    assert "WORKER_LABEL=employer-worker" in env


@pytest.mark.parametrize("principal,condition", [
    ("allUsers", None), ("allAuthenticatedUsers", {"expression": "true"}),
])
def test_native_worker_refuses_unresolved_custom_project_role_public_principal(plan, monkeypatch, principal, condition):
    boundary = release.NativeBoundary(ROOT)
    service = release.SERVICES[1]
    environment = {**release.SAFE_ENV, "APP_RELEASE": plan.release, "SERVICE_ROLE": "worker",
                   "WORKER_LABEL": "analysis-worker", "WORKER_ALLOWED_TOPICS": "analysis.run"}
    env = [{"name": name, "value": value} for name, value in environment.items()]
    for name, secret in (("DATABASE_URL", plan.runtime_secret), ("HIREWIZ_EXPENSE_POLICY_JSON", plan.expense_secret)):
        secret_name, key = secret.split(':')
        env.append({"name": name, "valueFrom": {"secretKeyRef": {"name": secret_name, "key": key}}})
    revision = {"metadata": {"labels": {"serving.knative.dev/service": service}},
                "spec": {"serviceAccountName": plan.runtime_service_account, "containers": [{"env": env}]},
                "status": {"imageDigest": plan.image, "conditions": [{"type": "Ready", "status": "True"}]}}
    binding = {"role": f"projects/{release.PROJECT}/roles/customInvoker", "members": [principal]}
    if condition:
        binding["condition"] = condition
    def cloud(*args):
        if args[:3] == ("run", "revisions", "describe"):
            return revision
        if args[:3] == ("run", "services", "describe"):
            return {"metadata": {"annotations": {"run.googleapis.com/invoker-iam-disabled": "false"}}}
        if args[:2] == ("projects", "get-iam-policy"):
            return {"bindings": [binding]}
        pytest.fail("Worker privacy verification must stop at unresolved project public binding")
    monkeypatch.setattr(boundary, "_cloud", cloud)
    with pytest.raises(release.ReleaseDenied, match="project_invoker_policy_unresolved_or_public"):
        boundary.verify_revision(plan, service, service + "-00042-test")


def test_read_only_observation_is_useful_while_external_fence_verifier_is_unavailable(plan, monkeypatch):
    boundary = release.NativeBoundary(ROOT)
    monkeypatch.setattr(release.source_guard, "checked_files", lambda *args: set())
    monkeypatch.setattr(release.source_guard, "migration_chain", lambda *args: release.CHAIN)
    monkeypatch.setattr(boundary, "verify_identity_and_policy", lambda *args: None)
    monkeypatch.setattr(boundary, "verify_admission_closed", lambda: (_ for _ in ()).throw(release.ReleaseDenied("queue_not_paused_and_drained")))
    monkeypatch.setattr(boundary, "verify_database", lambda *args: None)
    monkeypatch.setattr(boundary, "_cloud", lambda *args: pytest.fail("Unexpected cloud mutation"))
    result = release.observe_release(plan, boundary)
    assert result["cloud_mutations"] == 0 and result["cutover_authorized"] is False
    assert result["gates"]["source_ci_image_policy"]["observed_check_passed"]
    assert result["gates"]["queue_scheduler_job_drain"]["reason"] == "queue_not_paused_and_drained"
    assert result["gates"]["external_provider_and_consumer_fence"]["reason"] == "native_external_consumer_and_provider_fence_verifier_missing"


def test_caller_backup_metadata_never_substitutes_for_native_owned_creation(plan, monkeypatch):
    boundary = release.NativeBoundary(ROOT)
    monkeypatch.setattr(boundary, "_cloud", lambda *args: pytest.fail("Caller metadata must not dispatch"))
    with pytest.raises(release.ReleaseDenied, match="native_owned_post_fence_backup_creation_missing"):
        boundary.verify_backup(plan, release.Fence(STAMP, STAMP - timedelta(minutes=5), True, True, True))


@pytest.mark.parametrize("fault", [None, "retention", "changed_bytes", "bucket_missing", "bucket_acl", "bucket_pap"])
def test_native_owned_backup_creation_and_exact_generation_verification(plan, monkeypatch, fault):
    import hashlib
    from types import SimpleNamespace

    from sqlalchemy.engine import URL
    boundary = release.NativeBoundary(ROOT)
    since = datetime.now(UTC) - timedelta(minutes=5)
    fence = release.Fence(datetime.now(UTC), since, True, True, True)
    monkeypatch.setenv("HIREWIZ_CUTOVER_DATABASE_URL", URL.create("postgresql+psycopg", username="synthetic_observer", host="127.0.0.1", database="synthetic").render_as_string(hide_password=False))
    monkeypatch.setattr(boundary, "_database_context", lambda *args: (None, (SimpleNamespace(dispose=lambda: None), SimpleNamespace(expected_system_identifier_sha256="f" * 64, expected_database_oid=7)), {}, ()))
    from contextlib import contextmanager
    @contextmanager
    def snapshot_context(*args):
        yield "00000001-000000AA-1", SimpleNamespace(expected_system_identifier_sha256="f" * 64, expected_database_oid=7)
    monkeypatch.setattr(boundary, "_backup_snapshot", snapshot_context)
    monkeypatch.setattr(boundary, "verify_database", lambda *args: None)
    monkeypatch.setattr(boundary, "verify_fences", lambda *args: release.Fence(datetime.now(UTC), since, True, True, True))
    stored = {}
    calls = []
    def process(arguments, **kwargs):
        calls.append(tuple(arguments))
        assert arguments[0] == "pg_dump"
        assert kwargs["env"]["PGDATABASE"] == "synthetic"
        Path(arguments[-1]).write_bytes(b"PGDMP" + b"synthetic-local-archive" * 20)
    def command(executable, *arguments):
        calls.append((executable, *arguments))
        if executable == "gcloud" and arguments[:2] == ("storage", "cp"):
            if arguments[2].startswith("gs://"):
                Path(arguments[3]).write_bytes(stored["bytes"] + (b"changed" if fault == "changed_bytes" else b""))
            else:
                assert arguments[arguments.index("--if-generation-match") + 1] == "0"
                assert arguments[arguments.index("--retention-mode") + 1] == "Locked"
                stored["bytes"] = Path(arguments[2]).read_bytes()
                stored["created"] = datetime.now(UTC).isoformat()
        return b"synthetic archive listing"
    def cloud(*arguments):
        calls.append(("cloud", *arguments))
        if arguments[:3] == ("storage", "buckets", "describe"):
            return {} if fault == "bucket_missing" else {
                "uniform_bucket_level_access": fault != "bucket_acl",
                "public_access_prevention": "inherited" if fault == "bucket_pap" else "enforced"}
        if arguments[:3] == ("storage", "buckets", "get-iam-policy"):
            return {"bindings": []}
        return {"generation": "91919", "creation_time": stored["created"], "size": len(stored["bytes"]),
                "retention": {"mode": "Unlocked" if fault == "retention" else "Locked",
                              "retainUntilTime": (datetime.now(UTC) + timedelta(days=7)).isoformat()}}
    monkeypatch.setattr(release.subprocess, "run", process)
    monkeypatch.setattr(boundary, "_command", command)
    monkeypatch.setattr(boundary, "_cloud", cloud)
    if fault in {"bucket_missing", "bucket_acl", "bucket_pap"}:
        with pytest.raises(release.ReleaseDenied, match="protected_backup_bucket_privacy_controls_not_verified"):
            boundary.create_backup(plan, fence)
        assert not any(call[0] == "pg_dump" or call[:3] == ("gcloud", "storage", "cp") for call in calls)
        assert boundary._owned_backup is None
        return
    boundary.create_backup(plan, fence)
    assert boundary._owned_backup["sha256"] == hashlib.sha256(stored["bytes"]).hexdigest()
    if fault:
        with pytest.raises(release.ReleaseDenied):
            boundary.verify_backup(plan, fence)
    else:
        boundary.verify_backup(plan, fence)
        assert any(call[:3] == ("gcloud", "storage", "cp") and call[3] == plan.backup_uri + "#91919" for call in calls)


@pytest.mark.parametrize("service", release.SERVICES)
def test_native_health_uses_real_worker_contract_and_tagged_revision_identity(plan, monkeypatch, service):
    import io
    import urllib.request
    revision = service + "-00042-test"
    boundary = release.NativeBoundary(ROOT)
    monkeypatch.setattr(boundary, "_cloud", lambda *args: {"status": {"url": "https://synthetic-service.run.app", "traffic": [{"tag": "monetary-candidate", "revisionName": revision, "url": "https://synthetic-verified.run.app"}]}})
    def token_command(*args):
        assert args[args.index("--audiences") + 1] == "https://synthetic-service.run.app"
        return b"synthetic-test-identity"
    monkeypatch.setattr(boundary, "_command", token_command)
    health = {"ok": True, "release": plan.release}
    if service == release.SERVICES[0]:
        health["revision"] = revision
    else:
        health["role"] = "analysis-worker" if service == release.SERVICES[1] else "employer-worker"
    opener = type("SyntheticOpener", (), {"open": lambda self, req, timeout: io.BytesIO(json.dumps(health).encode())})()
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: opener)
    boundary._verify_http_health(plan, service, revision)


def test_reviewed_ci_contract_refuses_default_merge_checkout(tmp_path):
    import yaml
    target = tmp_path / '.github/workflows'
    target.mkdir(parents=True)
    jobs = {job: {"steps": [{"uses": "actions/checkout@v4"}]} for job in release.CI_JOBS}
    (target / 'ci.yml').write_text(yaml.safe_dump({"jobs": jobs}))
    with pytest.raises(release.ReleaseDenied, match="reviewed_ci_checkout_contract_mismatch"):
        release.verify_ci_checkout_contract(tmp_path)


@pytest.mark.parametrize("aliases,overlap,reason", [
    (["HIREWIZ_CUTOVER_OPERATOR_LOCAL", "HIREWIZ_CUTOVER_OPERATOR_LOCAL"], False, "operator_role_alias_invalid"),
    (["DATABASE_URL"], False, "operator_role_alias_invalid"),
    (["HIREWIZ_CUTOVER_OPERATOR_LOCAL"], True, "operator_role_class_overlap"),
])
def test_native_context_retains_original_observer_operator_alias_guards(plan, monkeypatch, aliases, overlap, reason):
    from types import SimpleNamespace
    inventory = SimpleNamespace(candidate_commit=plan.release, candidate_image=plan.image_digest,
                                role_env={key: "HIREWIZ_CUTOVER_ROLE_" + key.upper() for key in ("retired", "replacement_runtime", "replacement_migration")},
                                database_operator_role_env=aliases)
    inventory.model_copy = lambda **kwargs: inventory
    boundary = release.NativeBoundary(ROOT)
    monkeypatch.setattr(boundary, "_module", lambda *args: SimpleNamespace(load_inventory=lambda *args: (inventory, plan.inventory_sha256)))
    for key, variable in inventory.role_env.items():
        monkeypatch.setenv(variable, "synthetic_" + key)
    monkeypatch.setenv("HIREWIZ_CUTOVER_OPERATOR_LOCAL", "synthetic_retired" if overlap else "synthetic_operator")
    monkeypatch.delenv("HIREWIZ_CUTOVER_DATABASE_URL", raising=False)
    with pytest.raises(release.ReleaseDenied, match=reason):
        boundary._database_context(plan, "20261008_0009")


def test_native_stage_overrides_inherited_open_checkout_and_preserves_payment_callbacks(plan, monkeypatch):
    boundary = release.NativeBoundary(ROOT)
    service = release.SERVICES[0]
    inherited = {"RAZORPAY_CHECKOUT_ENABLED": "true", "RAZORPAY_KEY_ID": "rzp_test_synthetic"}
    existing_payment_refs = {"RAZORPAY_WEBHOOK_SECRET": "hirewiz-payment-webhook:7"}
    def cloud(*args):
        assert args[:3] == ("run", "deploy", service)
        assert args[args.index("--no-traffic")] == "--no-traffic"
        updates = args[args.index("--update-env-vars") + 1]
        assert updates.startswith("^|^")
        inherited.update(part.split("=", 1) for part in updates[3:].split("|"))
        removals = args[args.index("--remove-env-vars") + 1].split(",")
        assert not set(removals) & (set(inherited) | set(existing_payment_refs))
        secret_updates = args[args.index("--update-secrets") + 1]
        assert secret_updates == f"DATABASE_URL={plan.runtime_secret},HIREWIZ_EXPENSE_POLICY_JSON={plan.expense_secret}"
        return {"status": {"latestCreatedRevisionName": service + "-00042-test"}}
    monkeypatch.setattr(boundary, "_cloud", cloud)
    assert boundary.stage(plan, service) == service + "-00042-test"
    assert inherited["RAZORPAY_CHECKOUT_ENABLED"] == "false"
    assert inherited["RAZORPAY_KEY_ID"] == "rzp_test_synthetic"
    assert existing_payment_refs == {"RAZORPAY_WEBHOOK_SECRET": "hirewiz-payment-webhook:7"}



@pytest.mark.parametrize("checkout", ["true", None, "false"])
def test_native_revision_requires_explicit_closed_checkout_without_disabling_payment_callbacks(plan, monkeypatch, checkout):
    boundary = release.NativeBoundary(ROOT)
    service = release.SERVICES[0]
    environment = {**release.SAFE_ENV, "APP_RELEASE": plan.release, "SERVICE_ROLE": "api",
                   "RAZORPAY_KEY_ID": "rzp_test_synthetic"}
    if checkout is None:
        environment.pop("RAZORPAY_CHECKOUT_ENABLED", None)
    else:
        environment["RAZORPAY_CHECKOUT_ENABLED"] = checkout
    env = [{"name": name, "value": value} for name, value in environment.items()]
    for name, secret in (("DATABASE_URL", plan.runtime_secret), ("HIREWIZ_EXPENSE_POLICY_JSON", plan.expense_secret),
                         ("RAZORPAY_WEBHOOK_SECRET", "hirewiz-payment-webhook:7")):
        secret_name, key = secret.split(":")
        env.append({"name": name, "valueFrom": {"secretKeyRef": {"name": secret_name, "key": key}}})
    revision = {"metadata": {"labels": {"serving.knative.dev/service": service}},
                "spec": {"serviceAccountName": plan.runtime_service_account, "containers": [{"env": env}]},
                "status": {"imageDigest": plan.image, "conditions": [{"type": "Ready", "status": "True"}]}}
    health = []
    def cloud(*args):
        assert args[:3] == ("run", "revisions", "describe")
        return revision
    monkeypatch.setattr(boundary, "_cloud", cloud)
    monkeypatch.setattr(boundary, "_verify_http_health", lambda *args: health.append(args))
    if checkout != "false":
        with pytest.raises(release.ReleaseDenied, match="joined_revision_identity_or_safety_mismatch"):
            boundary.verify_revision(plan, service, service + "-00042-test")
        assert health == []
    else:
        boundary.verify_revision(plan, service, service + "-00042-test")
        assert len(health) == 1
        assert env[-1] == {"name": "RAZORPAY_WEBHOOK_SECRET", "valueFrom": {"secretKeyRef": {"name": "hirewiz-payment-webhook", "key": "7"}}}



def test_complete_release_reports_checkout_admission_closed(plan):
    result = run(plan, Boundary())
    assert result["checkout_enabled"] is False
    assert result["queue_admission"] == "closed" and result["goal_complete"] is False


@pytest.mark.parametrize("ambient_region", [None, "global", "europe-west1"])
def test_native_build_read_uses_release_region_when_cli_default_differs(plan, monkeypatch, ambient_region):
    import os

    plan, boundary, calls = native_identity(plan, monkeypatch)
    fixture_command = boundary._command
    fixture_cloud = boundary._cloud
    build_reads = []
    if ambient_region is None:
        monkeypatch.delenv("CLOUDSDK_BUILDS_REGION", raising=False)
    else:
        monkeypatch.setenv("CLOUDSDK_BUILDS_REGION", ambient_region)

    def command(executable, *args):
        if executable != "gcloud":
            return fixture_command(executable, *args)
        if args[:2] == ("builds", "describe"):
            # Match the documented native CLI selection: explicit flag, then
            # builds/region, then global. This build exists only in us-central1.
            region = (args[args.index("--region") + 1] if "--region" in args
                      else os.environ.get("CLOUDSDK_BUILDS_REGION", "global"))
            build_reads.append((args[2], region))
            if region != release.REGION:
                raise release.ReleaseDenied("cloud_operation_unavailable_or_outcome_unknown")
        return json.dumps(fixture_cloud(*args)).encode()

    monkeypatch.setattr(boundary, "_command", command)
    monkeypatch.setattr(boundary, "_cloud", release.NativeBoundary._cloud.__get__(boundary))
    boundary.verify_identity_and_policy(plan)
    assert build_reads == [(plan.build_id, release.REGION)]
    assert not any("deploy" in call or "execute" in call for call in calls)
