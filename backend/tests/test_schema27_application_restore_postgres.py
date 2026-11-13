"""Owned schema27 SQL restore evidence using an explicitly selected local PG17.

Set HIREWIZ_SCHEMA27_LOCAL_RESTORE=true with the disposable fixture on port
55435. The historical CI transfer clients intentionally allow only schema9.
"""

import hashlib
import json
import os
import shutil
import subprocess
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from backend.app.billing.service import process_razorpay_webhook
from backend.app.domains.career import evidence_fence_definition, evidence_write_fence
from backend.app.domains.credits import service
from backend.app.domains.credits.models import CreditExecutionAttempt, CreditExecutionHold
from backend.app.domains.dispatch.models import DispatchOutbox
from backend.app.domains.employer.models import EmployerSource
from backend.app.domains.resume_uploads.models import ResumeUpload
from backend.app.models import EvidenceItem, ModelCostLiability, PaymentOrder, Resume, User
from psycopg.errors import CheckViolation
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from test_employer_admissions_postgres import _migrate
from test_razorpay_billing import _capture_payload
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


def create_data_archive(dump, restore, db_options, source_name, archive, toc):
    subprocess.run([dump, "--data-only", "--format=custom", "--no-password",
                    "--file", str(archive), "--dbname", source_name],
                   env=db_options, check=True, timeout=30, capture_output=True)
    archive_entries = subprocess.run([restore, "--list", str(archive)], env=db_options,
                                     check=True, timeout=5, capture_output=True, text=True).stdout
    excluded, retained = set(), []
    for line in archive_entries.splitlines():
        fields = line.split()
        if len(fields) >= 7 and fields[3:6] == ["TABLE", "DATA", "public"] and fields[6] in MIGRATION_SEEDS:
            assert fields[6] not in excluded and fields[7:] == ["hirewiz"]
            excluded.add(fields[6])
            continue
        retained.append(line)
    assert excluded == MIGRATION_SEEDS
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

        source_bytes = b"\\documentclass{article}\n\\begin{document}Synthetic Candidate\\end{document}\n"
        with Session(source) as db:
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

        assert_payment_and_reserve_replay(target, stamp, capture, reservation_id, source_bytes, view.credits)
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
        assert_payment_and_reserve_replay(rebuilt, stamp, capture, reservation_id, source_bytes, view.credits)
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
        with Session(source) as db:
            db.add(EmployerSource(id="owned-employer", employer="Synthetic Employer", platform="greenhouse",
                                  board_token="synthetic", careers_url="https://example.test/careers",
                                  verification_url="https://example.test/careers", verification_note="Synthetic"))
            db.commit()
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
