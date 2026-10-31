"""Synthetic metadata only; no genuine cloud receipt, processing or authority."""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from infra.gcp import native_quality_acquisition_isolation as isolation

D = isolation.documents
T = isolation.tex
IMAGE = f"{isolation.REGION}-docker.pkg.dev/{isolation.PROJECT}/cloud-run-source-deploy/{D.SERVICE}@sha256:" + "a" * 64
TEX_IMAGE = T.REPOSITORY + "@sha256:" + "b" * 64
RELEASE = "c" * 40


def plan(**changes):
    now = datetime.now(UTC)
    body = dict(purpose="unreviewed_development_capture_no_model_no_retail", evaluator=isolation.EVALUATOR,
        evaluator_uid="109100000000000000001", bucket=isolation.BUCKET, bucket_metageneration="1",
        experiment_id="isolated-dev-synthetic", valid_from=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1), controller_sha256="d" * 64,
        job_name="hirewiz-quality-dev-synthetic1", runner_release=isolation.RUNNER_RELEASE,
        runner_image=isolation.RUNNER_REPOSITORY + "@sha256:" + "e" * 64,
        runner_build_id="11111111-1111-1111-1111-111111111111", runner_ci_run_id=1,
        inspector_service_uid="synthetic-service", inspector_revision_uid="synthetic-revision",
        compiler_service_uid="synthetic-service", compiler_revision_uid="synthetic-revision",
        inspector_image=IMAGE, compiler_image=TEX_IMAGE, inspector_release=RELEASE, compiler_release=RELEASE,
        funding_evidence_sha256="f" * 64, acquisition_authorization_sha256="1" * 64,
        native_dispatch_review_sha256="2" * 64, bundle_name="input.synthetic.json",
        bundle_generation="1", bundle_sha256="3" * 64, entry_sha256="4" * 64)
    return isolation.Plan(**(body | changes))


def worker(module, expected):
    service = copy.deepcopy(expected["service"])
    rev = module.RESOURCE + "/revisions/" + module.SERVICE + "-synthetic"
    service.update(uri=module.ACCEPTED_ORIGIN, uid="synthetic-service", generation="1", observedGeneration="1", reconciling=False,
        latestReadyRevision=rev, terminalCondition={"state": "CONDITION_SUCCEEDED"},
        trafficStatuses=[{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST", "revision": rev, "percent": 100}])
    revision = copy.deepcopy(service["template"])
    revision.update(name=rev, service=module.RESOURCE, uid="synthetic-revision",
                    conditions=[{"type": "Ready", "state": "CONDITION_SUCCEEDED"}])
    return {"service": service, "revision": revision,
        "service_iam": {"version": 3, "bindings": [{"role": "roles/run.invoker", "members": sorted(
            "serviceAccount:" + name for name in (D.API_INVOKER, D.UPLOAD_COORDINATOR, isolation.EVALUATOR))}]},
        "project_iam": {"version": 3, "bindings": []},
        "service_account": {"email": module.SERVICE_ACCOUNT, "disabled": False}, "service_account_keys": {"keys": []},
        "upload_coordinator_service_account": {"name": f"projects/{D.PROJECT}/serviceAccounts/{D.UPLOAD_COORDINATOR}",
            "projectId": D.PROJECT, "email": D.UPLOAD_COORDINATOR, "uniqueId": D.UPLOAD_COORDINATOR_UID, "disabled": False},
        "upload_coordinator_service_account_keys": {"keys": []}}


def snapshot(p):
    return {"evaluator_service_account": {"name": f"projects/{D.PROJECT}/serviceAccounts/{isolation.EVALUATOR}",
        "projectId": D.PROJECT, "email": isolation.EVALUATOR, "uniqueId": p.evaluator_uid, "disabled": False},
        "evaluator_service_account_keys": {"keys": []}, "project_iam": {"version": 3, "bindings": []},
        "bucket": {"name": isolation.BUCKET, "projectNumber": isolation.PROJECT_NUMBER, "location": "US-CENTRAL1", "storageClass": "STANDARD",
            "metageneration": "1", "iamConfiguration": {"uniformBucketLevelAccess": {"enabled": True},
                "publicAccessPrevention": "enforced"}},
        "object_role": {"name": isolation.ROLE, "stage": "GA",
                        "includedPermissions": ["storage.objects.get", "storage.objects.create"]},
        "bucket_iam": {"version": 3, "bindings": [{"role": isolation.ROLE,
            "members": ["serviceAccount:" + isolation.EVALUATOR], "condition": p.condition}]},
        "inspector": worker(D, D.render(IMAGE, RELEASE, source_revision=True)),
        "compiler": worker(T, T.render(TEX_IMAGE, RELEASE, coordinator_phase=True))}


def test_full_actual_shape_is_checked_without_claiming_native_or_processing_authority():
    p = plan()
    s = snapshot(p)
    before = copy.deepcopy(s)
    out = isolation.check(p, s, controller_sha256=p.controller_sha256)
    assert s == before and out["configuration_matches"]
    assert out["input_authority"] == "untrusted_offline_snapshot"
    assert not out["native_approval"] and not out["processing_authorized"]
    assert not out["effective_iam_proven"] and out["provider_calls"] == out["model_calls"] == 0


@pytest.mark.parametrize("change", [
    lambda s: s["evaluator_service_account"].update(email=D.UPLOAD_COORDINATOR),
    lambda s: s["evaluator_service_account"].update(uniqueId="109100000000000000002"),
    lambda s: s["evaluator_service_account"].update(disabled=True),
    lambda s: s["evaluator_service_account"].update(disabled=0),
    lambda s: s["evaluator_service_account_keys"].update(keys=[{"keyType": "USER_MANAGED"}]),
    lambda s: s["evaluator_service_account_keys"].update(keys=[{}]),
    lambda s: s["project_iam"]["bindings"].append({"role": "roles/editor", "members": ["serviceAccount:" + isolation.EVALUATOR]}),
    lambda s: s["bucket"].update(location="US"),
    lambda s: s["bucket"].update(projectNumber="111111111111"),
    lambda s: s["compiler"]["service"].update(uid="other-nonempty-UID"),
    lambda s: s["bucket"].update(metageneration="2"),
    lambda s: s["bucket"]["iamConfiguration"]["uniformBucketLevelAccess"].update(enabled=False),
    lambda s: s["bucket"]["iamConfiguration"].update(publicAccessPrevention="inherited"),
    lambda s: s["object_role"]["includedPermissions"].append("storage.objects.delete"),
    lambda s: s["bucket_iam"]["bindings"][0]["condition"].update(expression="true"),
    lambda s: s["bucket_iam"]["bindings"][0]["condition"].update(expression='resource.name.startsWith("projects/_/buckets/other/")'),
    lambda s: s["inspector"]["service_iam"]["bindings"][0]["members"].remove("serviceAccount:" + isolation.EVALUATOR),
    lambda s: s["compiler"]["service_iam"]["bindings"][0]["members"].append("serviceAccount:extra@example.test"),
    lambda s: s["inspector"]["revision"]["containers"][0].update(sandboxLauncher=False),
    lambda s: s["compiler"]["service"]["template"].update(timeout="120s"),
    lambda s: s["inspector"]["service"].update(ingress="INGRESS_TRAFFIC_ALL"),
    lambda s: s["compiler"]["service"].update(uri="https://foreign.run.app"),
    lambda s: s["inspector"]["revision"].update(uid=""),
    lambda s: s["compiler"]["revision"]["containers"][0]["env"][1].update(value="0" * 64),
])
def test_wrong_identity_extra_authority_and_original_worker_guards_refuse(change):
    p = plan()
    s = snapshot(p)
    change(s)
    with pytest.raises(isolation.IsolationDenied):
        isolation.check(p, s, controller_sha256=p.controller_sha256)


def test_old_exact_two_semantics_are_unchanged_and_never_accept_new_member():
    p = plan()
    s = snapshot(p)
    kwargs = dict(inspector_image=IMAGE, inspector_release=RELEASE, compiler_image=TEX_IMAGE, compiler_release=RELEASE)
    with pytest.raises(ValueError):
        isolation.check_historical_workers(s["inspector"], s["compiler"], **kwargs)
    for name in ("inspector", "compiler"):
        s[name]["service_iam"]["bindings"][0]["members"].remove("serviceAccount:" + isolation.EVALUATOR)
    out = isolation.check_historical_workers(s["inspector"], s["compiler"], **kwargs)
    assert out["inspector"]["configuration_matches"] and out["compiler"]["configuration_matches"]
    with pytest.raises(isolation.IsolationDenied):
        isolation.check(p, s, controller_sha256=p.controller_sha256)


def job(p, batch=0):
    return {"name": f"projects/{D.PROJECT}/locations/us-central1/jobs/{p.job_name}", "uid": "synthetic-job",
        "generation": "1", "observedGeneration": "1", "reconciling": False,
        "terminalCondition": {"state": "CONDITION_SUCCEEDED"}, "template": isolation.expected_job(p, batch=batch)}


def test_one_task_zero_retry_fixed_entry_and_exact_bundle_job_is_not_dispatch():
    p = plan()
    isolation.check_job(p, job(p), batch=0)
    assert isolation.expected_job(p, batch=0)["template"]["timeout"] == "240s"
    assert isolation.expected_job(p, batch=8)["template"]["timeout"] == "1200s"


@pytest.mark.parametrize("change", [
    lambda j: j["template"].update(taskCount=2),
    lambda j: j["template"]["template"].update(serviceAccount=D.UPLOAD_COORDINATOR),
    lambda j: j["template"]["template"].update(maxRetries=1),
    lambda j: j["template"]["template"].update(timeout="3600s"),
    lambda j: j["template"]["template"].update(volumes=[{"secret": {"secret": "not-read"}}]),
    lambda j: j["template"]["template"]["containers"][0].update(command=["sh"]),
    lambda j: j["template"]["template"]["containers"][0].update(env=[{"name": "DATABASE_URL", "value": "synthetic-never-a-secret"}]),
    lambda j: j["template"]["template"]["containers"][0]["args"].__setitem__(5, "gs://other/quality-development/other/"),
    lambda j: j.update(name=j["name"] + "other"),
])
def test_job_cross_identity_prefix_secret_mount_and_replay_configuration_refuse(change):
    p = plan()
    value = job(p)
    change(value)
    with pytest.raises(isolation.IsolationDenied):
        isolation.check_job(p, value, batch=0)


def test_stale_controller_and_expired_plan_refuse_before_snapshot():
    p = plan()
    with pytest.raises(isolation.IsolationDenied, match="controller_source"):
        isolation.check(p, {}, controller_sha256="0" * 64)
    now = datetime.now(UTC)
    p = plan(valid_from=now - timedelta(hours=2), expires_at=now - timedelta(hours=1))
    with pytest.raises(isolation.IsolationDenied, match="expired_plan"):
        isolation.check(p, {}, controller_sha256=p.controller_sha256)


def test_bad_future_bundle_bytes_cannot_become_source_or_dispatch_proof():
    p = plan()
    with pytest.raises(isolation.IsolationDenied, match="bundle_bytes"):
        isolation.check_future_bundle(p, b"{}", batch=0)
    raw = b'{"job_name":"one","job_name":"two"}'
    p = plan(bundle_sha256=hashlib.sha256(raw).hexdigest())
    with pytest.raises(isolation.IsolationDenied, match="duplicate_bundle_key"):
        isolation.check_future_bundle(p, raw, batch=0)


def bundle_metadata(p):
    from infra.gcp import document_source_candidate as source

    from app.domains.analysis import supported_evaluation_plan as inputs
    from scripts.native_acquisition_job import Bundle

    def ref(name):
        return {"name": name, "generation": "1", "size": 1, "sha256": "5" * 64}
    auth = {"schema_version": "development-native-service-acquisition-v1",
        "purpose": "unreviewed_native_input_capture_no_model_no_retail", "state": "authorized",
        "authorized_by": "synthetic-not-native", "experiment_id": p.experiment_id,
        "journal_path": isolation.WORKSPACE + "/journal", "split": "development",
        "valid_from": p.valid_from, "expires_at": p.expires_at,
        "packet_manifest_sha256": inputs.MANIFEST_SHA, "native_manifest_sha256": "6" * 64,
        "funding_evidence": {"path": "funding.json", "sha256": p.funding_evidence_sha256},
        "request_limit": 80, "reserved_minor_per_request": 200, "maximum_native_reserved_minor": 16000,
        "currency": "INR", "inspector": {"url": D.ACCEPTED_ORIGIN + "/inspect", "image": IMAGE, "policy": source.POLICY_SHA256},
        "compiler": {"url": T.ACCEPTED_ORIGIN + "/compile", "image": TEX_IMAGE, "policy": T.POLICY_SHA256}}
    value = {"schema_version": "development-native-job-input-v1", "purpose": p.purpose,
        "entry_sha256": p.entry_sha256, "job_name": p.job_name,
        "configuration": {"schema_version": "development-native-object-channel-v1", "namespace": p.namespace,
            "acquisition_plan_sha256": "7" * 64, "channel_source_sha256": "8" * 64,
            "maximum_output_bytes": 512 * 1024 * 1024, "maximum_objects": 1024},
        "authorization": auth,
        "dispatch": {"schema_version": "reviewed-native-acquisition-dispatch-v1",
            "purpose": "unreviewed_service_capture_external_authority_required", "reviewed_by": "synthetic-not-native",
            "external_authority_review": {"path": "native-review.json", "sha256": p.native_dispatch_review_sha256},
            "channel_binding": ref("channel-binding.json"), "acknowledgment": ref("dispatch.synthetic.json"),
            "before_checkpoint_sha256": "9" * 64, "parent_terminal": None, "maximum_requests": 1},
        "previous_dispatch": None, "runner_funding_evidence": {"path": "runner-funding.json", "sha256": "a" * 64},
        "native_files": [{"path": "materialization-manifest.json" if n == 0 else f"source-{n}.pdf", "object": ref(f"input.native-{n}")} for n in range(41)],
        "authorization_files": [{"path": "funding.json", "object": ref("input.funding")}]}
    return Bundle.model_validate(value).model_dump(mode="json")


def bind_bundle(p, body):
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return p.model_copy(update={"bundle_sha256": hashlib.sha256(raw).hexdigest(),
        "acquisition_authorization_sha256": isolation.digest(body["authorization"])}), raw


def test_future_bundle_source_count_prefix_auth_and_external_review_are_exact_but_not_native_proof():
    p = plan()
    body = bundle_metadata(p)
    p, raw = bind_bundle(p, body)
    isolation.check_future_bundle(p, raw, batch=0)
    assert body["dispatch"]["purpose"] == "unreviewed_service_capture_external_authority_required"


@pytest.mark.parametrize("change", [
    lambda b: b["configuration"].update(namespace="gs://other-test-bucket/quality-development/isolated-dev-synthetic/"),
    lambda b: b["authorization"].update(experiment_id="another-synthetic"),
    lambda b: b["authorization"].update(request_limit=79),
    lambda b: b["authorization"].update(state="draft"),
    lambda b: b["authorization"]["inspector"].update(image=IMAGE[:-1] + "b"),
    lambda b: b["authorization"]["funding_evidence"].update(sha256="0" * 64),
    lambda b: b["dispatch"]["external_authority_review"].update(sha256="0" * 64),
    lambda b: b["dispatch"].update(maximum_requests=10),
    lambda b: b.update(job_name="hirewiz-quality-dev-other-job"),
    lambda b: b["native_files"].pop(),
    lambda b: b["native_files"][0]["object"].update(name="../outside-prefix"),
])
def test_rehashed_future_bundle_cannot_cross_prefix_source_job_or_dispatch(change):
    p = plan()
    body = bundle_metadata(p)
    change(body)
    p, raw = bind_bundle(p, body)
    with pytest.raises(ValueError):
        isolation.check_future_bundle(p, raw, batch=0)


def test_actual_immutable_GCS_port_has_zero_generation_writes_and_no_mutable_head():
    """Use the real port, with only the owned SDK object replaced by a fake."""
    from scripts import native_acquisition_channel as channel
    calls = []
    class Blob:
        generation = "7"
        size = 1
        def upload_from_string(self, raw, **kwargs):
            calls.append((raw, kwargs))
    class Bucket:
        def blob(self, name):
            assert name == "quality-development/isolated-dev-synthetic/immutable.intent.json"
            return Blob()
    class Client:
        def bucket(self, name):
            assert name == isolation.BUCKET
            return Bucket()
    store = channel.GcsObjects(Client(), plan().namespace)
    reference = store.create("immutable.intent.json", b"x")
    assert reference.generation == "7" and len(calls) == 1
    assert calls[0][1]["if_generation_match"] == 0 and calls[0][1]["retry"] is None


def test_plain_or_model_constructed_wrong_identity_plan_cannot_pass_source_boundary():
    p = plan()
    with pytest.raises(isolation.IsolationDenied, match="operator_plan_type"):
        isolation.check(p.model_dump(), {}, controller_sha256=p.controller_sha256)
    forged = isolation.Plan.model_construct(**(p.model_dump() | {"evaluator": D.UPLOAD_COORDINATOR}))
    with pytest.raises(ValueError):
        isolation.check(forged, {}, controller_sha256=p.controller_sha256)
