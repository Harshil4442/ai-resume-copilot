"""Synthetic transaction ports only; no PostgreSQL, SQL execution or native proof."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import models
from app.domains.career import evidence_fence_definition as definition
from app.domains.career import evidence_write_fence as fence


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def mappings(self):
        return self

    def all(self):
        return self.value


def catalog():
    return {"tgtype": 30, "tgenabled": "A", "tgnargs": 0, "tgargs": b"", "unconditional": True,
        "columns": "", "tgconstraint": 0, "tgdeferrable": False, "tginitdeferred": False,
        "tgname": "evidence_writer_fence", "proname": "evidence_writer_fence", "pronargs": 0,
        "returns_trigger": True, "prosecdef": False, "proconfig": ["search_path=pg_catalog"],
        "prosrc": definition.FUNCTION_BODY, "lanname": "plpgsql", "function_namespace": "owned_schema"}


class Port:
    def __init__(self):
        self.dialect = SimpleNamespace(name="postgresql")
        self.rows = [catalog()]
        self.marker = None
        self.calls = []
        self.rollbacks = 0
        self.is_active = True
        self.fail = None

    def get_bind(self):
        return self

    def connection(self):
        return self

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        if self.fail and self.fail(sql):
            raise ValueError("Synthetic transport failure")
        if sql == "SELECT current_schema()":
            return Result("owned_schema")
        if "FROM pg_catalog.pg_class" in sql:
            assert params == {"namespace": "owned_schema"} and "LIMIT 2" in sql
            return Result(self.rows)
        if sql == "SELECT current_setting(:setting, true)":
            return Result(self.marker)
        assert params["setting"] == definition.SETTING
        self.marker = params.get("version", "")
        return Result(self.marker)

    def rollback(self):
        self.rollbacks += 1
        self.marker = None
        self.is_active = True

    def flush(self, items):
        assert self.marker == definition.VERSION
        self.calls.append(("finite_flush", list(items)))


def test_exact_catalog_and_temporary_marker_wrap_only_the_given_flush():
    db = Port()
    row = models.EvidenceItem(id="synthetic", user_id=1)
    fence.flush_evidence_writes(db, [row])
    assert db.marker == "" and db.rollbacks == 0
    assert next(value for sql, value in db.calls if sql == "finite_flush") == [row]
    assert len([sql for sql, _ in db.calls if "set_config" in sql]) == 2


@pytest.mark.parametrize("field,value", [("tgtype", 31), ("tgenabled", "O"), ("tgnargs", 1),
    ("tgargs", b"extra"), ("unconditional", False), ("columns", "1"), ("tgconstraint", 1),
    ("tgdeferrable", True), ("tginitdeferred", True), ("tgname", "another_trigger"),
    ("proname", "another_function"), ("pronargs", 1), ("returns_trigger", False), ("prosecdef", True),
    ("proconfig", []), ("prosrc", "BEGIN RETURN NULL; END;"), ("lanname", "sql"),
    ("function_namespace", "other_schema")])
def test_catalog_drift_refuses_before_marker_or_original_write(field, value):
    db = Port()
    db.rows[0][field] = value
    with pytest.raises(fence.EvidenceWriteFenceDenied, match="schema26_fence_required"):
        with fence.evidence_write_scope(db):
            pytest.fail("An unqualified fence must not reach any write")
    assert not any("set_config" in sql for sql, _ in db.calls)


@pytest.mark.parametrize("rows", [[], [catalog(), catalog()]])
def test_missing_or_extra_trigger_never_becomes_an_accepted_prefix(rows):
    db = Port()
    db.rows = rows
    with pytest.raises(fence.EvidenceWriteFenceDenied, match="schema26_fence_required"):
        with fence.evidence_write_scope(db):
            pytest.fail("Missing or extra trigger must refuse")


@pytest.mark.parametrize("marker", ["cas_v1", "unknown"])
def test_preexisting_or_nested_authority_rolls_back_instead_of_inheriting_it(marker):
    db = Port()
    db.marker = marker
    with pytest.raises(fence.EvidenceWriteFenceDenied, match="already_active"):
        with fence.evidence_write_scope(db):
            pytest.fail("No inherited marker may enable a write")
    assert db.rollbacks == 1 and db.marker is None


def test_body_failure_preserves_exception_identity_and_rolls_back_without_clear_on_aborted_sql():
    db, original = Port(), ValueError("Synthetic original statement error")
    with pytest.raises(ValueError) as caught:
        with fence.evidence_write_scope(db):
            assert db.marker == definition.VERSION
            raise original
    assert caught.value is original and db.rollbacks == 1 and db.marker is None
    assert len([sql for sql, _ in db.calls if "set_config" in sql]) == 1


@pytest.mark.parametrize("phase", ["arm", "clear"])
def test_marker_transport_failure_never_returns_a_writable_session(phase):
    db = Port()
    db.fail = lambda sql: "set_config" in sql and (phase == "arm" or "''" in sql)
    with pytest.raises(ValueError, match="Synthetic transport failure"):
        with fence.evidence_write_scope(db):
            assert phase == "clear"
    assert db.rollbacks == 1 and db.marker is None


def test_unknown_dialect_refuses_without_connection_or_flush():
    db = Port()
    db.dialect.name = "unknown"
    with pytest.raises(fence.EvidenceWriteFenceDenied, match="dialect_unqualified"):
        fence.flush_evidence_writes(db, [models.EvidenceItem(id="synthetic")])
    assert not db.calls


def test_explicit_local_sqlite_is_not_labeled_native_fence_proof():
    db = Port()
    db.dialect.name = "sqlite"
    with fence.evidence_write_scope(db):
        assert db.marker is None
    assert not db.calls


@pytest.mark.parametrize("items", [[], [object()]])
def test_omitted_or_non_evidence_flush_has_no_native_side_effect(items):
    db = Port()
    with pytest.raises(fence.EvidenceWriteFenceDenied, match="rows_required"):
        fence.flush_evidence_writes(db, items)
    assert not db.calls


def test_explicit_namespace_drift_refuses_without_inspecting_another_schema():
    db = Port()
    with pytest.raises(fence.EvidenceWriteFenceDenied, match="namespace_changed"):
        fence.require_installed(db, "other_schema")
    assert len(db.calls) == 1


@pytest.mark.parametrize("version", [None, "0" * 64])
def test_individual_delete_requires_the_exact_shown_version_before_write_scope(monkeypatch, version):
    from backend.app.domains.career import service
    from backend.tests.test_source_transcription_review import PersistencePort
    from fastapi import HTTPException

    db = PersistencePort()
    calls = []
    monkeypatch.setattr(service, "flush_evidence_writes", lambda *_: calls.append("flush"))
    with pytest.raises(HTTPException) as error:
        service.delete_evidence(db, 1, db.item.id, expected_fingerprint=version)
    assert error.value.status_code == 409 and error.value.detail["code"] == "evidence_refresh_required"
    assert not calls and db.commits == 0 and db.rollbacks == 1
    assert db.item in db.rows


def test_foreign_delete_is_404_before_version_check_or_catalog_disclosure(monkeypatch):
    from backend.app.domains.career import service
    from backend.tests.test_source_transcription_review import PersistencePort
    from fastapi import HTTPException

    db = PersistencePort()
    monkeypatch.setattr(service.schemas, "evidence_snapshot_fingerprint",
        lambda *_: pytest.fail("Foreign item must not be fingerprinted"))
    with pytest.raises(HTTPException) as error:
        service.delete_evidence(db, 2, db.item.id, expected_fingerprint="0" * 64)
    assert error.value.status_code == 404 and db.commits == 0


def test_exact_delete_locks_and_refreshes_then_flushes_only_the_owned_row(monkeypatch):
    from backend.app.domains.career import service
    from backend.tests.test_source_transcription_review import PersistencePort

    db = PersistencePort()
    shown = service.schemas.evidence_snapshot_fingerprint(db.item)
    deleted, flushed = [], []
    db.delete = lambda item: deleted.append(item)
    original_query = db.query
    no_versions = SimpleNamespace(all=lambda: [])
    no_versions.filter = lambda *_: no_versions
    db.query = lambda model: no_versions if model is service.models.ResumeVersion.id else original_query(model)
    monkeypatch.setattr(service, "flush_evidence_writes", lambda _db, rows: flushed.extend(rows))
    service.delete_evidence(db, 1, db.item.id, expected_fingerprint=shown)
    assert deleted == flushed == [db.item] and db.commits == 1
    assert any(fresh and locked and "FOR UPDATE" in sql for sql, fresh, locked in db.queries)


@pytest.mark.parametrize("token", [None, "not-a-sha256"])
def test_delete_http_foreign404_precedes_even_malformed_version_comparison(token):
    from backend.app.database import get_db
    from backend.app.routers.v1.career import require_career_workspace, router
    from backend.app.security import get_current_user
    from backend.tests.test_source_transcription_review import PersistencePort
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    db = PersistencePort()
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=2)
    app.dependency_overrides[require_career_workspace] = lambda: None
    with TestClient(app) as client:
        response = client.delete("/api/v1/evidence-items/" + db.item.id,
            params={} if token is None else {"expected_fingerprint": token})
    assert response.status_code == 404 and db.commits == 0
