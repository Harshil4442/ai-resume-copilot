"""Distinct reviewed Source27 application contract; never native QA authority.

45b3 is the genuinely qualified retained26 source. Current312 application
bytes and optional digest wire are a NEW family; old v3 fingerprints, producer
receipts, private runtime proofs and qualification commands remain disjoint.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
from pathlib import Path

from scripts import notification_schema27_contract as schema27
from scripts import schema25_app_release_contract_v3 as v3

CONTRACT = "hirewiz_schema27_app_v1"
PRIOR_RELEASE = "45b3e808c88d78c1d7d182cd4af4482df25c13f0"
PRIOR_APP_SHA256 = "f2775c3b9f8d8df1f4f3d97abbe190cf5d2eb46b87beeb942b5252c3840d8670"
PRIOR_APP_FILES = 290
CANDIDATE_APP_SHA256 = '4250c17aa0b918e6034f0a33bf54e3bd8b73e0184cb950a3202faa26d938ba94'
CANDIDATE_APP_FILES = 314
COMPATIBILITY_STEP = "Qualify schema27 app compatibility"
PROTOCOL_STEP = "Qualify schema27 app native protocol"
RETAINED_STEP = "Qualify schema26 app v3 compatibility"
RETAINED_PROTOCOL_STEP = "Qualify schema26 app v3 native protocol"
COMPATIBILITY_COMMAND = ".venv/bin/python -I -B scripts/qualify_schema27.py --phase compatibility"
PROTOCOL_COMMAND = ".venv/bin/python -I -B scripts/qualify_schema27.py --phase protocol"
RETAINED_COMMAND = ".venv/bin/python -I -B scripts/qualify_schema26_retained_v3.py --phase compatibility"
RETAINED_PROTOCOL_COMMAND = ".venv/bin/python -I -B scripts/qualify_schema26_retained_v3.py --phase protocol"
DOCKERFILE_SHA256 = "e02fce326f540cb09b6e5e9390923605371da4f84b40ccbd21b999251a4b060f"
F07_FLAGS = ("NOTIFICATION_DIGESTS_ENABLED", "LIFECYCLE_EMAILS_ENABLED")
PG_URL = "postgresql+psycopg://hirewiz:hirewiz@127.0.0.1:55433/hirewiz_admission_test"
NATIVE_ENV = {"HIREWIZ_APP_COMPATIBILITY_REQUIRED": "true", "HIREWIZ_AUTHORITY_EMULATOR_REQUIRED": "true",
              "HIREWIZ_TEST_POSTGRES_URL": PG_URL,
              "HIREWIZ_OWNED_REDIS_SERVER": "${{ steps.native-metadata-redis.outputs.binary }}"}
HISTORICAL_CURRENT_IGNORES = (*v3.HISTORICAL_CURRENT_IGNORES,
    "tests/test_schema25_app_compatibility_v3_postgres.py", "tests/test_schema25_app_protocol_v3.py",
    "tests/test_schema25_app_rolling_v3.py")
NEW_HTTP_PATHS = {
    "/api/recommendations/match_strategy/saved": "84cb75fbecc4fc9ba2f7cea63a5a461cf2c382e3094998065f27bbadcb1e4d3f",
    "/api/public/notifications/unsubscribe": "c2d58bafabeb58932448485c133bd4a7613f3c134a48fa6d502d2cfda5fe2044",
    "/api/v1/employer-jobs/bootstrap": "7214b6a32145394e0bdc3401c13bae629b8dec5b3b6412a4e56555b30e8d0ba3",
    "/api/v1/notifications/preferences": "8bca55f75a9c5e9c67713554f1bea9d6fedf31cfef85fbea5066ebf7cea46cb4",
    "/api/v1/opportunities/page": "29f1a35114d7cf58e15d0816b6dcda425b4098c745bb18b64bbc15d08407f8a4",
    # Optional learning projection. Keep the retained v3 route pin immutable.
    "/api/v1/opportunities/{opportunity_id}/workspace": "6e1d8ef20c87bdf480648071f00c941cbe711d3f772af5e8f89ebb48b1501cfa",
    "/api/v1/opportunities/{opportunity_id}/workspace/source-snapshot/selected": "8b141705ba49a35d502a08dfaf674ee34093f664f29b9c507b8745a9e8064297",
    "/api/v1/opportunities/{opportunity_id}/workspace/{collection}": "ce7b8e566f8d9d169ad1e29455fe2d247eb23d9f16c10ff4cc6cbd9aca59fa1c",
}
NEW_HTTP_SCHEMAS = {
    "JobsApplicationAdmission": "8d0f05405d0d888a1ef89bca6e2eb0484b70a2ff69e1399134050957650d29e0",
    "JobsApplicationArtifact": "8adad70dc819fae25b7eb7e8cca231f158fd1803f1364011d6c1a84c0686a636",
    "JobsApplicationPosting": "65f10aad2d7ef78ca1ff37ce64e71a707ef904f6caf6e5139558bee7dd2f8520",
    "JobsApplicationPrice": "d5ce7a2fc309ab8c9533f59853293d43ea7653b0d50f30f1b6ace53b304a5f07",
    "JobsApplicationRow": "d283547aafff85c9854afc5eb43645947748bfff85a5d4cebff538f75ee3c690",
    "JobsBootstrapView": "7ef4a9c964613a30964c1745e6bf76ceda6be676c411d11ef04d0b63710c3f68",
    "PreferenceChange": "f836f891975e90293310e2b7ed501a2ad5e41d7ad80d8f46a74dcdcd5419a072",
    "PreferenceResponse": "10ad227d9d00a4a1dbcf928fed2047bfc30e9d2ca56715cb07318a3fa91c813b",
    "UnsubscribeRequest": "a87c23e73f8c868aaf837fcc755f290412fbecb636f714bd41b042badff0de6a",
    "OpportunityPageItem": "8393811d947f625ea1a973b02547823c23ce0b75016f780babe2275a4c47b95d",
    "OpportunityPageResponse": "fc5888ecca03cb83495b1d8733ed477c28de5ed83caf794e480cfe2b2328d244",
    "WorkspaceResponse": "8f408916af8d6005c2604ce6a570bddca996b2005691bdd3b2db9eef06f87318",
    "WorkspaceSourceSnapshotCreate": "9fb774cb149726bae8f55f19525bdb50b7f10b966de48cad994470db968f6f29",
    "InterviewResultMetadata": "b0b0577d8c6472de876710cf2f1998ccbc9824c661f4d81b4fe7a7f5d4ebf813",
    "WorkspacePage_InterviewResultMetadata_": "ce512358144d1940c3cb0f91018c1445831fcd662fb62fe6b0fde4b5711bfa46",
    "WorkspacePages": "e1b63ac13d466953c1c4f4f5e18d749dedeae62e3a34ff11c4889639d2bb3363",
}
HTTP_PATHS = {**v3.HTTP_PATHS, **NEW_HTTP_PATHS}
HTTP_SCHEMAS = {**v3.HTTP_SCHEMAS, **NEW_HTTP_SCHEMAS}
DIGEST_COMMANDS = ("ScopedDigestConsentChange", "ScopedDigestEmailDisable", "ScopedDigestSendBegin", "ScopedDigestSendOutcome")
DIGEST_KINDS = ("scoped_digest_consent_change", "scoped_digest_email_disable", "scoped_digest_send_begin", "scoped_digest_send_outcome")
DIGEST_NAMESPACES = {"HEADS": "v3_digest_consent_heads", "HISTORY": "v3_digest_consent_history",
                     "BEGINS": "v3_digest_send_begins", "OUTCOMES": "v3_digest_send_outcomes"}
OLD_COMMANDS = ("ScopedDenial", "ScopedPasswordReset", "ScopedBegin", "ScopedSubmitDeviceEnrollment", "ScopedSubmitBegin",
                "ScopedPairedDeviceActivation", "ScopedPairedDeviceDenial", "ScopedPairedRevocation")
F07_PATHS = (
    "backend/scripts/stage5_first_minute.py", "backend/tests/test_stage5_first_minute.py",
    "backend/tests/test_stage5_capacity.py",
    "backend/scripts/schema27_app_release_contract.py", "backend/scripts/notification_schema27_contract.py",
    "backend/scripts/notification_schema27_step.py", "backend/scripts/qualify_schema27.py",
    "backend/scripts/qualify_schema26_retained_v3.py", "infra/gcp/schema27_native_migration.py",
    "backend/alembic/versions/20261105_0027_notification_preferences.py",
    "backend/scripts/qualify_native_quality_acquisition.py", "backend/evaluation/native_acquisition_v2/qualification.py",
    "backend/tests/test_schema27_app_release_contract.py", "backend/tests/test_notification_schema27_contract.py",
    "backend/tests/test_notification_schema27_step_postgres.py", "backend/tests/test_schema27_native_migration.py",
    "backend/tests/test_digest_unsubscribe.py", "backend/tests/test_notification_digest_native.py",
    "backend/tests/test_digest_delivery_integration.py", "backend/tests/test_notification_mail_imports.py",
    "backend/tests/test_source27_current_application_profile.py", "backend/tests/test_neon_schema27_release_transport.py",
    "backend/tests/test_neon_schema27_release_postgres.py", "backend/tests/test_notification_schema_compat_postgres.py",
    "backend/tests/test_mail_budget_postgres.py", "backend/tests/test_mail_provider.py",
    "backend/tests/test_notification_preferences_projection.py", "backend/tests/test_notification_preference_admission.py",
    "backend/tests/test_notification_digest_content.py", "backend/tests/test_notification_consent_contract.py",
    "backend/tests/test_notification_digest_emulator.py", "backend/tests/test_notification_schema26_reader_compatibility.py",
    "backend/tests/test_qualify_schema27.py", "backend/tests/test_qualify_schema26_retained_v3.py",
    "backend/tests/test_schema27_app_compatibility_postgres.py",
    "backend/tests/test_interview_input_guards.py", "backend/tests/test_interview_input_postgres.py",
    "backend/tests/test_supported_evaluation_postgres.py",
    "backend/tests/test_credit_release_margin_gate.py",
    "backend/app/domains/credits/payment_expenses.py", "backend/app/domains/credits/payment_funding.py",
    "infra/gcp/payment_funding_operator.py", "backend/tests/test_payment_expenses.py",
    "backend/tests/test_payment_funding.py", "backend/tests/test_payment_funding_postgres.py",
    "backend/tests/test_payment_funding_operator.py", "backend/tests/test_unified_payment_expense_admission.py",
    "backend/app/domains/career/browsing.py", "backend/tests/test_opportunity_browsing.py",
    "backend/tests/test_opportunity_browsing_postgres.py",
    "backend/app/domains/free_core/service.py", "backend/app/domains/credits/execution.py",
    "backend/app/domains/credits/analysis.py", "backend/app/domains/credits/reconciliation.py",
    "backend/app/services/synchronous_contracts.py", "backend/app/routers/recommendations.py",
    "backend/app/domains/free_core/synchronous.py", "backend/app/domains/credits/synchronous.py",
    "backend/app/routers/v1/analysis.py", "backend/resources/courses.json",
    "backend/tests/test_free_core_funding_period.py", "backend/tests/test_free_core_funding_period_postgres.py",
    "backend/tests/test_unified_analysis_cancellation.py", "backend/tests/test_learning_resource_delivery.py",
    "backend/tests/test_native_expense_reconciliation.py",
    "backend/app/domains/credits/finance.py", "backend/app/domains/credits/runtime.py",
    "backend/app/routers/credits.py", "backend/tests/test_curated_learning_reuse.py",
    "backend/tests/test_credit_input_unsupported.py", "frontend/components/career/LearningStrategyPanel.tsx",
    "frontend/components/career/ReviewedServices.test.tsx",
    "frontend/lib/opportunityPageClient.ts", "frontend/lib/opportunityPageClient.test.ts",
    "frontend/app/workspace/page.tsx", "frontend/app/workspace/page.test.tsx",
    "frontend/lib/generated/api.ts", "frontend/app/api/backend/[...path]/route.test.ts",
    "frontend/e2e/employer-jobs.spec.ts",
    "backend/tests/fixtures/schema26_notification_reader/PROVENANCE.json",
    "backend/tests/fixtures/schema26_notification_reader/gcp_partitioned_contracts.py.snapshot",
    "frontend/components/WeeklyDigestPreference.tsx", "frontend/components/WeeklyDigestPreference.test.tsx",
    "frontend/components/NotificationUnsubscribe.tsx", "frontend/components/NotificationUnsubscribe.test.tsx",
    "frontend/lib/notificationPreferencesClient.ts", "frontend/lib/notificationPreferencesClient.test.ts",
    "frontend/lib/notificationUnsubscribeLink.ts", "frontend/lib/notificationUnsubscribeLink.test.ts",
    "frontend/app/notifications/unsubscribe/page.tsx", "frontend/app/profile/page.tsx",
    "frontend/app/api/backend/[...path]/route.ts", "frontend/app/api/backend/[...path]/notificationRoutes.test.ts",
    "frontend/instrumentation-client.ts", "frontend/components/SessionProviderWrapper.tsx", "frontend/next.config.mjs",
    "backend/app/domains/career/workspace.py", "backend/tests/test_workspace_skill_roi.py",
    "backend/tests/test_career_workspace_pagination.py",
    "frontend/app/workspace/[id]/page.tsx", "frontend/app/workspace/[id]/page.test.tsx",
    "frontend/components/career/WorkspaceInterviewHistory.tsx", "frontend/lib/useQuotedAnalysis.ts",
    "frontend/lib/workspacePages.ts",
    "backend/tests/test_employer_jobs_bootstrap.py",
)


class Source27Denied(RuntimeError):
    pass


def need(value, reason):
    if not value:
        raise Source27Denied("source27_" + reason)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def read(root, name, *, allow_empty=False):
    path = root / name
    need(path.is_file() and not path.is_symlink() and path.resolve() == path
         and (0 if allow_empty else 1) <= path.stat().st_size <= 2 * 1024**2, "source_path")
    return path.read_bytes()


def app_digest(files):
    result = hashlib.sha256()
    need(type(files) is dict and 0 < len(files) <= 2048 and all(type(n) is str and type(b) is bytes for n, b in files.items()),
         "app_inventory")
    for name, raw in sorted(files.items()):
        result.update(name.encode() + b"\0" + hashlib.sha256(raw).digest())
    return result.hexdigest()


def current_app(root):
    names = sorted(p.relative_to(root).as_posix() for p in (root / "backend/app").rglob("*")
                   if p.is_file() and p.suffix in {".py", ".json"} and "__pycache__" not in p.parts)
    need(0 < len(names) <= 2048, "app_inventory")
    # Python package markers may be empty and still belong in the source digest.
    files = {name: read(root, name, allow_empty=Path(name).name == "__init__.py")
             for name in names}
    need(sum(map(len, files.values())) <= 32 * 1024**2, "app_inventory_bytes")
    return files


def git_bytes(root, name):
    """Only the literal qualified45b3 predecessor, never caller-selected history."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_NO_LAZY_FETCH="1", GIT_NO_REPLACE_OBJECTS="1", GIT_TERMINAL_PROMPT="0",
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    result = subprocess.run(["git", "--no-replace-objects", "-c", "core.fsmonitor=false", "show", PRIOR_RELEASE + ":" + name],
                            cwd=root, check=True, capture_output=True, timeout=10, env=env)
    need(len(result.stdout) <= 2 * 1024**2, "retained_source_bound")
    return result.stdout


def additive_http(root):
    old = json.loads(git_bytes(root, "backend/openapi.json"))
    current = json.loads(read(root, "backend/openapi.json"))
    for group, pins in (("paths", HTTP_PATHS), ("schemas", HTTP_SCHEMAS)):
        before = old["paths"] if group == "paths" else old["components"]["schemas"]
        after = current["paths"] if group == "paths" else current["components"]["schemas"]
        for name, stamp in pins.items():
            need(name in after and digest(after[name]) == stamp, "reviewed_http_delta_changed")
            if name in before:
                after[name] = before[name]
            else:
                after.pop(name)
    need(current == old, "unreviewed_http_change")


def assignments(tree):
    result = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            need(node.targets[0].id not in result, "duplicate_declaration")
            result[node.targets[0].id] = node.value
    return result


def runtime_contract(root):
    """Read AST only: four closed commands/namespaces and purpose-isolated codec."""
    need(hashlib.sha256(read(root, "Dockerfile")).hexdigest() == DOCKERFILE_SHA256, "baked_off_image_source")
    native = assignments(ast.parse(read(root, "backend/app/domains/recovery/gcp_notification_digest.py")))
    need({name: ast.literal_eval(native[name]) for name in DIGEST_NAMESPACES} == DIGEST_NAMESPACES, "digest_namespaces")
    census = assignments(ast.parse(read(root, "backend/app/domains/recovery/gcp_digest_census.py")))
    need(ast.dump(census["DIGEST_COMMANDS"]) == ast.dump(ast.parse("(" + ",".join(DIGEST_COMMANDS) + ")", mode="eval").body)
         and ast.dump(census["DIGEST_NAMESPACES"]) == ast.dump(ast.parse("{HEADS,HISTORY,BEGINS,OUTCOMES}", mode="eval").body),
         "digest_census_scope")
    tree = ast.parse(read(root, "backend/app/domains/recovery/gcp_partitioned_contracts.py"))
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    for name, kind in zip(DIGEST_COMMANDS, DIGEST_KINDS, strict=True):
        fields = {n.target.id: n for n in classes[name].body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
        value = fields["kind"].value
        need(value is not None, "digest_command_kind")
        assert value is not None
        need(ast.literal_eval(value) == kind, "digest_command_kind")
    command = next(node.annotation for node in classes["ScopedJournalIntent"].body
                   if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "command")
    need(isinstance(command, ast.Subscript) and isinstance(command.slice, ast.Tuple)
         and bool(command.slice.elts), "closed_command_union")
    assert isinstance(command, ast.Subscript) and isinstance(command.slice, ast.Tuple)
    union = command.slice.elts[0]
    def flatten(node):
        return flatten(node.left) + flatten(node.right) if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr) else [node.id]
    need(tuple(flatten(union)) == (*OLD_COMMANDS, *DIGEST_COMMANDS), "closed_command_union")
    codec = assignments(ast.parse(read(root, "backend/app/domains/notification_consent/unsubscribe.py")))
    need(ast.literal_eval(codec["PURPOSE"]) == b"hirewiz:weekly-digest-disable:v1"
         and ast.literal_eval(codec["MAX_TOKEN_BYTES"]) == 4096, "unsubscribe_purpose")
    neon = ast.parse(read(root, "backend/scripts/neon_direct_identity.py"))
    need(ast.literal_eval(assignments(neon)["SCHEMA27_RELEASE_SCHEMA"]) == schema27.SCHEMA, "neon_schema27")
    collector = next(n for n in neon.body if isinstance(n, ast.FunctionDef) and n.name == "collect_schema27_release_target")
    returned = [n.value for n in collector.body if isinstance(n, ast.Return)]
    need(len(returned) == 1 and isinstance(returned[0], ast.Call)
         and isinstance(returned[0].func, ast.Name) and returned[0].func.id == "_collect_fresh_release_target"
         and [(k.arg, ast.literal_eval(k.value)) for k in returned[0].keywords if k.arg == "purpose"]
         == [("purpose", "schema27_fresh_release_capture")], "neon_distinct_purpose")


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


def preserved_sources(root):
    """Retain old financial/document/lock/companion source closure as-is."""
    import document_source_candidate as document

    for name in v3.HISTORICAL_PATHS:
        need(read(root, name) == git_bytes(root, name), "historical_contract_or_lock_changed")
    policy = read(root, document.POLICY_PATH)
    need(hashlib.sha256(policy).hexdigest() == document.POLICY_SHA256, "document_policy_changed")
    worker = document.validate_policy(policy, root)
    need(all(hashlib.sha256(read(root, name)).hexdigest() == expected for name, expected in worker.items()), "worker_source_changed")
    helpers = v3.current_document_helpers(root)
    need(set(helpers) <= set(document.CONTRACT_PATHS), "document_helper_scope")
    need(all(read(root, name) == git_bytes(root, name) for name in document.CONTRACT_PATHS if name not in helpers),
         "direct_document_qualification_changed")
    need(all(hashlib.sha256(read(root, name)).hexdigest() == expected for name, expected in v3.HISTORICAL_COMPANION_PACKAGES),
         "historical_companion_package_changed")
    names = {*v3.v2.QUALIFICATION_PATHS, *v3.v2.TRANSPORT_PATHS, *v3.v2.BOOTSTRAP_PATHS,
             *v3.HISTORICAL_PATHS, *v3.NEW_PATHS, *v3.FOLLOWON_PATHS, *v3.PROTOCOL_FIXTURES,
             *("backend/" + name for name in v3.PROOF_ROOTS), *worker, *document.CONTRACT_PATHS, document.POLICY_PATH,
             *F07_PATHS}
    entries = {name: hashlib.sha256(read(root, name)).hexdigest() for name in sorted(names)}
    need(all(entries[name] == stamp for name, stamp in helpers.items()), "document_helper_changed_during_fingerprint")
    return entries


def source_fingerprint(root: Path, prior_files):
    """Current application/operator source only; no retained producer or QA claim."""
    need(len(prior_files) == PRIOR_APP_FILES and app_digest(prior_files) == PRIOR_APP_SHA256, "qualified_prior_app_changed")
    schema27.verify_source(root)
    current = current_app(root)
    need(len(current) == CANDIDATE_APP_FILES and app_digest(current) == CANDIDATE_APP_SHA256, "current_app_unqualified")
    runtime_contract(root)
    verify_ci(root)
    additive_http(root)
    entries = preserved_sources(root)
    entries.update(contract=CONTRACT, prior_release=PRIOR_RELEASE, prior_app=PRIOR_APP_SHA256,
                   current_app=CANDIDATE_APP_SHA256, current_files=CANDIDATE_APP_FILES,
                   http_delta=digest([HTTP_PATHS, HTTP_SCHEMAS]), schema27=schema27.CONTRACT,
                   native_runner_eligible=False, financial_invariants=digest(v3.v1.INVARIANTS))
    return digest(entries)
