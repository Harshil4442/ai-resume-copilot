"""Finite reviewed ab2→candidate qualification; never migration or rollout proof.

V1 bytes and rules remain in their original module. This new contract binds the
actual reviewed local app bytes; candidate Git/Build/CI/image are proved later.
"""
from __future__ import annotations

import ast
import hashlib
import json
import subprocess

from scripts import schema25_app_release_contract as v1

CONTRACT = "hirewiz_schema25_app_rolling_v2"
PRIOR_RELEASE = "ab2deadd1b1cecbe9cb10c73806dbcde851bf5e2"
PRIOR_APP_SHA256 = "5d09318281793a9d9f46827ca19194164579f1c45e78b36d2d48b4655b326b89"
CANDIDATE_APP_SHA256 = "3711eeae0945045ef5d137dfff3f3ea6771a700a70dd36877611979fc3035f7f"
COMPATIBILITY_STEP = "Qualify schema25 app v2 compatibility"
PROTOCOL_STEP = "Qualify schema25 app v2 native protocol"
COMPATIBILITY_COMMAND = ".venv/bin/python -I -B -m pytest --noconftest -q tests/test_schema25_app_compatibility_v2_postgres.py tests/test_schema25_app_rolling_v2.py tests/test_schema25_app_alias_retirement.py"
PROTOCOL_COMMAND = ".venv/bin/python -I -B -m pytest --noconftest -q tests/test_schema25_app_protocol_v2.py"
ADDITIONS = {"DimensionScore": ("evidence_ids", "evidence_quote"), "JobMatchResponse": (
    "skill_analysis", "unassessed_dimensions", "evidence_links", "missing_evidence", "gap_semantics", "score_basis")}
ADDITION_NODES = {"evidence_ids": "evidence_ids: list[str] = Field(default_factory=list)",
    "evidence_quote": "evidence_quote: str | None = None", "skill_analysis": "skill_analysis: list[dict] = Field(default_factory=list)",
    "unassessed_dimensions": "unassessed_dimensions: list[dict] = Field(default_factory=list)",
    "evidence_links": "evidence_links: dict = Field(default_factory=dict)",
    "missing_evidence": "missing_evidence: list[str] = Field(default_factory=list)",
    "gap_semantics": "gap_semantics: str | None = None", "score_basis": "score_basis: str | None = None"}
CHANGED_PROTOCOL = frozenset({"backend/app/schemas.py", "backend/app/security.py",
    "backend/app/domains/candidate_accounts/production.py", "backend/app/domains/candidate_ingress/production.py"})
TRANSPORT_PATHS = (
    "backend/app/domains/candidate_accounts/production.py", "backend/app/domains/candidate_ingress/production.py",
    "backend/app/domains/recovery/gcp_rpc.py", "backend/app/domains/recovery/native_identity_observation.py",
    "backend/app/domains/recovery/native_metadata_admission.py", "backend/app/domains/recovery/native_diagnostics.py",
    "backend/app/domains/recovery/gcp_partitioned_publication.py", "backend/app/rate_limiter.py",
    "backend/app/security.py", "backend/app/routers/native_metadata_http.py", "backend/app/routers/auth.py",
    "backend/app/routers/candidate_accounts.py", "backend/app/routers/candidate_ingress.py")
QUALIFICATION_PATHS = ("backend/scripts/schema25_app_release_contract_v2.py",
    "backend/tests/test_schema25_app_compatibility_v2_postgres.py", "backend/tests/test_schema25_app_protocol_v2.py",
    "backend/tests/test_schema25_app_rolling_v2.py", "backend/tests/test_schema25_app_alias_retirement.py",
    "infra/gcp/schema25_app_rolling_release.py", "infra/gcp/schema25_app_alias_retirement.py",
    ".github/workflows/ci.yml", "backend/uv.lock", "backend/tests/conftest.py",
    "backend/scripts/qualify_schema25_retained_v1.py")
PROTOCOL_SUITES = ("tests/test_firestore_identity_list.py", "tests/test_candidate_validation_identity_list.py",
    "tests/test_native_metadata_admission.py", "tests/test_native_metadata_admission_redis.py",
    "tests/test_gcp_rpc_emulator.py", "tests/test_candidate_web_lifecycle.py")
PROTOCOL_FIXTURES = ("backend/tests/test_candidate_production_factory.py", "backend/tests/test_browser_submit_native.py",
    "backend/tests/fixtures/firestore_emulator.py", "backend/tests/test_candidate_scoped_lifecycle_emulator.py",
    "backend/tests/test_gcp_pairing_emulator.py", "backend/tests/test_gcp_password_lifetime_emulator.py")
BOOTSTRAP_PATHS = ("backend/app/domains/candidate_accounts/bootstrap.py", "backend/app/domains/candidate_accounts/profile_view.py",
    "backend/app/domains/credits/availability.py", "backend/app/domains/credits/service.py", "backend/app/routers/auth.py",
    "backend/app/routers/credits.py", "backend/openapi.json", "backend/tests/test_owned_bootstrap.py",
    "backend/tests/test_profile_read_projection.py", "backend/tests/test_unified_credit_http.py",
    "frontend/lib/accountTransportServer.ts", "frontend/lib/accountTransportClient.ts", "frontend/lib/ownedBootstrapClient.ts",
    "frontend/lib/ownedBootstrapContract.ts", "frontend/lib/ownedBootstrapClient.test.tsx", "frontend/lib/accountTransportServer.test.ts",
    "frontend/lib/ownerProfile.ts", "frontend/lib/opportunityQueries.ts", "frontend/lib/unifiedCreditClient.ts",
    "frontend/components/Nav.tsx", "frontend/components/billing/LegacyBillingAccount.tsx",
    "frontend/components/billing/UnifiedBillingPanel.tsx", "frontend/app/dashboard/page.tsx", "frontend/app/profile/page.tsx",
    "frontend/app/workspace/page.tsx", "frontend/app/workspace/[id]/page.tsx", "frontend/lib/generated/api.ts")
STAGE5_FLAGS = ("CANDIDATE_ACCOUNT_LIST_IDENTITY_ENABLED", "CANDIDATE_METADATA_ADMISSION_ENABLED")


def need(value, reason):
    v1.need(value, "v2_" + reason)


def additive_schema(prior: bytes, current: bytes):
    """Exactly eight optional response members; every other AST node unchanged."""
    old, new = ast.parse(prior), ast.parse(current)
    classes = {n.name: n for n in new.body if isinstance(n, ast.ClassDef)}
    previous = {n.name: n for n in old.body if isinstance(n, ast.ClassDef)}
    for name, additions in ADDITIONS.items():
        need(name in classes and name in previous, "schema_class_changed")
        nodes = [n for n in classes[name].body if isinstance(n, ast.AnnAssign)
                 and isinstance(n.target, ast.Name) and n.target.id in additions]
        need(len(nodes) == len(additions) and {ast.unparse(n.target): ast.dump(n, include_attributes=False) for n in nodes}
             == {key: ast.dump(ast.parse(ADDITION_NODES[key]).body[0], include_attributes=False) for key in additions},
             "optional_additions_changed")
        classes[name].body = [n for n in classes[name].body if n not in nodes]
    need(ast.dump(old, include_attributes=False) == ast.dump(new, include_attributes=False), "schema_changed")


def verify_ci(root):
    import yaml
    workflow = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["backend"]["steps"]
    for name, command in ((COMPATIBILITY_STEP, COMPATIBILITY_COMMAND), (PROTOCOL_STEP, PROTOCOL_COMMAND)):
        matched = [step for step in steps if step.get("name") == name]
        expected_env = ({"HIREWIZ_APP_COMPATIBILITY_REQUIRED": "true", "HIREWIZ_AUTHORITY_EMULATOR_REQUIRED": "true",
                         "HIREWIZ_OWNED_REDIS_SERVER": "${{ steps.native-metadata-redis.outputs.binary }}"} if name == PROTOCOL_STEP else
                        {"HIREWIZ_APP_COMPATIBILITY_REQUIRED": "true",
                         "HIREWIZ_TEST_POSTGRES_URL": "postgresql+psycopg://hirewiz:hirewiz@127.0.0.1:55433/hirewiz_admission_test"})
        need(len(matched) == 1 and matched[0] == {"name": name, "working-directory": "backend",
             "run": command, "env": expected_env},
             "mandatory_ci_step_changed")
    retained = [step for step in steps if step.get("name") == "Qualify retained actual-source schema25 app v1"]
    need(retained == [{"name": "Qualify retained actual-source schema25 app v1", "working-directory": "backend",
         "env": {"HIREWIZ_TEST_POSTGRES_URL": "postgresql+psycopg://hirewiz:hirewiz@127.0.0.1:55433/hirewiz_admission_test",
                 "HIREWIZ_APP_COMPATIBILITY_REQUIRED": "true"},
         "run": ".venv/bin/python scripts/qualify_schema25_retained_v1.py"}], "retained_ci_step_changed")
    checkout = [step for step in steps if step.get("uses", "").startswith("actions/checkout@")]
    need(len(checkout) == 1 and checkout[0].get("with", {}).get("fetch-depth") == 0, "git_history_required")


def additive_http(root):
    previous = subprocess.run(["git", "show", PRIOR_RELEASE + ":backend/openapi.json"], cwd=root,
        check=True, capture_output=True, timeout=10).stdout
    old = json.loads(previous)
    current = json.loads((root / "backend/openapi.json").read_bytes())
    path = current["paths"].pop("/api/auth/bootstrap", None)
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    need(digest(path) == "92b6124681e2496c8c8b03ec5b2dae868c855e3c03adc8c17ca9d1e808349bfa", "bootstrap_get_changed")
    schemas = current["components"]["schemas"]
    for name, expected in (("FeatureView", "b8071daf5f6dfddf4e44d9b184c86f1c4bc9888588596bd113411a28753226a4"),
                           ("OwnedBootstrapView", "c3a99edc84212d23ddb394c95e9c8e7810688f3d4faa99e127fc9d8553515135")):
        need(digest(schemas.pop(name, None)) == expected, "bootstrap_response_changed")
    for name, fields in ADDITIONS.items():
        for field in fields:
            need(field in schemas[name]["properties"], "optional_response_field_missing")
            schemas[name]["properties"].pop(field)
    need(current == old, "unreviewed_http_change")


def source_fingerprint(root, prior_files):
    import sys
    sys.path.insert(0, str(root / "infra/gcp"))
    import unified_credit_schema25_release as release
    v1.schema25.verify_source(root)
    locked = subprocess.run(["git", "show", PRIOR_RELEASE + ":backend/uv.lock"], cwd=root,
        capture_output=True, check=True, timeout=10).stdout
    need(locked == (root / "backend/uv.lock").read_bytes(), "dependency_upgrade_unqualified")
    need(release.prior_app_digest(prior_files) == PRIOR_APP_SHA256, "prior_app_changed")
    current = {path: (root / path).read_bytes() for path in release.prior_app_files(root, PRIOR_RELEASE)}
    # Include added modules too; the complete reviewed app, not merely a file allowlist.
    current.update({str(path.relative_to(root)): path.read_bytes() for path in (root / "backend/app").rglob("*")
                    if path.is_file() and path.suffix in {".py", ".json"} and "__pycache__" not in path.parts})
    need(release.prior_app_digest(current) == CANDIDATE_APP_SHA256, "candidate_app_unqualified")
    for name in v1.PROTOCOL_PATHS:
        if name == "backend/app/schemas.py":
            additive_schema(prior_files[name], current[name])
        elif name not in CHANGED_PROTOCOL:
            need(current[name] == prior_files[name], "protocol_changed")
    verify_ci(root)
    additive_http(root)
    names = (*QUALIFICATION_PATHS, *TRANSPORT_PATHS, *PROTOCOL_FIXTURES, *BOOTSTRAP_PATHS,
             *("backend/" + name for name in PROTOCOL_SUITES))
    entries = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names}
    entries.update(prior_app=PRIOR_APP_SHA256, candidate_app=CANDIDATE_APP_SHA256,
        additive_manifest=hashlib.sha256(json.dumps(ADDITIONS, sort_keys=True).encode()).hexdigest(),
        financial_invariants=hashlib.sha256("\n".join(v1.INVARIANTS).encode()).hexdigest())
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()
