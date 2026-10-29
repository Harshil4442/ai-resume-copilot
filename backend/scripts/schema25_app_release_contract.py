"""Read-only live-pilot schema25 application contract; no DDL or admission writes.

Unlike the initial21→25 migration, ordinary compatible app changes may overlap
legacy pilot work. Check actual catalog/financial invariants, not a frozen hash
of mutable customer rows, and retain unknown provider obligations as liabilities.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sqlalchemy import text

from scripts import monetary_cutover_preflight as financial
from scripts import unified_credit_schema25_contract as schema25

PRIOR_RELEASE = "8cbdfaeb9bb6cf6674c18ca8d59bc123fbf73a2a"
PRIOR_APP_SHA256 = "71bb4ddf639b3b0750d1f403e21d4e9973d9c7f7ad89c94cc900373fac884d92"
CONTRACT = "hirewiz_schema25_app_rolling_v1"
COMPATIBILITY_STEP = "Qualify schema25 app compatibility"
COMPATIBILITY_COMMAND = ".venv/bin/python -I -B -m pytest --noconftest -q tests/test_schema25_app_compatibility_postgres.py"
PROTOCOL_PATHS = (
    "backend/app/schemas.py", "backend/app/domains/analysis/schemas.py",
    "backend/app/domains/employer/schemas.py", "backend/app/domains/resume_uploads/schemas.py",
    "backend/app/domains/dispatch/models.py", "backend/app/domains/dispatch/service.py",
    "backend/app/routers/worker.py", "backend/app/services/document_ingestion.py",
    "backend/app/domains/resume_uploads/scanner.py",
    "backend/app/security.py", "backend/app/domains/candidate_ingress/contracts.py",
    "backend/app/domains/candidate_ingress/crypto.py", "backend/app/domains/candidate_ingress/replay.py",
    "backend/app/domains/candidate_ingress/production.py",
    "backend/app/domains/candidate_accounts/contracts.py", "backend/app/domains/candidate_accounts/production.py",
    "backend/app/domains/candidate_accounts/access.py",
    "backend/app/domains/browser_assistance/schemas.py", "backend/app/domains/browser_assistance/native_pairing.py",
)
INVARIANTS = (
    "SELECT count(*) FROM public.users WHERE ai_credits < 0 OR job_service_credits < 0",
    "SELECT count(*) FROM public.service_credit_reservations WHERE reserved_amount < 0 OR committed_amount < 0 OR released_amount < 0 OR committed_amount+released_amount>reserved_amount OR (state='settled' AND committed_amount+released_amount<>reserved_amount)",
    "SELECT count(*) FROM public.model_cost_liabilities WHERE reserved_cost_micros < 0 OR settled_cost_micros < 0 OR (cost_state IN ('reserved','outcome_unknown','usage_unavailable') AND reserved_cost_micros <= 0)",
    "SELECT count(*) FROM public.analysis_runs WHERE status IN ('queued','running') AND generation_attempt_limit > 0",
    "SELECT count(*) FROM public.model_call_events WHERE status NOT IN ('succeeded','failed','invalid_output')",
    "SELECT count(*) FROM pg_catalog.pg_prepared_xacts WHERE database=current_database()",
)


def need(value, reason):
    if not value:
        raise schema25.Schema25Denied("schema25_app_" + reason)


def source_fingerprint(root: Path, prior_files: dict[str, bytes]) -> str:
    """Initial scope keeps API/task/native wire schemas byte-identical."""
    schema25.verify_source(root)
    entries = {}
    for name in PROTOCOL_PATHS:
        current = (root / name).read_bytes()
        need(name in prior_files and current == prior_files[name], "protocol_changed")
        entries[name] = hashlib.sha256(current).hexdigest()
    test = root / "backend/tests/test_schema25_app_compatibility_postgres.py"
    need(test.is_file(), "compatibility_rehearsal_missing")
    entries[str(test.relative_to(root))] = hashlib.sha256(test.read_bytes()).hexdigest()
    entries["financial_invariant_contract"] = hashlib.sha256("\n".join(INVARIANTS).encode()).hexdigest()
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()


def observe(connection, *, namespace_oid=2200) -> str:
    """Native caller supplies authenticated TLS/cluster/role identity separately."""
    schema25.verify_shape(connection, namespace_oid)
    # This first post25 app rollout does not adopt already activated wallets.
    # A future populated-wallet release needs its own compatibility qualification.
    schema25.verify_empty_new_tables(connection)
    for statement in INVARIANTS:
        need(connection.execute(text(statement)).scalar_one() == 0, "live_financial_invariant_failed")
    liability_rows(connection)
    # Unknown costs are not a reason to erase/refund/retry work or call them zero.
    # Their rows and positive reservations remain untouched; no amount is frozen
    # across a legitimate later callback or search completion.
    return hashlib.sha256(json.dumps({"contract": CONTRACT, "schema": schema25.SCHEMA,
        "catalog": schema25.catalog_digest(connection, namespace_oid, "public"),
        "invariants": INVARIANTS, "new_wallet_activity": "none"}, sort_keys=True).encode()).hexdigest()


def liability_rows(connection):
    rows = connection.execute(text("SELECT * FROM public.model_cost_liabilities ORDER BY id LIMIT 257")).mappings().all()
    need(len(rows) <= 256, "protected_liability_bound")
    for row in rows:
        need(row["reserved_cost_micros"] > 0 and row["cost_state"] in {
            "reserved", "outcome_unknown", "usage_unavailable", "settled", "overrun"}, "protected_liability_invalid")
        if row["cost_state"] == "settled":
            need(financial._known_settled(row), "protected_liability_settlement_invalid")
    return rows


def liability_pins(connection):
    mutable = {"cost_state", "settled_cost_micros", "input_tokens", "output_tokens",
               "usage_provenance", "settled_at"}
    result = []
    for row in liability_rows(connection):
        fields = {key: value.isoformat() if hasattr(value, "isoformat") else value
                  for key, value in row.items() if key not in mutable}
        result.append((row["id"], hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest(),
                       row["reserved_cost_micros"]))
    return tuple(result)


def verify_liability_pins(connection, expected):
    # A genuine usage callback may settle a protected row, but cannot remove
    # its identity/quote/reservation. New AI activity is outside this scope.
    need(liability_pins(connection) == expected, "protected_liability_changed")
