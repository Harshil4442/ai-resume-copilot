from __future__ import annotations

import json
import re
from datetime import timedelta
from types import SimpleNamespace

import pytest
from infra.gcp import document_v5_inventory_history as history_filter
from infra.gcp import document_v5_inventory_resume as resume


class NativeProofDenied(RuntimeError):
    pass


class HttpError(RuntimeError):
    def __init__(self, status_code):
        self.status_code = status_code


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    namespace = "/locations/us-central1/jobs/" + resume.CORPUS_JOB
    image = "registry/image@" + resume.IMAGE_DIGEST
    identity = {"immutable_image": image}
    wanted = {"name": namespace}
    plan = {"image": image, "mode": "corpus", "job_nonce": resume.NONCE}
    job_uid = "b67c278a-c8a9-4dcf-a257-fe49fa595e3f"
    execution_uid = "28ba25fe-5d6c-469a-8723-0e2c587a7f06"
    operation = {"name": "operations/create"}
    execution = {"name": namespace + "/executions/corpus", "uid": execution_uid}
    calls = []
    state = {"job": None, "fail_create": False, "fail_run": False, "log_misses": 1}

    def check_intent(name):
        assert json.loads((tmp_path / f"corpus.{name}.started.json").read_text())["automatic_replay"] is False

    def read_job(name):
        assert name == namespace
        if state["job"] is None:
            raise HttpError(404)
        return state["job"]

    def create(body):
        assert body == wanted
        check_intent("create")
        calls.append("corpus-create")
        state["job"] = {"name": namespace, "uid": job_uid, "etag": "etag", "executionCount": 0}
        if state["fail_create"]:
            raise TimeoutError("unknown create")
        return operation

    def start(job):
        assert job is state["job"] and job["executionCount"] == 0
        check_intent("run")
        calls.append("corpus-run")
        job["executionCount"] = 1
        if state["fail_run"]:
            raise TimeoutError("unknown execution")
        return {"name": "operations/run"}

    def read_operation(clients, operation, kind, deadline):
        assert deadline > 0
        return state["job"] if kind == "Job" else execution

    def observe(clients, mode, observed):
        assert mode == "corpus" and observed == identity
        calls.append("corpus-log-read")
        if state["log_misses"] > 0:
            state["log_misses"] -= 1
            raise NativeProofDenied("document_candidate_event_missing")
        return state["job"], execution, {}

    def verify(image_value, observed_identity, journal):
        assert (image_value, observed_identity, journal) == (image, identity, tmp_path)
        calls.append("verify-stock-acceptance")
        return SimpleNamespace(job_uid=job_uid, execution_uid=execution_uid)

    def validate_job(job, observed_plan):
        assert job is state["job"] and observed_plan == plan

    def write_new(path, value):
        with path.open("x") as output:
            json.dump(value, output)

    real_wait = resume.wait_for_corpus_log
    monkeypatch.setattr(resume, "wait_for_corpus_log",
                        lambda action, prior: real_wait(action, prior, sleep=lambda delay: None))
    clients = SimpleNamespace(job=read_job, create=create, start=start, preflight=lambda: None,
                              errors=SimpleNamespace(HttpError=HttpError))
    prior = SimpleNamespace(NativeProofDenied=NativeProofDenied, wait_operation=read_operation,
                            integer=lambda value, expected: type(value) is int and value == expected,
                            UUID=re.compile(r"[a-f0-9-]{36}"))
    probe = SimpleNamespace(handles=lambda build: {"corpus": {"nonce": resume.NONCE}},
                            validate_job=validate_job, _observation=observe, verify_successor=verify)
    monkeypatch.setattr(resume, "require_fresh", lambda now: None)

    def run():
        return resume.run_corpus(clients, prior, SimpleNamespace(write_new=write_new), probe, identity, wanted, tmp_path)

    return run, calls, state


def test_reuses_inventory_and_creates_runs_and_verifies_only_the_missing_corpus(corpus):
    run, calls, _ = corpus
    assert run().execution_uid
    assert calls == ["corpus-create", "corpus-run", "corpus-log-read", "corpus-log-read", "verify-stock-acceptance"]
    with pytest.raises(resume.ResumeDenied, match="corpus_exists_reconcile_only"):
        run()
    assert calls.count("corpus-create") == calls.count("corpus-run") == 1


@pytest.mark.parametrize("phase", ["create", "run"])
def test_lost_acknowledgment_leaves_the_intent_and_cannot_replay(corpus, tmp_path, phase):
    run, calls, state = corpus
    state[f"fail_{phase}"] = True
    with pytest.raises(TimeoutError):
        run()
    assert (tmp_path / f"corpus.{phase}.started.json").is_file()
    assert not (tmp_path / f"corpus.{phase}.acknowledged.json").exists()
    before = calls[:]
    with pytest.raises(resume.ResumeDenied, match="corpus_exists_reconcile_only"):
        run()
    assert calls == before


def test_log_retry_refuses_scope_errors_without_a_second_read():
    calls = []

    def invoke():
        calls.append("read")
        raise NativeProofDenied("document_candidate_log_scope_or_uniqueness")

    with pytest.raises(NativeProofDenied, match="log_scope_or_uniqueness"):
        resume.wait_for_corpus_log(invoke, SimpleNamespace(NativeProofDenied=NativeProofDenied))
    assert calls == ["read"]


def test_inventory_image_must_have_unexpired_definitions_before_new_work():
    resume.require_fresh(resume.DEFINITIONS_DEADLINE - timedelta(microseconds=1))
    with pytest.raises(resume.ResumeDenied, match="definitions_outside_admission_window"):
        resume.require_fresh(resume.DEFINITIONS_DEADLINE)


@pytest.mark.parametrize("proved", [True, False])
def test_only_the_native_verified_continuation_consumes_original_history(tmp_path, monkeypatch, proved):
    history = tmp_path / "history"
    history.mkdir(mode=0o700)
    partial = history / str(resume.ORIGINAL_ARTIFACT)
    completed = history / "14450000000"
    predecessor = history / "14410380686"
    older = history / "14300000000"
    for directory in (partial, completed, predecessor, older):
        directory.mkdir(mode=0o700)
    rows = tmp_path / "rows.tsv"
    rows.write_text(
        "44350000000\t14450000000\tdocument-refresh-resume-v5-inventory-44350000000-1\tmain\n"
        f"{resume.ORIGINAL_RUN}\t{resume.ORIGINAL_ARTIFACT}\t{resume.ORIGINAL_NAME}\tmain\n"
        "44263912800\t14410380686\tdocument-refresh-refresh-44263912800-1\tmain\n"
        "44200000000\t14300000000\tdocument-refresh-refresh-44200000000-1\tmain\n"
    )
    rows.chmod(0o600)
    payload = {"result.json": b"retained"}
    original = {"native-proof/binding.json": b"owned"}
    calls = []

    def verify_consumption(path, original_files, complete_files, manifest):
        assert path == completed and original_files == original
        assert complete_files == payload and manifest == {"verified": "scoped"}
        assert partial.is_dir()
        calls.append("native-read")
        if not proved:
            raise RuntimeError("unverified continuation")

    monkeypatch.setattr(history_filter, "_completed_archive", lambda path, row: (payload, {}))
    monkeypatch.setattr(history_filter, "_completed_manifest", lambda files, run: {"verified": "scoped"})
    monkeypatch.setattr(history_filter, "_verify_consumption", verify_consumption)
    monkeypatch.setattr(resume, "verify_original_metadata", lambda: None)
    monkeypatch.setattr(resume, "read_tree", lambda path: original)
    monkeypatch.setattr(history_filter.custody, "read_tree", lambda path: payload)
    preserved = tmp_path / ("document-v5-inventory-original-" + str(resume.ORIGINAL_ARTIFACT))
    if proved:
        history_filter.normalize(history, rows)
        assert not partial.exists() and preserved.is_dir()
    else:
        with pytest.raises(RuntimeError, match="unverified continuation"):
            history_filter.normalize(history, rows)
        assert partial.is_dir() and not preserved.exists()
    assert calls == ["native-read"]
    assert all(path.is_dir() for path in (completed, predecessor, older))
