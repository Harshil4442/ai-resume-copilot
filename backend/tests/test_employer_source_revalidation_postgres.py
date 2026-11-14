"""Real source-lease races in an owned local PostgreSQL schema, never cloud QA.

CI's existing disposable admission database is mandatory there. Local absence is
an explicit skip and is never reported as PostgreSQL concurrency qualification.
All employer HTTP is an injected transport; no provider connection is used.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import httpx
import pytest
from backend.app import models as core
from backend.app.database import Base
from backend.app.domains.common import utcnow
from backend.app.domains.employer import (
    catalogue_search,
    connectors,
    models,
    source_revalidation,
    tasks,
)
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def owned_source_database(monkeypatch):
    configured = os.getenv("HIREWIZ_TEST_POSTGRES_URL")
    if not configured:
        if os.getenv("GITHUB_ACTIONS") == "true" or os.getenv("HIREWIZ_APP_COMPATIBILITY_REQUIRED") == "true":
            pytest.fail("Mandatory source concurrency evidence needs the prepared owned PostgreSQL URL")
        pytest.skip("Owned PostgreSQL URL absent; source concurrency is not locally qualified")
    url = make_url(configured)
    if (url.drivername not in {"postgresql+psycopg", "postgresql+psycopg2"} or url.host != "127.0.0.1"
            or url.port not in {55433, 55434} or url.database != "hirewiz_admission_test"):
        pytest.fail("Source concurrency accepts only the existing disposable local admission database")
    schema = "source_revalidation_" + uuid4().hex
    admin = create_engine(url, hide_parameters=True)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, hide_parameters=True, connect_args={
        "options": f"-csearch_path={schema} -clock_timeout=4000 -cstatement_timeout=10000"})
    try:
        Base.metadata.create_all(engine, tables=[core.User.__table__, models.EmployerSource.__table__,
                                                 models.EmployerPosting.__table__])
        factory = sessionmaker(bind=engine, autoflush=False)
        monkeypatch.setattr(tasks, "SessionLocal", factory)
        monkeypatch.setattr(tasks.config, "discovery_enabled", lambda: True)
        monkeypatch.setattr(tasks.config, "catalogue_search_enabled", lambda: False)
        monkeypatch.setattr(tasks.service, "bounded_search_maintenance", lambda _: None)
        monkeypatch.setattr(catalogue_search, "maintenance_step", lambda _: None)
        monkeypatch.setattr(source_revalidation, "pipeline_revision", lambda: "a" * 64)
        with factory() as db:
            source = models.EmployerSource(id="source-owned-pg", employer="Owned employer", platform="greenhouse",
                board_token="owned", region="global", careers_url="https://owned.example/careers",
                allowed_hosts=["owned.example"], verification_url="https://owned.example/careers",
                verification_note="Synthetic source concurrency fixture", last_success_at=utcnow(), status="healthy")
            db.add(source)
            db.flush()
            db.add(models.EmployerPosting(id="job-owned-pg", source_id=source.id, external_id="1",
                title="Python engineer", employer=source.employer, location="India", description="Python services",
                canonical_url="https://job-boards.greenhouse.io/owned/jobs/1",
                apply_url="https://job-boards.greenhouse.io/owned/jobs/1", content_sha256="b" * 64,
                skills=["Python"]))
            db.commit()
            snapshot = source_revalidation.capture_snapshot(source, db.query(models.EmployerPosting).all(), pipeline="a" * 64)
            assert snapshot is not None
        class Cache:
            raw = None
            published = 0
            def get(self, _key):
                return self.raw
            def setex(self, _key, _ttl, raw):
                self.raw = raw
                self.published += 1
                return True
            def close(self):
                pass
        cache = Cache()
        assert source_revalidation.publish(snapshot, {"collection": {"etag": 'W/"owned"', "vary": []}, "details": {}}, client=cache)
        monkeypatch.setattr(source_revalidation, "_redis_client", lambda: cache)
        yield factory, cache
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def _held_source_read(monkeypatch):
    entered, release, requests = Event(), Event(), []
    original = connectors.fetch_source_scan
    def handler(request):
        requests.append(request)
        assert request.headers["If-None-Match"] == 'W/"owned"'
        entered.set()
        assert release.wait(timeout=10), "Owned HTTP barrier was not released"
        return httpx.Response(304, headers={"ETag": '"owned"'})
    def fetch(source, **kwargs):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return original(source, client, **kwargs)
    monkeypatch.setattr(connectors, "fetch_source_scan", fetch)
    return entered, release, requests


@pytest.mark.parametrize("change", ["source-config", "posting-date", "lease-owner"])
def test_postgres_source_changes_during_http_cannot_commit_or_publish_old_snapshot(owned_source_database, monkeypatch, change):
    from datetime import UTC, datetime
    factory, cache = owned_source_database
    entered, release, _ = _held_source_read(monkeypatch)
    with factory() as db:
        before = db.get(models.EmployerSource, "source-owned-pg").last_success_at
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(tasks.refresh_source, "source-owned-pg")
        try:
            assert entered.wait(timeout=5)
            # This must acquire/commit while HTTP is held: no source transaction
            # or posting locks are retained across the external-read boundary.
            with factory() as db:
                source = db.query(models.EmployerSource).filter_by(id="source-owned-pg").with_for_update().one()
                if change == "source-config":
                    source.verification_note = "Replacement source configuration"
                elif change == "posting-date":
                    db.get(models.EmployerPosting, "job-owned-pg").source_updated_at = datetime(2026, 10, 9, tzinfo=UTC)
                else:
                    source.scan_token = "scan-owned-replacement"
                db.commit()
        finally:
            release.set()
        assert pending.result(timeout=10) == "superseded"
    with factory() as db:
        source = db.get(models.EmployerSource, "source-owned-pg")
        assert source.last_success_at == before
        assert db.get(models.EmployerPosting, "job-owned-pg").is_open
        if change == "lease-owner":
            assert source.scan_token == "scan-owned-replacement"
    assert cache.published == 1


def test_postgres_competing_refresh_observes_running_lease_without_another_get(owned_source_database, monkeypatch):
    factory, cache = owned_source_database
    entered, release, requests = _held_source_read(monkeypatch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(tasks.refresh_source, "source-owned-pg")
        try:
            assert entered.wait(timeout=5)
            assert tasks.refresh_source("source-owned-pg") == "running"
            assert len(requests) == 1
        finally:
            release.set()
        assert pending.result(timeout=10) == "completed"
    assert cache.published == 2
    with factory() as db:
        assert db.get(models.EmployerSource, "source-owned-pg").scan_token is None


@pytest.mark.parametrize("change", ["configuration", "claim", "checkpoint"])
def test_postgres_permanent_error_cannot_record_against_changed_source(owned_source_database, monkeypatch, change):
    from datetime import timedelta

    factory, cache = owned_source_database
    def obsolete_failure(_source, **_kwargs):
        with factory() as db:
            source = db.get(models.EmployerSource, "source-owned-pg", with_for_update=True)
            if change == "configuration":
                source.board_token = "replacement"
            elif change == "claim":
                source.scan_token = "replacement-claim"
            else:
                source.last_success_at += timedelta(minutes=1)
            db.commit()
        raise connectors.ConnectorError("job_destination_tenant_mismatch", safe_to_retry=False)

    monkeypatch.setattr(connectors, "fetch_source_scan", obsolete_failure)
    with factory() as db:
        current = db.get(models.EmployerSource, "source-owned-pg")
        previous_due = current.next_refresh_at
        previous_success = current.last_success_at
    with pytest.raises(connectors.ConnectorError, match="job_destination_tenant_mismatch"):
        tasks.refresh_source("source-owned-pg")
    with factory() as db:
        current = db.get(models.EmployerSource, "source-owned-pg")
        assert current.status == "healthy" and current.last_error_code is None
        assert current.next_refresh_at == previous_due
        assert current.last_success_at == previous_success + timedelta(minutes=int(change == "checkpoint"))
        assert current.scan_token is not None
        if change == "claim":
            assert current.scan_token == "replacement-claim"
        assert db.get(models.EmployerPosting, "job-owned-pg").is_open
    assert cache.published == 1
