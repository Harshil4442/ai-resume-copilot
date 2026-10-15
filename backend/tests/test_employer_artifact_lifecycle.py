from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
from backend.app import models as core
from backend.app.domains.common import utcnow
from backend.app.domains.dispatch import service as dispatch
from backend.app.domains.dispatch.models import DispatchOutbox
from backend.app.domains.employer import artifacts, models, privacy, tasks
from backend.app.routers import worker
from backend.tests import test_employer_services as service_tests
from google.api_core.exceptions import NotFound
from google.cloud import storage


@pytest.fixture
def context(monkeypatch):
    return service_tests.context.__wrapped__(monkeypatch)


def _create(client):
    return client.post("/api/v1/employer-jobs/applications", json={
        "posting_id": "job_1", "resume_id": 10, "resume_choice": "original",
        "idempotency_key": "artifact-lifecycle-create",
    })


def _fake_storage(monkeypatch, *, missing=False):
    calls = []

    class Blob:
        generation = 88

        def reload(self, **kwargs):
            calls.append(("reload", kwargs))
            if missing:
                raise NotFound("fake missing object")

        def delete(self, **kwargs):
            calls.append(("delete", kwargs))

    bucket = SimpleNamespace(blob=lambda *_args, **_kwargs: Blob())
    monkeypatch.setattr(storage, "Client", lambda: SimpleNamespace(bucket=lambda *_args: bucket))
    return calls


def test_rejected_attachment_keeps_the_preupload_guard_and_purge(context, monkeypatch):
    factory, client, _ = context
    monkeypatch.setenv("EMPLOYER_ARTIFACT_BUCKET", "private-test")

    def upload(sealed, *, user_id, object_name):
        with factory() as db:
            guard = db.query(models.EmployerArtifactUpload).one()
            assert guard.gcs_object == "gs://private-test/" + object_name
            assert guard.user_id == user_id
            db.get(models.EmployerPosting, "job_1").is_open = False
            db.commit()
        return replace(sealed, gcs_object="gs://private-test/" + object_name, gcs_generation="42")

    monkeypatch.setattr(artifacts, "store", upload)
    assert _create(client).status_code == 409
    with factory() as db:
        assert db.query(models.SealedApplicationArtifact).count() == 0
        assert db.query(models.EmployerArtifactUpload).count() == 0
        deletion = db.query(models.EmployerArtifactDeletion).one()
        assert deletion.gcs_generation == "42" and deletion.completed_at is None
        assert db.query(DispatchOutbox).filter_by(aggregate_id=deletion.id).one().status == "pending"


def test_successful_attachment_consumes_upload_guard_in_artifact_transaction(context, monkeypatch):
    factory, client, _ = context
    monkeypatch.setenv("EMPLOYER_ARTIFACT_BUCKET", "private-test")
    monkeypatch.setattr(artifacts, "store", lambda sealed, *, user_id, object_name:
                        replace(sealed, gcs_object="gs://private-test/" + object_name, gcs_generation="42"))
    result = _create(client)
    assert result.status_code == 201
    with factory() as db:
        artifact = db.query(models.SealedApplicationArtifact).one()
        assert artifact.gcs_generation == "42" and "/upload_" in artifact.gcs_object
        assert db.query(models.EmployerArtifactUpload).count() == 0
        assert db.query(models.EmployerArtifactDeletion).count() == 0


def test_execution_overtaking_package_edit_cleans_only_unattached_upload(context, monkeypatch):
    factory, client, _ = context
    application = _create(client).json()
    monkeypatch.setenv("EMPLOYER_ARTIFACT_BUCKET", "private-test")

    def upload(sealed, *, user_id, object_name):
        with factory() as db:
            db.get(models.EmployerApplication, application["id"]).status = "queued"
            db.commit()
        return replace(sealed, gcs_object="gs://private-test/" + object_name, gcs_generation="42")

    monkeypatch.setattr(artifacts, "store", upload)
    response = client.put(f"/api/v1/employer-jobs/applications/{application['id']}/package", json={
        "resume_id": 10, "resume_choice": "original", "answers": service_tests.ANSWERS, "consents": {},
    })
    assert response.status_code == 409
    with factory() as db:
        assert db.query(models.SealedApplicationArtifact).count() == 1
        assert db.query(models.SealedApplicationArtifact).one().gcs_object is None
        assert db.query(models.EmployerArtifactDeletion).one().gcs_generation == "42"


def test_deleted_owner_and_late_upload_rearm_after_a_missing_object_check(context, monkeypatch):
    factory, client, _ = context
    monkeypatch.setenv("EMPLOYER_ARTIFACT_BUCKET", "private-test")
    calls = _fake_storage(monkeypatch, missing=True)

    def upload(sealed, *, user_id, object_name):
        with factory() as db:
            privacy.delete_account_data(db, user_id)
            guard = db.query(models.EmployerArtifactUpload).one()
            assert guard.user_id is None
            guard.lease_until = utcnow() - timedelta(seconds=1)
            db.query(core.Resume).filter_by(user_id=user_id).delete(synchronize_session=False)
            db.query(core.User).filter_by(id=user_id).delete(synchronize_session=False)
            db.commit()
        with factory() as db:
            assert privacy.enqueue_artifact_cleanup(db) == 1
            deletion_id = db.query(models.EmployerArtifactDeletion).one().id
            db.commit()
        assert tasks.delete_artifact(deletion_id) == "deferred"
        # The in-flight write finishes after the first missing-object probe.
        return replace(sealed, gcs_object="gs://private-test/" + object_name, gcs_generation="43")

    monkeypatch.setattr(artifacts, "store", upload)
    assert _create(client).status_code == 409
    with factory() as db:
        deletion = db.query(models.EmployerArtifactDeletion).one()
        assert deletion.gcs_generation == "43"
        assert deletion.completed_at is None and deletion.absence_confirmed_at is None
        assert db.query(models.SealedApplicationArtifact).count() == 0
        assert db.query(models.EmployerArtifactUpload).count() == 0
    assert [call[0] for call in calls] == ["reload"]


def test_crash_before_attachment_recovers_unknown_generation_without_owner(context, monkeypatch):
    factory, _, _ = context
    calls = _fake_storage(monkeypatch)
    with factory() as db:
        db.add(models.EmployerArtifactUpload(id="upload_crashed", user_id=None,
            gcs_object="gs://private-test/applications/1/upload_crashed/sealed-hash",
            lease_until=utcnow() - timedelta(seconds=1)))
        db.commit()
    with factory() as db:
        assert privacy.enqueue_artifact_cleanup(db) == 1
        deletion = db.query(models.EmployerArtifactDeletion).one()
        assert deletion.gcs_generation == "0"
        identity = deletion.id
        db.commit()
    assert tasks.delete_artifact(identity) == "completed"
    assert [call[0] for call in calls] == ["reload", "delete"]
    assert calls[1][1]["if_generation_match"] == 88
    with factory() as db:
        assert db.get(models.EmployerArtifactDeletion, identity).gcs_object == "deleted"


def test_cleanup_preserves_a_legacy_hash_object_with_a_live_package(context, monkeypatch):
    factory, client, _ = context
    application = _create(client).json()
    uri = "gs://private-test/applications/1/shared-resume-hash"
    with factory() as db:
        row = db.get(models.EmployerApplication, application["id"])
        artifact = db.get(models.SealedApplicationArtifact, row.artifact_id)
        artifact.gcs_object, artifact.gcs_generation = uri, "42"
        db.add(models.EmployerArtifactDeletion(id="purge_shared", gcs_object=uri, gcs_generation="42"))
        db.commit()
    monkeypatch.setattr(storage, "Client", lambda: pytest.fail("A referenced hash object must not be deleted"))
    assert tasks.delete_artifact("purge_shared") == "completed"
    with factory() as db:
        assert db.get(models.EmployerArtifactDeletion, "purge_shared").last_error_code == "retained_active_reference"
        assert db.query(models.SealedApplicationArtifact).filter_by(gcs_object=uri).count() == 1


def test_exhausted_task_delivery_is_requeued_by_cleanup_maintenance(context, monkeypatch):
    factory, _, _ = context
    monkeypatch.setattr(dispatch, "SessionLocal", factory)
    with factory() as db:
        deletion = models.EmployerArtifactDeletion(id="purge_recover", gcs_object="gs://private-test/applications/1/resume-hash", gcs_generation="42")
        db.add(deletion)
        event = dispatch.enqueue(db, topic="employer.artifact-delete", aggregate_id=deletion.id,
                                 payload={"deletion_id": deletion.id}, key="purge-exhausted")
        event.execution_attempts = 6
        db.commit()
        event_id = event.id
    assert dispatch.process_event(event_id) == "failed"
    with factory() as db:
        deletion = db.get(models.EmployerArtifactDeletion, "purge_recover")
        assert deletion.completed_at is None and deletion.last_error_code == "task_delivery_exhausted"
        deletion.next_attempt_at = utcnow() - timedelta(seconds=1)
        db.commit()
    with factory() as db:
        assert privacy.enqueue_artifact_cleanup(db) == 1
        replacement = db.query(DispatchOutbox).filter(DispatchOutbox.id != event_id).one()
        replacement_id = replacement.id
        db.commit()
    calls = _fake_storage(monkeypatch)
    assert dispatch.process_event(replacement_id) == "completed"
    assert calls[0][1]["if_generation_match"] == 42
    with factory() as db:
        assert db.get(models.EmployerArtifactDeletion, "purge_recover").completed_at is not None


def test_unknown_upload_absence_uses_a_bounded_second_probe(context, monkeypatch):
    factory, _, _ = context
    calls = _fake_storage(monkeypatch, missing=True)
    with factory() as db:
        db.add(models.EmployerArtifactDeletion(id="purge_absent", gcs_object="gs://private-test/applications/1/never-uploaded", gcs_generation="0"))
        db.commit()
    assert tasks.delete_artifact("purge_absent") == "deferred"
    with factory() as db:
        deletion = db.get(models.EmployerArtifactDeletion, "purge_absent")
        assert deletion.completed_at is None
        deletion.absence_confirmed_at = utcnow() - timedelta(minutes=16)
        deletion.next_attempt_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert tasks.delete_artifact("purge_absent") == "completed"
    assert [call[0] for call in calls] == ["reload", "reload"]


def test_purge_cannot_touch_database_backups(context, monkeypatch):
    factory, _, _ = context
    with factory() as db:
        db.add(models.EmployerArtifactDeletion(id="purge_backup", gcs_object="gs://private-test/releases/database-backup.dump", gcs_generation="42"))
        db.commit()
    monkeypatch.setattr(storage, "Client", lambda: pytest.fail("Backup objects must never reach storage deletion"))
    with pytest.raises(ValueError, match="invalid_artifact_object"):
        tasks.delete_artifact("purge_backup")
    with factory() as db:
        deletion = db.get(models.EmployerArtifactDeletion, "purge_backup")
        assert deletion.completed_at is None and deletion.last_error_code == "ValueError"


def test_maintenance_recovers_upload_cleanup_when_discovery_is_disabled(context, monkeypatch):
    factory, _, _ = context
    monkeypatch.setenv("EMPLOYER_DISCOVERY_ENABLED", "false")
    monkeypatch.delenv("ANALYSIS_TASK_TOKEN", raising=False)
    monkeypatch.setattr(worker, "run_maintenance", lambda _db: SimpleNamespace(to_dict=lambda: {}))
    monkeypatch.setattr(worker, "dispatch_pending", lambda **_kwargs: {"dispatched": 0, "failed": 0})
    with factory() as db:
        db.add(models.EmployerArtifactUpload(id="upload_disabled", user_id=None,
            gcs_object="gs://private-test/applications/1/upload_disabled/abandoned",
            lease_until=utcnow() - timedelta(seconds=1)))
        db.commit()
        result = worker.execute_maintenance_task(x_cloudscheduler=None, x_hirewiz_task_token=None, db=db)
        assert result["artifact_cleanup_queued"] == 1 and result["sources_queued"] == 0
    with factory() as db:
        assert db.query(DispatchOutbox).one().topic == "employer.artifact-delete"
        assert db.query(models.EmployerArtifactUpload).count() == 0
