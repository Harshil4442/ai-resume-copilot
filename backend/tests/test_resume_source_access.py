"""Legacy absence differs from a broken direct source; only the former may fall back.

Uses disposable PostgreSQL schemas and synthetic storage/scanner. No native GCS,
real candidate bytes, browser credential, model call or production database.
"""
from __future__ import annotations

import pytest
from backend.app import models as core
from backend.app.domains.resume_uploads import service, storage, tasks
from backend.app.domains.resume_uploads.models import ResumeUpload
from backend.tests import test_resume_upload_quarantine as upload
from fastapi import HTTPException

pg_engine = upload.pg_engine
context = upload.context


def released(context):
    identifier, _ = upload.queue(context)
    assert tasks.process(context[0], identifier, context[1])
    with context[0]() as db:
        row = db.get(ResumeUpload, identifier)
        return identifier, row.result_resume_id


def test_only_owned_legacy_resume_receives_fallback_absence(context):
    with context[0]() as db:
        resume = core.Resume(user_id=1, original_filename="legacy.pdf", source_format="pdf", source_document=upload.CONTENT)
        db.add(resume)
        db.commit()
        with pytest.raises(HTTPException) as error:
            service.source_access(db, 1, resume.id, context[1])
        assert error.value.status_code == 404
        assert error.value.detail == {"code": "direct_source_not_found"}
        assert db.query(ResumeUpload).count() == 0


@pytest.mark.parametrize("case", ["other_owner", "missing"])
def test_unknown_or_other_owner_resume_does_not_receive_fallback_code(context, case):
    with context[0]() as db:
        resume = core.Resume(user_id=2, original_filename="other.pdf", source_format="pdf", source_document=upload.CONTENT)
        db.add(resume)
        db.commit()
        identity = resume.id if case == "other_owner" else resume.id + 1000
        with pytest.raises(HTTPException) as error:
            service.source_access(db, 1, identity, context[1])
        assert error.value.status_code == 404
        assert error.value.detail == {"code": "resume_not_found"}


@pytest.mark.parametrize("state", ["failed", "cancelled", "inspecting"])
def test_nonreleased_direct_binding_never_receives_fallback_code(context, state):
    identifier, resume_id = released(context)
    with context[0]() as db:
        db.get(ResumeUpload, identifier).state = state
        db.commit()
        with pytest.raises(HTTPException) as error:
            service.source_access(db, 1, resume_id, context[1])
        assert error.value.status_code == 409
        assert error.value.detail == {"code": "clean_source_unavailable"}


@pytest.mark.parametrize("field", ["clean_generation", "scan_receipt"])
def test_broken_generation_or_receipt_never_receives_fallback_code(context, field):
    identifier, resume_id = released(context)
    with context[0]() as db:
        row = db.get(ResumeUpload, identifier)
        setattr(row, field, "0" if field == "clean_generation" else {"sha256": "0" * 64})
        db.commit()
        with pytest.raises(HTTPException) as error:
            service.source_access(db, 1, resume_id, context[1])
        assert error.value.status_code == 409
        assert error.value.detail == {"code": "clean_source_unavailable"}


def test_signing_outage_never_receives_fallback_code(context, monkeypatch):
    _, resume_id = released(context)
    def unavailable(*args):
        raise storage.StorageUnavailable("synthetic signer outage")
    monkeypatch.setattr(context[1], "sign_read", unavailable)
    with context[0]() as db:
        with pytest.raises(HTTPException) as error:
            service.source_access(db, 1, resume_id, context[1])
        assert error.value.status_code == 503
        assert error.value.detail == {"code": "clean_source_unavailable"}


@pytest.mark.parametrize("binding_owner", [None, 2])
def test_retained_direct_binding_with_changed_owner_never_falls_back(context, binding_owner, monkeypatch):
    identifier, resume_id = released(context)
    with context[0]() as db:
        row = db.get(ResumeUpload, identifier)
        row.user_id, row.state, row.scan_receipt = binding_owner, "failed", None
        db.commit()
        def no_sign(*args):
            pytest.fail("A broken direct binding must not issue a storage grant")
        monkeypatch.setattr(context[1], "sign_read", no_sign)
        with pytest.raises(HTTPException) as error:
            service.source_access(db, 1, resume_id, context[1])
        assert error.value.status_code == 409
        assert error.value.detail == {"code": "clean_source_unavailable"}


def test_other_owner_cannot_discover_a_released_direct_source(context):
    _, resume_id = released(context)
    with context[0]() as db:
        with pytest.raises(HTTPException) as error:
            service.source_access(db, 2, resume_id, context[1])
        assert error.value.status_code == 404
        assert error.value.detail == {"code": "resume_not_found"}
