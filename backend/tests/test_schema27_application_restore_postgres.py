"""Owned schema27 SQL restore evidence using an explicitly selected local PG17.

Set HIREWIZ_SCHEMA27_LOCAL_RESTORE=true with the disposable fixture on port
55435. The historical CI transfer clients intentionally allow only schema9.
Set HIREWIZ_SCHEMA27_PHYSICAL_PG_BIN to a PG17 bin directory for the separate
physical recovery rehearsal, which creates and owns both temporary clusters.
Set HIREWIZ_SCHEMA27_LOGICAL_PG_BIN for the populated logical archive rehearsal.
Set HIREWIZ_SCHEMA27_CANONICAL_PG_BIN for the migration-built variant.
"""

import hashlib
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from backend.app.billing.service import process_razorpay_webhook
from backend.app.domains.career import evidence_fence_definition, evidence_write_fence
from backend.app.domains.career import schemas as career_schemas
from backend.app.domains.career import service as career_service
from backend.app.domains.credits import service
from backend.app.domains.credits.models import CreditExecutionAttempt, CreditExecutionHold
from backend.app.domains.dispatch.models import DispatchOutbox
from backend.app.domains.employer import admissions, artifacts, connectors, tasks
from backend.app.domains.employer import credits as employer_credits
from backend.app.domains.employer import models as employer_models
from backend.app.domains.employer import service as employer_service
from backend.app.domains.resume_uploads.models import ResumeUpload
from backend.app.models import (
    EvidenceItem,
    ModelCostLiability,
    PaymentOrder,
    Resume,
    ResumeVersion,
    User,
)
from backend.app.services.resume_artifacts import render_resume_version
from fastapi import HTTPException
from psycopg.errors import CheckViolation, ForeignKeyViolation, InsufficientPrivilege
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool
from test_employer_admissions_postgres import _migrate
from test_employer_services import ANSWERS, FORM
from test_razorpay_billing import _capture_payload
from test_resume_layout import _edit, _first_unit, _pdf
from test_unified_credits import reserve

from scripts import notification_schema27_contract as schema27

RESTORE_TABLES = (
    "users", "resumes", "resume_uploads", "payment_orders", "payment_transactions",
    "payment_events", "entitlement_ledger", "credit_purchase_quotes", "credit_accounts",
    "credit_grants", "credit_events", "credit_reservations", "credit_allocations",
    "credit_execution_holds", "credit_execution_attempts", "model_cost_liabilities",
    "dispatch_outbox",
)
MIGRATION_SEEDS = {"alembic_version", "employer_catalogue_clock"}
CATALOGUE_COPY_TRIGGERS = (
    ("employer_sources", "employer_catalogue_source_insert"),
    ("employer_postings", "employer_catalogue_posting_insert"),
)
EMPTY_CATALOGUE_CLOCK = [{
    "id": 1, "watermark": 0, "inventory_state": "sources",
    "source_cursor": None, "posting_cursor": None, "prune_cursor": None,
}]
SOURCE_CONSTRAINT = (
    "CHECK (((source_format)::text = ANY ((ARRAY['pdf'::character varying, "
    "'docx'::character varying, 'tex'::character varying, 'texzip'::character varying])::text[])))"
)
RESTORED_CONSTRAINT = (
    "CHECK (((source_format)::text = ANY (ARRAY[('pdf'::character varying)::text, "
    "('docx'::character varying)::text, ('tex'::character varying)::text, "
    "('texzip'::character varying)::text])))"
)


def upload_constraint(db):
    return db.execute(text(
        "SELECT conname,contype,convalidated,condeferrable,condeferred,conislocal,coninhcount,connoinherit,"
        "conkey,pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid='public.resume_uploads'::regclass AND conname='ck_resume_upload_format'"
    )).one()


def snapshot(engine):
    """Compare every ordinary public table and exact sequence runtime state."""
    with engine.connect() as db:
        assert db.scalar(text("SELECT version_num FROM public.alembic_version")) == schema27.SCHEMA
        relations = db.execute(text(
            "SELECT c.relname,c.relkind FROM pg_class AS c JOIN pg_namespace AS n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relkind IN ('r','S') ORDER BY c.relname"
        ))
        tables, sequences = {}, {}
        quote = engine.dialect.identifier_preparer.quote
        for name, kind in relations:
            relation = f"public.{quote(name)}"
            if kind == "S":
                sequences[name] = tuple(db.execute(text(f"SELECT last_value,is_called FROM {relation}")).one())
            else:
                tables[name] = db.execute(text(
                    f"SELECT to_jsonb(row) FROM {relation} AS row ORDER BY to_jsonb(row)"
                )).scalars().all()
        return tables, sequences


def create_data_archive(dump, restore, db_options, source_name, archive, toc, *, excluded_seeds=MIGRATION_SEEDS):
    subprocess.run([dump, "--data-only", "--format=custom", "--no-password",
                    "--file", str(archive), "--dbname", source_name],
                   env=db_options, check=True, timeout=30, capture_output=True)
    archive_entries = subprocess.run([restore, "--list", str(archive)], env=db_options,
                                     check=True, timeout=5, capture_output=True, text=True).stdout
    excluded, retained = set(), []
    for line in archive_entries.splitlines():
        fields = line.split()
        if len(fields) >= 7 and fields[3:6] == ["TABLE", "DATA", "public"] and fields[6] in excluded_seeds:
            assert fields[6] not in excluded and fields[7:] == ["hirewiz"]
            excluded.add(fields[6])
            continue
        retained.append(line)
    assert excluded == excluded_seeds
    toc.write_text("\n".join(retained) + "\n")


def restore_data(restore, db_options, database, archive, toc=None):
    command = [restore, "--data-only", "--single-transaction", "--exit-on-error",
               "--no-owner", "--no-privileges", "--no-password", "--dbname", database]
    if toc is not None:
        command.extend(["--use-list", str(toc)])
    return subprocess.run([*command, str(archive)], env=db_options,
                          timeout=30, capture_output=True, text=True)


def assert_payment_and_reserve_replay(engine, stamp, capture, reservation_id, source_bytes, credits):
    with Session(engine) as db:
        wallet = service.wallet(db, user_id=1, now=stamp)
        assert (wallet.available_credits, wallet.reserved_credits) == (credits - 8, 8)
        assert db.get(Resume, 1).source_document == source_bytes
        assert process_razorpay_webhook(db, raw_body=capture, provider_event_id="evt_owned_schema27",
                                        provider_mode="test").status == "duplicate"
        assert reserve(db, now=stamp).id == reservation_id
        db.commit()


def seed_application(source, stamp):
    source_bytes = b"\\documentclass{article}\n\\begin{document}Synthetic Candidate\\end{document}\n"
    with Session(source) as db:
        if db.get(User, 1) is None:
            db.add(User(id=1, email="restore27@example.test", ai_credits=17, job_service_credits=13))
            db.flush()
        db.add(Resume(id=1, user_id=1, original_filename="synthetic.tex",
                      source_document=source_bytes, source_format="tex"))
        db.add(ResumeUpload(id="owned-upload", user_id=1, idempotency_sha256="a" * 64,
                            request_sha256="b" * 64, original_filename="synthetic.tex",
                            source_format="tex", media_type="application/x-tex", size_bytes=len(source_bytes),
                            source_sha256=hashlib.sha256(source_bytes).hexdigest(),
                            quarantine_bucket="owned-quarantine", quarantine_name="owned/source.tex",
                            clean_bucket="owned-clean", clean_name="owned/source.tex",
                            expires_at=stamp + timedelta(days=1), upload_grant_expires_at=stamp))
        quote = service.issue_purchase_quote(db, user_id=1, sku="starter_pass",
                                            idempotency_key="owned-quote", checkout_enabled=True, now=stamp)
        order_id = "owned-schema27-order"
        quote = service.accept_purchase_quote(db, user_id=1, quote_id=quote.id,
                                              order_id=order_id, now=stamp)
        view = service.quote_response(quote, now=stamp)
        payment = PaymentOrder(public_id=order_id, user_id=1, provider_mode="test",
                               provider_order_id="order_owned_schema27", sku=view.sku,
                               catalog_version=view.catalog_version,
                               cost_policy_snapshot={"unified_purchase_quote": quote.snapshot},
                               entitlement_kind="credit_pass", entitlement_quantity=view.credits,
                               billing_country_confirmed_at=stamp, gross_amount_minor=view.amount_minor,
                               status="created")
        db.add(payment)
        db.commit()
        capture = json.dumps(_capture_payload(payment)).encode()
        assert process_razorpay_webhook(db, raw_body=capture, provider_event_id="evt_owned_schema27",
                                        provider_mode="test").status == "processed"
        service.issue_purchase_quote(db, user_id=1, sku="starter_pass",
                                     idempotency_key="owned-active-quote", checkout_enabled=True, now=stamp)
        reservation = reserve(db, now=stamp)
        reservation_id = reservation.id
        db.add(ModelCostLiability(id="owned-liability", financial_group_id="owned-model-group",
                                  attempt_number=1, provider="google", model="synthetic-model",
                                  endpoint_key="d" * 64, pricing_quote={"version": "synthetic-v1"},
                                  input_token_estimate=100, reserved_cost_micros=12_345,
                                  cost_state="outcome_unknown"))
        db.add(CreditExecutionHold(id="owned-hold", user_id=1, reservation_id=reservation_id,
                                   financial_group_id="owned-model-group", route_id="synthetic-route",
                                   funding_kind="paid", reserved_minor=360, native_reserved_minor=0,
                                   state="outcome_unknown", delivery_state="pending",
                                   snapshot={"fixture": "synthetic"}, created_at=stamp))
        db.flush()
        db.add(CreditExecutionAttempt(id="owned-attempt", hold_id="owned-hold",
                                      liability_id="owned-liability", ordinal=1, state="outcome_unknown",
                                      claim_key="owned-claim", started_at=stamp))
        db.add(DispatchOutbox(id="owned-outbox", topic="analysis.run", aggregate_id="run-one",
                              idempotency_key="owned-outbox-request", payload={"run_id": "run-one"}))
        evidence = EvidenceItem(id="owned-evidence", user_id=1, resume_id=1, category="experience",
                                title="Synthetic evidence", evidence_text="Verified fixture example")
        db.add(evidence)
        evidence_write_fence.flush_evidence_writes(db, [evidence])
        db.execute(text("SELECT setval('public.users_id_seq',41,true),setval('public.resumes_id_seq',72,false)"))
        db.commit()
    return source_bytes, capture, reservation_id, view.credits


def publish_employer(source):
    with Session(source) as db:
        db.add(employer_models.EmployerSource(id="owned-employer", employer="Synthetic Employer", platform="greenhouse",
                                              board_token="synthetic", careers_url="https://example.test/careers",
                                              verification_url="https://example.test/careers", verification_note="Synthetic"))
        db.commit()


def seed_application_recovery(engine, stamp):
    """Build the retained send boundary without calling a provider."""
    original = _pdf()
    with Session(engine) as db:
        source = db.get(employer_models.EmployerSource, "owned-employer")
        posting = employer_models.EmployerPosting(
            id="owned-posting", source_id=source.id, external_id="owned-requisition", title="Software Engineer",
            employer=source.employer, description="Synthetic software engineering role",
            canonical_url="https://example.test/careers/owned-requisition", apply_url="https://example.test/careers/owned-requisition",
            content_sha256="a" * 64, first_seen_at=stamp, last_checked_at=stamp,
        )
        posting.opening_key = admissions.opening_identity(posting, source)
        resume = Resume(id=2, user_id=1, original_filename="synthetic.pdf", source_format="pdf", source_document=original)
        db.add_all([posting, resume])
        db.flush()
        edit = _edit(_first_unit(original, "pdf"))
        evidence = EvidenceItem(id="owned-approved-application-evidence", user_id=1, resume_id=resume.id,
                                category="experience", title="Synthetic application evidence",
                                evidence_text=edit["original_text"], approval_state="approved")
        edit["evidence_ids"] = [evidence.id]
        db.add(evidence)
        evidence_write_fence.flush_evidence_writes(db, [evidence])
        version = ResumeVersion(id="owned-approved-version", user_id=1, resume_id=resume.id, version_number=1,
                                label="Approved synthetic version", evidence_ids=[evidence.id], structured_content={
                                    "format_preservation": "source", "source_format": "pdf", "source_edits": [edit],
                                })
        db.add(version)
        db.flush()
        version = career_service.update_resume_version_state(
            db, 1, version.id, career_schemas.ResumeVersionStateUpdate(approval_state="approved"),
        )
        rendered = render_resume_version(version, resume, "pdf")
        assert rendered.content != original
        artifact = employer_models.SealedApplicationArtifact(
            id="owned-approved-artifact", user_id=1, resume_id=resume.id, resume_version_id=version.id,
            content=rendered.content, filename=rendered.filename, media_type=rendered.media_type,
            size_bytes=len(rendered.content), sha256=hashlib.sha256(rendered.content).hexdigest(),
        )
        db.add(artifact)
        db.flush()
        application_id = "owned-unknown-application"
        balance = db.get(User, 1).job_service_credits
        reservation = employer_credits.reserve(
            db, user_id=1, operation="job_application", source_id=application_id, unit_price=5, count=1,
        )
        actions = ["upload", "submit"]
        application = employer_models.EmployerApplication(
            id=application_id, user_id=1, posting_id=posting.id, opening_key=posting.opening_key,
            employer_key=admissions.employer_identity(source), admission_snapshot=admissions.quote(source, posting, now=stamp),
            pricing_snapshot={"unit_price": 5, "pricing_version": reservation.pricing_version,
                              "cost_policy_snapshot": reservation.cost_policy_snapshot},
            idempotency_key="owned-application", input_fingerprint="b" * 64,
            active_key=f"1:{posting.opening_key}", resume_id=resume.id, resume_choice="tailored",
            resume_version_id=version.id, artifact_id=artifact.id, form=FORM, answers=ANSWERS, consents={},
            job_content_sha256=posting.content_sha256, application_mode="api", status="submitting",
            allowed_actions=actions, approved_at=stamp, approval_expires_at=stamp + timedelta(minutes=10),
            credit_cost=5, reservation_id=reservation.id,
        )
        application.package_digest = employer_service._package_digest(application, posting, source, artifact)
        application.approved_digest = application.package_digest
        db.add(application)
        db.flush()
        db.add(employer_models.EmployerApplicationApproval(
            id="owned-application-approval", application_id=application_id, package_digest=application.package_digest,
            review_snapshot={"artifact_sha256": artifact.sha256, "resume_version_id": version.id}, allowed_actions=actions,
            approved_at=stamp, expires_at=application.approval_expires_at,
        ))
        admissions.reserve(db, application, source, now=stamp)
        admissions.launch(db, application, source, now=stamp)
        db.add(employer_models.EmployerApplicationAttempt(
            id="owned-application-attempt", application_id=application_id, launch_token="owned-launch-token",
            package_digest=application.package_digest, state="unknown", started_at=stamp, completed_at=stamp,
            error_code="submission_outcome_unknown",
        ))
        tasks._mark_unknown(db, application)
        db.commit()
        return application_id, original, rendered.content, balance - reservation.reserved_amount


def assert_application_recovery(engine, expected, monkeypatch):
    application_id, original, approved_pdf, remaining = expected

    def deny_transport(*_args, **_kwargs):
        pytest.fail("Restored unknown application attempted employer transport")

    monkeypatch.setattr(tasks, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(connectors, "load_form", deny_transport)
    monkeypatch.setattr(connectors, "submit_greenhouse", deny_transport)
    assert tasks.execute_application(application_id) == "unknown"
    assert tasks.execute_application(application_id) == "unknown"
    with Session(engine) as db:
        application = db.get(employer_models.EmployerApplication, application_id)
        approval = db.query(employer_models.EmployerApplicationApproval).filter_by(application_id=application_id).one()
        attempt = db.query(employer_models.EmployerApplicationAttempt).filter_by(application_id=application_id).one()
        admission = db.query(employer_models.EmployerAdmission).filter_by(application_id=application_id).one()
        reservation = db.get(employer_models.ServiceCreditReservation, application.reservation_id)
        event = db.query(employer_models.ServiceCreditEvent).filter_by(source_id=application_id).one()
        posting = db.get(employer_models.EmployerPosting, application.posting_id)
        version = career_service.get_resume_version(db, 1, application.resume_version_id)
        resume = db.get(Resume, version.resume_id)
        artifact = db.get(employer_models.SealedApplicationArtifact, application.artifact_id)
        assert resume.source_document == original and version.approval_state == "approved"
        assert artifact.resume_version_id == version.id and artifacts.read(artifact) == approved_pdf
        assert render_resume_version(version, resume, "pdf").content == approved_pdf
        assert approval.review_snapshot == {"artifact_sha256": artifact.sha256, "resume_version_id": version.id}
        assert application.approved_digest == approval.package_digest == attempt.package_digest == application.package_digest
        assert posting.is_open and posting.opening_key == application.opening_key == admission.opening_key
        assert application.status == attempt.state == admission.state == "unknown"
        assert attempt.launch_token == "owned-launch-token" and attempt.receipt is application.receipt is None
        assert admission.possible_send_at is not None and admission.active_key == f"1:{posting.opening_key}"
        assert application.charged_credits == reservation.committed_amount == reservation.released_amount == 0
        assert reservation.state == "reserved" and reservation.reserved_amount == 5
        assert (event.event_type, event.amount, event.balance_after) == ("reserve", -5, remaining)
        assert db.get(User, 1).job_service_credits == remaining

        # A replacement cannot bypass the retained possible-send claim for the same opening.
        replacement = employer_models.EmployerApplication(
            id="owned-replacement-application", user_id=1, opening_key=application.opening_key,
            employer_key=application.employer_key, credit_cost=application.credit_cost,
            admission_snapshot=application.admission_snapshot,
        )
        source = db.get(employer_models.EmployerSource, posting.source_id)
        assert employer_service._package_digest(application, posting, source, artifact) == approval.package_digest
        with pytest.raises(HTTPException) as conflict:
            admissions.reserve(db, replacement, source)
        assert conflict.value.status_code == 409 and conflict.value.detail["code"] == "canonical_opening_conflict"


def test_schema27_restore_preserves_active_obligations_and_payment_replay(tmp_path, record_property):
    if os.getenv("HIREWIZ_SCHEMA27_LOCAL_RESTORE") != "true":
        pytest.skip("Requires an explicitly selected local PG17 restore rehearsal")
    if os.getenv("GITHUB_ACTIONS") == "true":
        pytest.fail("The historical CI transfer clients do not support this owned restore rehearsal")
    configured = os.getenv("HIREWIZ_TEST_POSTGRES_URL")
    if not configured:
        pytest.fail("Schema27 restore requires the owned PostgreSQL fixture")
    url = make_url(configured)
    if (url.drivername, url.host, url.database, url.username, url.password, url.query) != (
        "postgresql+psycopg", "127.0.0.1", "hirewiz_admission_test", "hirewiz", "hirewiz", {},
    ) or url.port != 55435:
        pytest.fail("Schema27 restore accepts only the owned local PostgreSQL fixture")
    dump = os.getenv("HIREWIZ_TRANSFER_PG_DUMP") or shutil.which("pg_dump")
    restore = os.getenv("HIREWIZ_TRANSFER_PG_RESTORE") or shutil.which("pg_restore")
    assert dump and restore, "PG17 dump and restore clients are required"

    tag = uuid4().hex
    source_name, target_name, rebuilt_name, catalogue_name = (
        f"hw_restore27_{tag}_{suffix}" for suffix in ("source", "raw", "rebuilt", "catalogue")
    )
    connect_args = {"connect_timeout": 5, "sslmode": "disable",
                    "options": "-cstatement_timeout=15000 -clock_timeout=5000"}
    admin = create_engine(url.set(database="postgres"), poolclass=NullPool,
                          isolation_level="AUTOCOMMIT", connect_args=connect_args)
    engines, created = [], []
    try:
        with admin.connect() as db:
            assert int(db.scalar(text("SHOW server_version_num"))) // 10000 == 17
            for name in (source_name, target_name, rebuilt_name, catalogue_name):
                db.exec_driver_sql(f'CREATE DATABASE "{name}"')
                created.append(name)
                engines.append(create_engine(url.set(database=name), poolclass=NullPool,
                                             connect_args=connect_args))
        source, target, rebuilt, catalogue = engines
        _migrate(source, schema27.SCHEMA)
        stamp = datetime.now(UTC) - timedelta(seconds=1)
        archive = tmp_path / "owned-schema27.dump"
        db_options = {
            "PGHOST": "127.0.0.1", "PGPORT": str(url.port), "PGUSER": "hirewiz",
            "PGPASSWORD": "hirewiz", "PGCONNECT_TIMEOUT": "5", "PGSSLMODE": "disable",
            "PGOPTIONS": connect_args["options"], "LC_ALL": "C",
        }
        for client in (dump, restore):
            version = subprocess.run([client, "--version"], env=db_options, check=True,
                                     timeout=5, capture_output=True, text=True).stdout
            assert "(PostgreSQL) 17." in version

        source_bytes, capture, reservation_id, credits = seed_application(source, stamp)

        expected = snapshot(source)
        expected_tables, expected_sequences = expected
        assert len(expected_tables["credit_purchase_quotes"]) == 2
        assert all(expected_tables[table] for table in (*RESTORE_TABLES, "evidence_items"))
        assert expected_tables["alembic_version"] == [{"version_num": schema27.SCHEMA}]
        assert expected_tables["employer_catalogue_clock"] == EMPTY_CATALOGUE_CLOCK
        assert expected_sequences["users_id_seq"] == (41, True)
        assert expected_sequences["resumes_id_seq"] == (72, False)
        record_property("owned_public_tables", len(expected_tables))
        record_property("owned_nonempty_public_tables", sum(bool(rows) for rows in expected_tables.values()))
        record_property("owned_public_sequences", len(expected_sequences))
        started = time.monotonic()
        subprocess.run([dump, "--format=custom", "--no-owner", "--no-privileges", "--no-password",
                        "--file", str(archive), "--dbname", source_name],
                       env=db_options, check=True, timeout=30, capture_output=True)
        subprocess.run([restore, "--single-transaction", "--exit-on-error", "--no-owner", "--no-privileges",
                        "--no-password", "--dbname", target_name, str(archive)],
                       env=db_options, check=True, timeout=30, capture_output=True)
        record_property("owned_dump_restore_seconds", round(time.monotonic() - started, 3))
        record_property("owned_dump_bytes", archive.stat().st_size)
        assert snapshot(target) == expected

        assert_payment_and_reserve_replay(target, stamp, capture, reservation_id, source_bytes, credits)
        assert snapshot(target) == expected
        assert snapshot(source) == expected
        with source.connect() as original, target.connect() as restored:
            source_constraint, restored_constraint = upload_constraint(original), upload_constraint(restored)
            assert source_constraint[:-1] == restored_constraint[:-1]
            assert source_constraint[-1] == SOURCE_CONSTRAINT
            assert restored_constraint[-1] == RESTORED_CONSTRAINT
            record_property("source_upload_constraint", source_constraint[-1])
            record_property("target_raw_upload_constraint", restored_constraint[-1])
            schema27.verify_shape(original)
            with pytest.raises(schema27.before.reset21.previous.Schema20Denied,
                               match="^schema20_upload_format_constraint_mismatch$"):
                schema27.verify_shape(restored)

        # The two reviewed migration-owned rows already match the archive.
        data_archive = tmp_path / "owned-schema27-data.dump"
        data_toc = tmp_path / "owned-schema27-data.list"
        started = time.monotonic()
        _migrate(rebuilt, schema27.SCHEMA)
        with rebuilt.connect() as restored:
            schema27.verify_shape(restored)
        clean_target = snapshot(rebuilt)
        assert {table: clean_target[0][table] for table in MIGRATION_SEEDS} == {
            table: expected_tables[table] for table in MIGRATION_SEEDS
        }
        create_data_archive(dump, restore, db_options, source_name, data_archive, data_toc)
        unfiltered = restore_data(restore, db_options, rebuilt_name, data_archive)
        assert unfiltered.returncode != 0 and "alembic_version_pkc" in unfiltered.stderr
        assert snapshot(rebuilt) == clean_target
        refused = restore_data(restore, db_options, rebuilt_name, data_archive, data_toc)
        assert refused.returncode != 0 and "evidence_writer_version_required" in refused.stderr
        assert snapshot(rebuilt) == clean_target

        # Exercise the installed fence using the current writer marker on this owned client.
        writer_options = {**db_options, "PGOPTIONS": db_options["PGOPTIONS"] +
                          f" -c{evidence_fence_definition.SETTING}={evidence_fence_definition.VERSION}"}
        restored_data = restore_data(restore, writer_options, rebuilt_name, data_archive, data_toc)
        assert restored_data.returncode == 0, restored_data.stderr
        record_property("owned_rebuild_and_data_restore_seconds", round(time.monotonic() - started, 3))
        with rebuilt.begin() as restored:
            assert upload_constraint(restored) == source_constraint
            schema27.verify_shape(restored)
            for admitted_format in ("pdf", "docx", "tex", "texzip"):
                restored.execute(text("UPDATE public.resume_uploads SET source_format=:format"),
                                 {"format": admitted_format})
            restored.execute(text("UPDATE public.resume_uploads SET source_format='tex'"))
            with pytest.raises(IntegrityError) as rejected, restored.begin_nested():
                restored.execute(text("UPDATE public.resume_uploads SET source_format='zip'"))
            assert isinstance(rejected.value.orig, CheckViolation)
            assert rejected.value.orig.diag.constraint_name == "ck_resume_upload_format"
        assert snapshot(rebuilt) == expected
        assert_payment_and_reserve_replay(rebuilt, stamp, capture, reservation_id, source_bytes, credits)
        assert snapshot(rebuilt) == expected
        with rebuilt.connect() as restored:
            assert restored.execute(text(
                "SELECT nextval('public.users_id_seq'),nextval('public.resumes_id_seq')"
            )).one() == (42, 72)
            restored.rollback()
        tables, sequences = snapshot(rebuilt)
        assert tables == expected_tables
        assert sequences == {**expected_sequences, "users_id_seq": (42, True), "resumes_id_seq": (72, True)}
        assert snapshot(source) == expected

        # A published catalogue exercises actual trigger side effects during COPY.
        publish_employer(source)
        published_tables, _ = snapshot(source)
        assert published_tables["employer_catalogue_clock"] == [{**EMPTY_CATALOGUE_CLOCK[0], "watermark": 1}]
        assert len(published_tables["employer_catalogue_source_revisions"]) == 1
        _migrate(catalogue, schema27.SCHEMA)
        clean_catalogue = snapshot(catalogue)
        assert clean_catalogue[0]["employer_catalogue_clock"] == EMPTY_CATALOGUE_CLOCK
        create_data_archive(dump, restore, db_options, source_name, data_archive, data_toc)
        refused = restore_data(restore, writer_options, catalogue_name, data_archive, data_toc)
        assert refused.returncode != 0
        assert 'COPY failed for table "employer_catalogue_source_revisions"' in refused.stderr
        assert 'unique constraint "employer_catalogue_source_revisions_pkey"' in refused.stderr
        assert "(source_id, valid_from)=(owned-employer, 1)" in refused.stderr
        # This refusal precedes SEQUENCE SET. Sequence changes generally survive rollback.
        assert snapshot(catalogue) == clean_catalogue
        assert snapshot(source) == (published_tables, expected_sequences)
        record_property("owned_published_catalogue_restore_refusal", "employer_catalogue_source_revisions_pkey")
    finally:
        for engine in engines:
            engine.dispose()
        try:
            if created:
                with admin.connect() as db:
                    for name in reversed(created):
                        db.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
                    assert db.scalar(text("SELECT count(*) FROM pg_database WHERE datname = ANY(:names)"),
                                     {"names": created}) == 0
        finally:
            admin.dispose()


def owned_pg_command(pg_bin, tool, *arguments, env=None, timeout=30):
    with subprocess.Popen([str(pg_bin / tool), *map(str, arguments)],
                          env={"LC_ALL": "C", **(env or {})}, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          start_new_session=True) as child:
        try:
            output, error = child.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.communicate(timeout=5)
            pytest.fail(f"Owned PostgreSQL command timed out: {tool}")
    assert child.returncode == 0, f"{tool} failed: {error}"
    return output


@contextmanager
def owned_pg_cluster(pg_bin, data, password):
    # Let the OS choose an available port. The data-directory and authenticated
    # identity are checked before application writes, including a possible bind race.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    pid_file = data / "postmaster.pid"
    assert not pid_file.exists()
    url = URL.create("postgresql+psycopg", username="hirewiz", password=password,
                     host="127.0.0.1", port=port, database="postgres")
    engine = create_engine(url, poolclass=NullPool, connect_args={
        "connect_timeout": 5, "sslmode": "disable",
        "options": "-cstatement_timeout=15000 -clock_timeout=5000",
    })
    try:
        owned_pg_command(pg_bin, "pg_ctl", "-D", data, "-l", data.parent / f"{data.name}.log",
                         "-o", f"-h 127.0.0.1 -p {port} -c unix_socket_directories= -c ssl=off",
                         "-w", "-t", "10", "start", timeout=15)
        with engine.connect() as db:
            assert db.execute(text(
                "SELECT current_setting('data_directory'), inet_server_addr()::text,"
                "inet_server_port(), current_user, current_setting('server_version_num')::int / 10000"
            )).one() == (str(data.resolve()), "127.0.0.1/32", port, "hirewiz", 17)
        yield engine, port
    finally:
        engine.dispose()
        if pid_file.exists():
            owned_pg_command(pg_bin, "pg_ctl", "-D", data, "-m", "immediate",
                             "-w", "-t", "10", "stop", timeout=15)
        assert not pid_file.exists()


def physical_catalogue(db):
    """Include exact catalog definitions and effective trigger states in the comparison."""
    schema27.verify_shape(db)
    return (
        db.execute(text(
            "SELECT c.oid,c.relname,c.relkind,c.relowner,c.relacl FROM pg_class c "
            "WHERE c.relnamespace='public'::regnamespace ORDER BY c.oid"
        )).all(),
        db.execute(text(
            "SELECT conname,convalidated,pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE connamespace='public'::regnamespace ORDER BY oid"
        )).all(),
        db.execute(text(
            "SELECT tgenabled,pg_get_triggerdef(oid) FROM pg_trigger "
            "WHERE tgrelid IN (SELECT oid FROM pg_class WHERE relnamespace='public'::regnamespace) ORDER BY oid"
        )).all(),
        db.execute(text(
            "SELECT pg_get_functiondef(oid) FROM pg_proc WHERE pronamespace='public'::regnamespace ORDER BY oid"
        )).all(),
    )


def logical_catalogue(db):
    """Compare identities, definitions and ACLs whose OIDs change after import."""
    queries = (
        "SELECT nspname,pg_get_userbyid(nspowner),nspacl FROM pg_namespace WHERE nspname='public'",
        "SELECT relname,relkind,pg_get_userbyid(relowner),relacl FROM pg_class "
        "WHERE relnamespace='public'::regnamespace ORDER BY relname",
        "SELECT conrelid::regclass::text,conname,convalidated,pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE connamespace='public'::regnamespace ORDER BY 1,2",
        "SELECT tgrelid::regclass::text,tgname,tgenabled,pg_get_triggerdef(oid) FROM pg_trigger "
        "WHERE NOT tgisinternal AND tgrelid IN (SELECT oid FROM pg_class "
        "WHERE relnamespace='public'::regnamespace) ORDER BY 1,2",
        "SELECT tgrelid::regclass::text,tgconstrrelid::regclass::text,tgenabled,"
        "pg_get_constraintdef(tgconstraint),tgfoid::regproc::text,tgtype,tgdeferrable,tginitdeferred "
        "FROM pg_trigger WHERE tgisinternal AND tgrelid IN (SELECT oid FROM pg_class "
        "WHERE relnamespace='public'::regnamespace) ORDER BY 1,2,3,4,5,6,7,8",
        "SELECT oid::regprocedure::text,pg_get_userbyid(proowner),proacl,pg_get_functiondef(oid) "
        "FROM pg_proc WHERE pronamespace='public'::regnamespace ORDER BY 1",
    )
    return tuple(db.exec_driver_sql(query).all() for query in queries)


def init_owned_pg_cluster(pg_bin, data, password):
    password_file = data.parent / "password"
    with os.fdopen(os.open(password_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        stream.write(password + "\n")
    try:
        owned_pg_command(pg_bin, "initdb", "--pgdata", data, "--username=hirewiz",
                         "--auth-local=reject", "--auth-host=scram-sha-256", "--pwfile", password_file,
                         "--no-locale", "--encoding=UTF8", "--data-checksums")
    finally:
        password_file.unlink()


def restore_logical_archive(pg_bin, archive, port, password):
    owned_pg_command(pg_bin, "pg_restore", "--host=127.0.0.1", f"--port={port}", "--username=hirewiz",
                     "--dbname=postgres", "--single-transaction", "--exit-on-error", "--no-password", archive,
                     env={"PGPASSWORD": password, "PGCONNECT_TIMEOUT": "5", "PGSSLMODE": "disable"})


def restore_canonical_archive(pg_bin, archive, toc, port, password):
    """Restore into a freshly migrated fixture without round-tripping its DDL."""
    data_sql = archive.with_suffix(".sql")
    owned_pg_command(pg_bin, "pg_restore", "--data-only", "--exit-on-error", "--use-list", toc,
                     "--file", data_sql, archive)
    before_copy = [
        "SET LOCAL statement_timeout = '15s'", "SET LOCAL lock_timeout = '5s'",
        f"SET LOCAL {evidence_fence_definition.SETTING} = '{evidence_fence_definition.VERSION}'",
        "DELETE FROM public.employer_catalogue_clock WHERE id=1 AND watermark=0 AND inventory_state='sources' "
        "AND source_cursor IS NULL AND posting_cursor IS NULL AND prune_cursor IS NULL",
    ]
    before_copy.extend(f"ALTER TABLE public.{table} DISABLE TRIGGER {trigger}"
                       for table, trigger in CATALOGUE_COPY_TRIGGERS)
    before_copy.append("""DO $restore_probe$
      DECLARE rejected_constraint text;
      BEGIN
        BEGIN
          INSERT INTO public.resumes(id,user_id) VALUES (-1,-1);
          RAISE EXCEPTION 'Foreign-key check was inactive during import';
        EXCEPTION WHEN foreign_key_violation THEN
          GET STACKED DIAGNOSTICS rejected_constraint = CONSTRAINT_NAME;
          IF rejected_constraint <> 'resumes_user_id_fkey' THEN RAISE; END IF;
        END;
      END $restore_probe$""")
    after_copy = ["SET CONSTRAINTS ALL IMMEDIATE"]
    after_copy.extend(f"ALTER TABLE public.{table} ENABLE TRIGGER {trigger}"
                      for table, trigger in CATALOGUE_COPY_TRIGGERS)
    owned_pg_command(pg_bin, "psql", "--host=127.0.0.1", f"--port={port}", "--username=hirewiz",
                     "--dbname=postgres", "--no-psqlrc", "--no-password", "--single-transaction",
                     "--set=ON_ERROR_STOP=1", "--command", "; ".join(before_copy), "--file", data_sql,
                     "--command", "; ".join(after_copy),
                     env={"PGPASSWORD": password, "PGCONNECT_TIMEOUT": "5", "PGSSLMODE": "disable"})


def repair_restored_resume_constraint(db, source_constraint):
    restored = upload_constraint(db)
    assert source_constraint[-1] == SOURCE_CONSTRAINT
    assert restored[:-1] == source_constraint[:-1]
    if restored == source_constraint:
        return
    assert restored[-1] == RESTORED_CONSTRAINT
    with pytest.raises(schema27.before.reset21.previous.Schema20Denied,
                       match="^schema20_upload_format_constraint_mismatch$"):
        schema27.verify_shape(db)
    # The precise predicate in migration 20261023_0020 avoids a second
    # round trip through pg_get_constraintdef's non-canonical cast expression.
    db.exec_driver_sql("ALTER TABLE public.resume_uploads DROP CONSTRAINT ck_resume_upload_format, "
                       "ADD CONSTRAINT ck_resume_upload_format "
                       "CHECK (source_format IN ('pdf','docx','tex','texzip'))")
    assert upload_constraint(db) == source_constraint


def test_schema27_physical_recovery_preserves_published_catalogue_and_obligations(record_property, monkeypatch):
    _rehearse_published_catalogue_recovery(record_property, monkeypatch, method="physical")


def test_schema27_logical_archive_preserves_obligations_but_fails_strict_shape(record_property, monkeypatch):
    _rehearse_published_catalogue_recovery(record_property, monkeypatch, method="logical")


def test_schema27_canonical_restore_preserves_published_catalogue_and_obligations(record_property, monkeypatch):
    _rehearse_published_catalogue_recovery(record_property, monkeypatch, method="canonical")


def _rehearse_published_catalogue_recovery(record_property, monkeypatch, *, method):
    physical = method == "physical"
    canonical = method == "canonical"
    configured = os.getenv(f"HIREWIZ_SCHEMA27_{method.upper()}_PG_BIN")
    if not configured:
        pytest.skip("Requires an explicitly selected local PG17 binary directory")
    if os.getenv("GITHUB_ACTIONS") == "true":
        pytest.fail("The recovery rehearsal owns local PostgreSQL clusters only")
    pg_bin = Path(configured)
    assert pg_bin.is_absolute()
    backup_tools = ("pg_basebackup", "pg_verifybackup") if physical else ("pg_dump", "pg_restore", "psql")
    for tool in ("initdb", "pg_ctl", *backup_tools):
        assert "(PostgreSQL) 17." in owned_pg_command(pg_bin, tool, "--version", timeout=5)

    with tempfile.TemporaryDirectory(prefix=f"hirewiz-{method}-") as owned:
        root = Path(owned).resolve()
        password = secrets.token_hex(24)
        source_data, restored_data = root / "source", root / "restored"
        init_owned_pg_cluster(pg_bin, source_data, password)
        archive = root / "owned-catalogue.dump"
        stamp = datetime.now(UTC) - timedelta(seconds=1)
        with owned_pg_cluster(pg_bin, source_data, password) as (source, source_port):
            _migrate(source, schema27.SCHEMA)
            with Session(source) as db:
                db.add(User(id=1, email="restore27@example.test", ai_credits=17, job_service_credits=13))
                db.commit()
            publish_employer(source)
            application_recovery = seed_application_recovery(source, stamp)
            # The later synthetic payment and model rows must not fund the earlier accepted application.
            source_bytes, capture, reservation_id, credits = seed_application(source, stamp)
            with source.begin() as db:
                db.execute(text("UPDATE public.employer_sources SET enabled=false WHERE id='owned-employer'"))
                if not physical:
                    db.exec_driver_sql("CREATE ROLE owned_archive_reader NOLOGIN")
                    db.exec_driver_sql("GRANT SELECT ON public.employer_catalogue_source_revisions TO owned_archive_reader")
            tables, sequences = expected = snapshot(source)
            assert {(row["valid_from"], row["valid_to"], row["enabled"])
                    for row in tables["employer_catalogue_source_revisions"]} == {(1, 3, True), (3, None, False)}
            assert tables["employer_catalogue_clock"][0]["watermark"] == 3
            assert len(tables["employer_catalogue_posting_revisions"]) == 1
            with source.connect() as db:
                schema27.verify_shape(db)
                inspect_catalogue = physical_catalogue if physical else logical_catalogue
                expected_catalogue = inspect_catalogue(db)
                source_constraint = upload_constraint(db)
                system_identifier = db.scalar(text("SELECT system_identifier FROM pg_control_system()"))
                assert db.scalar(text("SHOW data_checksums")) == "on"
                assert db.scalar(text("SHOW session_replication_role")) == "origin"
            started = time.monotonic()
            toc = archive.with_suffix(".list")
            if canonical:
                create_data_archive(str(pg_bin / "pg_dump"), str(pg_bin / "pg_restore"),
                                    {"PGHOST": "127.0.0.1", "PGPORT": str(source_port), "PGUSER": "hirewiz",
                                     "PGPASSWORD": password, "PGCONNECT_TIMEOUT": "5", "PGSSLMODE": "disable"},
                                    "postgres", archive, toc, excluded_seeds={"alembic_version"})
            elif not physical:
                owned_pg_command(pg_bin, "pg_dump", "--host=127.0.0.1", f"--port={source_port}",
                                 "--username=hirewiz", "--dbname=postgres", "--format=custom", "--file", archive,
                                 "--no-password", env={"PGPASSWORD": password, "PGCONNECT_TIMEOUT": "5",
                                                       "PGSSLMODE": "disable"})
            else:
                owned_pg_command(pg_bin, "pg_basebackup", "--host=127.0.0.1", f"--port={source_port}",
                             "--username=hirewiz", "--pgdata", restored_data, "--format=plain",
                             "--wal-method=stream", "--checkpoint=fast", "--manifest-checksums=SHA256",
                             "--no-password", env={"PGPASSWORD": password, "PGCONNECT_TIMEOUT": "5",
                                                   "PGSSLMODE": "disable"})
                owned_pg_command(pg_bin, "pg_verifybackup", "--exit-on-error", restored_data)
            backup_measurement = "owned_physical_backup_verify_seconds" if physical else f"owned_{method}_backup_seconds"
            record_property(backup_measurement, round(time.monotonic() - started, 3))
            assert snapshot(source) == expected

        started = time.monotonic()
        if not physical:
            init_owned_pg_cluster(pg_bin, restored_data, password)
        with owned_pg_cluster(pg_bin, restored_data, password) as (restored, restored_port):
            if not physical:
                with restored.begin() as db:
                    # Cluster-global roles are external to an ordinary database archive.
                    db.exec_driver_sql("CREATE ROLE owned_restore_oid_gap NOLOGIN")
                    db.exec_driver_sql("CREATE ROLE owned_archive_reader NOLOGIN")
            if canonical:
                _migrate(restored, schema27.SCHEMA)
                before_copy, _ = snapshot(restored)
                assert {table for table, rows in before_copy.items() if rows} == MIGRATION_SEEDS
                assert before_copy["alembic_version"] == tables["alembic_version"]
                assert before_copy["employer_catalogue_clock"] == EMPTY_CATALOGUE_CLOCK
                with restored.begin() as db:
                    schema27.verify_shape(db)
                    db.exec_driver_sql("GRANT SELECT ON public.employer_catalogue_source_revisions TO owned_archive_reader")
                    assert inspect_catalogue(db) == expected_catalogue
                restore_canonical_archive(pg_bin, archive, toc, restored_port, password)
                assert snapshot(restored) == expected
                with restored.connect() as db:
                    schema27.verify_shape(db)
                    assert upload_constraint(db) == source_constraint
                record_property("owned_canonical_strict_shape_accepted", True)
                record_property("owned_canonical_foreign_key_active_during_archive_import", True)
                record_property("owned_canonical_detached_user_triggers", len(CATALOGUE_COPY_TRIGGERS))
            elif not physical:
                restore_logical_archive(pg_bin, archive, restored_port, password)
                assert snapshot(restored) == expected
                with restored.begin() as db:
                    repair_restored_resume_constraint(db, source_constraint)
                    obtained_catalogue = inspect_catalogue(db)
                    before_checks = {(table, name): (validated, expression)
                                     for table, name, validated, expression in expected_catalogue[2]}
                    after_checks = {(table, name): (validated, expression)
                                    for table, name, validated, expression in obtained_catalogue[2]}
                    assert before_checks.keys() == after_checks.keys()
                    drift = [key for key in before_checks if before_checks[key] != after_checks[key]]
                    assert {("password_reset_tokens", "ck_password_reset_purpose"),
                            ("password_reset_tokens", "ck_password_reset_status")} <= set(drift)
                    for key in drift:
                        before, after = before_checks[key], after_checks[key]
                        assert before[0] is after[0] is True
                        assert before[1].startswith("CHECK (") and after[1].startswith("CHECK (")
                    record_property("owned_logical_changed_check_definitions", len(drift))
                    with pytest.raises(schema27.before.Schema26Denied,
                                       match="^schema26_reset_purpose_or_status_check_mismatch$"):
                        schema27.verify_shape(db)
                    record_property("owned_logical_strict_shape_accepted", False)
            with restored.connect() as db:
                restored_identifier = db.scalar(text("SELECT system_identifier FROM pg_control_system()"))
                assert (restored_identifier == system_identifier) is physical
                assert db.scalar(text("SELECT pg_is_in_recovery()")) is False
                assert db.scalar(text("SHOW session_replication_role")) == "origin"
                assert db.scalar(text("SHOW data_checksums")) == "on"
                actual_catalogue = inspect_catalogue(db)
                if method == "logical":
                    # Keep the strict failure above. The comparisons below
                    # establish independent ACL, trigger and routine fidelity.
                    assert actual_catalogue[:2] == expected_catalogue[:2]
                    assert actual_catalogue[3:] == expected_catalogue[3:]
                else:
                    assert actual_catalogue == expected_catalogue
                if not physical:
                    assert db.scalar(text("SELECT has_table_privilege('owned_archive_reader',"
                                          "'public.employer_catalogue_source_revisions','SELECT')")) is True
                    assert db.scalar(text("SELECT has_table_privilege('owned_archive_reader',"
                                          "'public.employer_catalogue_source_revisions','UPDATE')")) is False
            assert snapshot(restored) == expected
            assert_payment_and_reserve_replay(restored, stamp, capture, reservation_id, source_bytes, credits)
            assert_application_recovery(restored, application_recovery, monkeypatch)
            record_property("owned_unknown_application_redeliveries", 2)
            record_property("owned_unknown_application_employer_calls", 0)
            assert snapshot(restored) == expected
            with restored.connect() as db:
                with pytest.raises(IntegrityError) as invalid_format, db.begin_nested():
                    db.execute(text("UPDATE public.resume_uploads SET source_format='zip'"))
                assert isinstance(invalid_format.value.orig, CheckViolation)
                assert invalid_format.value.orig.diag.constraint_name == "ck_resume_upload_format"
                with pytest.raises(DBAPIError) as denied, db.begin_nested():
                    db.execute(text("UPDATE public.evidence_items SET title='Unversioned update'"))
                assert isinstance(denied.value.orig, InsufficientPrivilege)
                assert denied.value.orig.diag.message_primary == "evidence_writer_version_required"
                with pytest.raises(IntegrityError) as rejected, db.begin_nested():
                    db.execute(text("UPDATE public.resumes SET user_id=999 WHERE id=1"))
                assert isinstance(rejected.value.orig, ForeignKeyViolation)
                db.execute(text("UPDATE public.employer_sources SET enabled=true WHERE id='owned-employer'"))
                assert db.execute(text(
                    "SELECT valid_from,valid_to,enabled FROM public.employer_catalogue_source_revisions "
                    "WHERE source_id='owned-employer' ORDER BY valid_from"
                )).all() == [(1, 3, True), (3, 4, False), (4, None, True)]
                db.rollback()
            assert snapshot(restored) == expected
            record_property(f"owned_{method}_start_and_verify_seconds", round(time.monotonic() - started, 3))
        record_property(f"owned_{method}_public_tables", len(tables))
        record_property(f"owned_{method}_public_sequences", len(sequences))
