"""Exact R02 phase-aware current app source; never Native80 or rollout authority.

The original Source27 family and its 0aa application/receipts stay historical.
This family qualifies only the reviewed 316-file R02 application and keeps the
same schema, HTTP, runtime, financial and native document source guards.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts import schema27_app_release_contract as base

CONTRACT = "hirewiz_schema27_app_r02_v1"
PRIOR_RELEASE = "45b3e808c88d78c1d7d182cd4af4482df25c13f0"
PRIOR_APP_SHA256 = "f2775c3b9f8d8df1f4f3d97abbe190cf5d2eb46b87beeb942b5252c3840d8670"
PRIOR_APP_FILES = 290
CANDIDATE_APP_SHA256 = "8fc5d7a70aa028bd0d93d73be50b5554226b9580a022658bcb971af4f298ffb1"
CANDIDATE_APP_FILES = 316
COMPATIBILITY_STEP = "Qualify R02 schema27 app compatibility"
PROTOCOL_STEP = "Qualify R02 schema27 app native protocol"
COMPATIBILITY_COMMAND = ".venv/bin/python -I -B scripts/qualify_schema27.py --phase compatibility --application-family hirewiz_schema27_app_r02_v1"
PROTOCOL_COMMAND = ".venv/bin/python -I -B scripts/qualify_schema27.py --phase protocol --application-family hirewiz_schema27_app_r02_v1"
RETAINED_STEP = "Qualify schema26 app v3 compatibility"
RETAINED_COMMAND = base.RETAINED_COMMAND
RETAINED_PROTOCOL_STEP = base.RETAINED_PROTOCOL_STEP
RETAINED_PROTOCOL_COMMAND = base.RETAINED_PROTOCOL_COMMAND
NATIVE_ENV = base.NATIVE_ENV
PG_URL = base.PG_URL
HISTORICAL_CURRENT_IGNORES = base.HISTORICAL_CURRENT_IGNORES
HTTP_PATHS = base.HTTP_PATHS
HTTP_SCHEMAS = base.HTTP_SCHEMAS
schema27 = base.schema27
v3 = base.v3
need = base.need
read = base.read
digest = base.digest
app_digest = base.app_digest
current_app = base.current_app
runtime_contract = base.runtime_contract
additive_http = base.additive_http
preserved_sources = base.preserved_sources
patched_document_profile = base.patched_document_profile
R02_PATHS = ("backend/scripts/schema27_app_r02_release_contract.py",
             "backend/tests/test_schema27_app_r02_release_contract.py",
             "infra/gcp/schema27_patched_app_successor.py",
             "backend/tests/test_schema27_patched_app_successor.py",
             "docs/delivery/SCHEMA27_PATCHED_APP_SUCCESSOR.md")
CREDIT_PHASE_SEMANTICS = {
    "legacy_default": "widening", "widening_minimum_successes": 200,
    "initial_phase": "initial_canary", "initial_minimum_successes": 1,
    "initial_server_audience": "canary", "shadow_days": 7,
    "public_requires_widening": True, "existing_consent_and_five_percent_admission": True,
    "runtime_config_and_prices_unchanged": True,
}


def verify_ci(root):
    import yaml

    workflow = yaml.safe_load(read(root, ".github/workflows/ci.yml"))
    jobs = workflow["jobs"]
    need(set(jobs) == {"backend", "frontend", "security-and-container", "browser-companion", "cold-browser-journey"}
         and all(job.get("runs-on") == "ubuntu-24.04" for job in jobs.values()), "fixed_ci_jobs")
    for job in jobs.values():
        checkouts = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout@")]
        need(len(checkouts) == 1 and checkouts[0].get("with", {}).get("ref")
             == "${{ github.event.pull_request.head.sha || github.sha }}", "checkout_identity")
    steps = jobs["backend"]["steps"]
    from infra.gcp import document_source_candidate_v5

    patched = patched_document_profile(root)
    if patched is not None or (root / document_source_candidate_v5.POLICY_PATH).is_file():
        checks = [step for step in steps if step.get("name") == "Build and verify genuine native document engines"]
        command = checks[0].get("run", "") if len(checks) == 1 else ""
        if patched is not None:
            need('--source-revision --patched-engine --output "$document_test_report_path"' in command
                 and "--source-v5" not in command, "patched_document_ci_required")
        else:
            need('--source-revision --source-v5 --output "$document_test_report_path"' in command
                 and "--patched-engine" not in command, "forward_document_ci_required")
    for name, command in ((COMPATIBILITY_STEP, COMPATIBILITY_COMMAND), (PROTOCOL_STEP, PROTOCOL_COMMAND),
                          (RETAINED_STEP, RETAINED_COMMAND), (RETAINED_PROTOCOL_STEP, RETAINED_PROTOCOL_COMMAND)):
        need([s for s in steps if s.get("name") == name] == [{"name": name, "working-directory": "backend",
             "run": command, "env": NATIVE_ENV}], "mandatory_family_ci_step")
    for name, command, env in ((v3.RETAINED_STEP, v3.RETAINED_COMMAND, NATIVE_ENV),
        ("Qualify retained actual-source schema25 app v1", ".venv/bin/python scripts/qualify_schema25_retained_v1.py",
         {"HIREWIZ_TEST_POSTGRES_URL": PG_URL, "HIREWIZ_APP_COMPATIBILITY_REQUIRED": "true"})):
        need([s for s in steps if s.get("name") == name] == [{"name": name, "working-directory": "backend",
             "run": command, "env": env}], "retained_ci_required")
    full = [s for s in steps if s.get("name") == "Run backend tests"]
    exact = ".venv/bin/pytest -q -p scripts.ci_postgres_lock_diagnostics " + " ".join(
        "--ignore=" + name for name in HISTORICAL_CURRENT_IGNORES) + "\n"
    need(len(full) == 1 and [line.strip() for line in full[0]["run"].splitlines() if "--ignore=" in line]
         == [exact.rstrip("\n")] and all(full[0]["env"].get(k) == v for k, v in NATIVE_ENV.items()),
         "current_and_retained_suites_required")
    backend_checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout@"))
    need(backend_checkout.get("with", {}).get("fetch-depth") == 0, "retained_history_required")


def verify_native_ci(observer, ci_run_id):
    """Actual official CI metadata, in addition to unchanged source/Build checks."""
    result = observer._command("gh", "run", "view", str(ci_run_id), "--repo", "Harshil4442/ai-resume-copilot", "--json", "jobs")
    value = json.loads(result)
    jobs = [j for j in value.get("jobs", []) if j.get("name") == "backend"]
    required = {COMPATIBILITY_STEP, PROTOCOL_STEP, RETAINED_STEP, RETAINED_PROTOCOL_STEP, v3.RETAINED_STEP}
    steps = [s for job in jobs for s in job.get("steps", []) if s.get("name") in required]
    need(type(ci_run_id) is int and ci_run_id > 0 and len(jobs) == 1 and len(steps) == len(required)
         and {s.get("name") for s in steps} == required
         and all(s.get("status") == "completed" and s.get("conclusion") == "success" for s in steps),
         "family_ci_not_successful")


def source_fingerprint(root: Path, prior_files):
    """Current app/operator identity only, with no retained producer authority."""
    need(len(prior_files) == PRIOR_APP_FILES and app_digest(prior_files) == PRIOR_APP_SHA256,
         "qualified_prior_app_changed")
    schema27.verify_source(root)
    current = current_app(root)
    need(len(current) == CANDIDATE_APP_FILES and app_digest(current) == CANDIDATE_APP_SHA256,
         "current_app_r02_unqualified")
    runtime_contract(root)
    verify_ci(root)
    additive_http(root)
    entries = preserved_sources(root)
    entries.update({name: hashlib.sha256(read(root, name)).hexdigest() for name in R02_PATHS})
    entries.update(contract=CONTRACT, prior_release=PRIOR_RELEASE, prior_app=PRIOR_APP_SHA256,
                   current_app=CANDIDATE_APP_SHA256, current_files=CANDIDATE_APP_FILES,
                   http_delta=digest([HTTP_PATHS, HTTP_SCHEMAS]), schema27=schema27.CONTRACT,
                   native_runner_eligible=False, financial_invariants=digest(v3.v1.INVARIANTS),
                   credit_phase_semantics=digest(CREDIT_PHASE_SEMANTICS))
    return digest(entries)
