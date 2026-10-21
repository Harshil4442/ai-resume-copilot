"""Native execution-reference compatibility keeps ownership guards intact."""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
import test_document_refresh_probe as native_helpers
from infra.gcp import document_refresh_probe as p
from test_document_refresh_probe import EX_UID, IMAGE, JOB_UID, NEXT, NONCE, completed


@pytest.fixture(params=["short", "full"])
def native_pair(request):
    job, execution = completed()
    if request.param == "short":
        job["latestCreatedExecution"]["name"] = execution["name"].rsplit("/", 1)[-1]
    return job, execution


@pytest.mark.parametrize("job_reference", ["short", "full"])
def test_current_native_execution_accepts_exact_short_and_full_references(native_pair, job_reference):
    job, execution = native_pair
    if job_reference == "short":
        execution["job"] = job["name"].rsplit("/", 1)[-1]

    p.validate_job(job, IMAGE, NONCE, uid=JOB_UID)
    p.validate_execution(execution, job, IMAGE, NONCE, uid=EX_UID)


@pytest.mark.parametrize(
    "reference",
    ["foreign_project", "foreign_job", "different_execution", "foreign_region", "prefix", "suffix"],
)
def test_latest_reference_cannot_select_another_resource_or_lookalike(reference):
    job, execution = completed()
    full = execution["name"]
    short = full.rsplit("/", 1)[-1]
    if reference == "foreign_project":
        selected = full.replace("projects/" + p.PROJECT, "projects/another-project", 1)
    elif reference == "foreign_job":
        selected = full.replace("/jobs/" + job["name"].rsplit("/", 1)[-1], "/jobs/another-job", 1)
    elif reference == "different_execution":
        selected = job["name"] + "/executions/" + job["name"].rsplit("/", 1)[-1] + "-other"
    elif reference == "foreign_region":
        selected = full.replace("/locations/" + p.REGION, "/locations/europe-west1", 1)
    elif reference == "prefix":
        selected = "prefix-" + short
    else:
        selected = short + "-suffix"
    job["latestCreatedExecution"]["name"] = selected

    with pytest.raises(p.NativeProofDenied, match="document_native_execution_not_singleton"):
        p.validate_execution(execution, job, IMAGE, NONCE, uid=EX_UID)


@pytest.mark.parametrize("foreign", ["project", "job"])
def test_matching_latest_reference_cannot_move_execution_outside_its_owned_job(native_pair, foreign):
    job, execution = native_pair
    latest_was_full = "/" in job["latestCreatedExecution"]["name"]
    if foreign == "project":
        execution["name"] = execution["name"].replace("projects/" + p.PROJECT, "projects/another-project", 1)
    else:
        execution["name"] = execution["name"].replace(
            "/jobs/" + job["name"].rsplit("/", 1)[-1], "/jobs/another-job", 1,
        )
    job["latestCreatedExecution"]["name"] = (
        execution["name"] if latest_was_full else execution["name"].rsplit("/", 1)[-1]
    )

    with pytest.raises(p.NativeProofDenied, match="document_native_execution_not_successful"):
        p.validate_execution(execution, job, IMAGE, NONCE, uid=EX_UID)


@pytest.mark.parametrize("changed", ["multiple_executions", "boolean_count", "uid", "image", "sandbox", "retry"])
def test_reference_compatibility_preserves_singleton_uid_and_template_guards(native_pair, changed):
    job, execution = native_pair
    if changed == "multiple_executions":
        job["executionCount"] = 2
    elif changed == "boolean_count":
        job["executionCount"] = True
    elif changed == "uid":
        execution["uid"] = JOB_UID
    elif changed == "image":
        execution["template"]["containers"][0]["image"] = NEXT
    elif changed == "sandbox":
        execution["template"]["containers"][0]["sandboxLauncher"] = False
    else:
        execution["template"]["maxRetries"] = 1

    with pytest.raises(p.NativeProofDenied):
        p.validate_execution(execution, job, IMAGE, NONCE, uid=EX_UID)


@pytest.fixture(params=[False, True], ids=["maintenance", "release_read_only"])
def current_successor(monkeypatch, request):
    fake = native_helpers.Fake()
    fake.created = fake.ran = True
    fake.j, fake.ex = completed()
    serving = native_helpers.service()
    fake.service = lambda _: copy.deepcopy(serving)
    fake.revision = lambda _: native_helpers.revision(serving)
    observed = {"build_routes": [], "client_options": [], "execution_gets": [], "receipt_reads": []}
    original_execution, original_entries = fake.execution, fake.entries

    def execution_get(name):
        observed["execution_gets"].append(name)
        return original_execution(name)

    def entries(query, token):
        observed["receipt_reads"].append(query)
        return original_entries(query, token)

    def clients(**options):
        observed["client_options"].append(options)
        return fake

    def verify_build(route, *arguments):
        assert arguments == (native_helpers.BUILD_ID, NONCE, IMAGE)
        observed["build_routes"].append(route)
        return native_helpers.build()

    fake.execution, fake.entries = execution_get, entries
    monkeypatch.setattr(p, "Clients", clients)
    monkeypatch.setattr(p, "build_module", lambda: SimpleNamespace(
        verify_current_build=lambda *args: verify_build("maintenance", *args),
        verify_current_build_release=lambda *args: verify_build("release", *args),
    ))
    verify = p.verify_current_successor_for_release if request.param else p.verify_current_successor
    return SimpleNamespace(fake=fake, serving=serving, observed=observed, verify=verify,
                           read_only=request.param)


@pytest.mark.parametrize("reference_form", ["short", "full"])
def test_current_successor_gets_full_resource_for_both_native_reference_forms(current_successor, reference_form):
    state = current_successor
    full = state.fake.ex["name"]
    state.fake.j["latestCreatedExecution"]["name"] = full.rsplit("/", 1)[-1] if reference_form == "short" else full

    result = state.verify(IMAGE, state.serving)

    assert state.observed["execution_gets"] == [full]
    assert state.observed["client_options"] == [{"read_only_release": True} if state.read_only else {}]
    assert state.observed["build_routes"] == ["release" if state.read_only else "maintenance"]
    assert state.fake.calls["preflight"] == state.fake.calls["close"] == 1
    assert len(state.observed["receipt_reads"]) == 1
    assert result.image == IMAGE and result.job_uid == JOB_UID and result.execution_uid == EX_UID
    assert state.fake.calls["create"] == state.fake.calls["start"] == state.fake.calls["delete"] == 0


@pytest.mark.parametrize("reference", ["foreign_project", "foreign_job", "foreign_execution", "prefix", "suffix"])
def test_current_successor_rejects_foreign_or_lookalike_reference_before_execution_get(current_successor, reference):
    state = current_successor
    full = state.fake.ex["name"]
    short = full.rsplit("/", 1)[-1]
    if reference == "foreign_project":
        selected = full.replace("projects/" + p.PROJECT, "projects/another-project", 1)
    elif reference == "foreign_job":
        selected = full.replace("/jobs/" + state.fake.j["name"].rsplit("/", 1)[-1], "/jobs/another-job", 1)
    elif reference == "foreign_execution":
        selected = state.fake.j["name"] + "/executions/another-job-abcde"
    elif reference == "prefix":
        selected = "prefix-" + short
    else:
        selected = short + "-suffix"
    state.fake.j["latestCreatedExecution"]["name"] = selected

    with pytest.raises(p.NativeProofDenied, match="document_native_current_execution_reference_invalid"):
        state.verify(IMAGE, state.serving)

    assert state.observed["execution_gets"] == state.observed["receipt_reads"] == []
    assert state.fake.calls["close"] == 1


@pytest.mark.parametrize("changed", ["worker_identity", "job_template", "receipt"])
def test_current_successor_reference_normalization_retains_identity_and_acceptance_checks(current_successor, changed):
    state = current_successor
    state.fake.j["latestCreatedExecution"]["name"] = state.fake.ex["name"].rsplit("/", 1)[-1]
    if changed == "worker_identity":
        state.serving["uid"] = JOB_UID
    elif changed == "job_template":
        state.fake.j["template"]["template"]["maxRetries"] = 1
    else:
        receipt = native_helpers.payload()
        receipt["cases"][4]["layout"]["unchanged_links"] = 0
        state.fake.pages = {None: {"entries": [native_helpers.event(state.fake.j, state.fake.ex, receipt)]}}

    with pytest.raises(p.NativeProofDenied):
        state.verify(IMAGE, state.serving)

    assert state.observed["execution_gets"] == ([state.fake.ex["name"]] if changed == "receipt" else [])
    assert len(state.observed["receipt_reads"]) == (1 if changed == "receipt" else 0)
    assert state.fake.calls["create"] == state.fake.calls["start"] == state.fake.calls["delete"] == 0
    assert state.fake.calls["close"] == 1
