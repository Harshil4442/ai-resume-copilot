"""Actual ledger/version refusal for an old caller receiving a new image receipt."""
from __future__ import annotations

import json

from backend.app.domains.analysis import tasks
from backend.app.models import AnalysisRun, ResumeVersion, UsageEvent, User
from backend.app.services import document_ingestion, docx_artifact, llm_client
from backend.tests.test_analysis_runs import _create_tailoring_run, _database, _source_edit_response


def test_maintenance_image_mismatch_releases_reserved_tailoring_units_exactly_once(monkeypatch):
    engine, factory = _database()
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setenv("DOCUMENT_WORKER_URL", "https://maintenance-test.run.app/inspect")
    monkeypatch.setenv("DOCUMENT_WORKER_IMAGE_DIGEST", "synthetic-old@sha256:" + "a" * 64)
    monkeypatch.setenv("DOCUMENT_WORKER_POLICY_SHA256", "b" * 64)
    monkeypatch.setenv("DOCUMENT_WORKER_DOCX_RENDERER_VERSION", "reviewed renderer")
    monkeypatch.setenv("DOCUMENT_WORKER_DOCX_FONT_MANIFEST_SHA256", "c" * 64)
    monkeypatch.setattr(document_ingestion, "_identity_token", lambda audience: "synthetic identity")
    calls = []
    async def wrong_image(config, payload, token, expected, edits):
        calls.append(config.image)
        # Deliberately unaccepted metadata, never a manufactured native proof.
        return json.dumps({**expected, "worker_image": "synthetic-new@sha256:" + "d" * 64,
            "policy_sha256": config.policy, "scan": {}, "candidate_scan": {}, "layout": {}}).encode()
    monkeypatch.setattr(docx_artifact, "_worker_http", wrong_image)
    monkeypatch.setattr(llm_client, "tailor_resume_from_evidence",
                        lambda **kwargs: _source_edit_response(kwargs["source_units"]))
    try:
        identifier = _create_tailoring_run(factory)
        assert tasks.process_analysis_run(identifier) == "failed"
        assert tasks.process_analysis_run(identifier) == "failed"
        assert calls == ["synthetic-old@sha256:" + "a" * 64]
        with factory() as db:
            run = db.get(AnalysisRun, identifier)
            assert run.committed_units == 0 and run.usage_state == "released"
            assert run.result_payload is None and run.attempt_count == 1
            assert db.query(ResumeVersion).count() == 0 and db.get(User, 1).ai_credits == 50
            events = db.query(UsageEvent).order_by(UsageEvent.created_at).all()
            assert [(event.event_type, event.amount) for event in events] == [("reserve", -2), ("release", 2)]
    finally:
        engine.dispose()
