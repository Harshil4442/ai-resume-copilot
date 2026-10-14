"""Standalone source-only money/model environment controls; no app or providers."""
from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import sys
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/gcp"))


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


release = load("hirewiz_model_environment_release", "infra/gcp/monetary_release.py")
cost = load("hirewiz_model_environment_expense", "backend/app/billing/cost_policy.py")


def plan():
    return release.Plan(
        release="a" * 40, image_digest="sha256:" + "b" * 64,
        build_id="11111111-1111-1111-1111-111111111111", ci_run_id=7,
        runtime_secret="hirewiz-runtime-db:2", migration_secret="hirewiz-migration-db:3",
        observer_secret="hirewiz-observer-db:7", database_owner_oid=16387,
        expense_secret="hirewiz-expense-policy:4", expense_sha256="c" * 64,
        model_policy_secret="hirewiz-model-cost-policy:5", model_policy_sha256="d" * 64,
        model_key_secret="hirewiz-model-key:6", model_primary="gemini-3.6-flash",
        model_api_base="https://generativelanguage.googleapis.com",
        runtime_service_account=f"hirewiz-runtime@{release.PROJECT}.iam.gserviceaccount.com",
        migration_service_account=f"hirewiz-migration@{release.PROJECT}.iam.gserviceaccount.com",
        backup_uri=f"gs://{release.PROJECT}-hirewiz-application-artifacts/releases/reviewed.dump",
        inventory_path=ROOT / "unread-synthetic-inventory.json", inventory_sha256="e" * 64,
    )


def policies():
    expense = json.loads((ROOT / "docs/examples/expense-policy-estimated-reviewed-2026-10-09.json").read_bytes())
    now = datetime.now(UTC)
    expense.update(version="synthetic-release-estimate", valid_from=(now - timedelta(hours=1)).isoformat(),
                   expires_at=(now + timedelta(hours=1)).isoformat(), reconciled_through=(now - timedelta(minutes=1)).isoformat())
    validated = cost.ExpensePolicy.model_validate(expense)
    cost.audit(validated)  # The existing finite-pack arithmetic is the authority.
    model = json.loads((ROOT / "infra/gcp/model-cost-policy.candidate.json").read_bytes())
    model["version"] = "synthetic-release-model-policy"
    for quote in model["pricing_quotes"]:
        quote.update(version="synthetic-price", valid_from=(now - timedelta(hours=1)).isoformat(),
                     expires_at=(now + timedelta(hours=1)).isoformat())
    return validated.model_dump(mode="json"), model


def verify_model(value, expense=None, selected=None, raw=None):
    if expense is None:
        expense, _ = policies()
    raw = raw if raw is not None else json.dumps(value)
    selected = replace(selected or plan(), model_policy_sha256=hashlib.sha256(raw.encode()).hexdigest())
    boundary = release.NativeBoundary(ROOT)
    calls = []
    def secret(reference):
        calls.append(("policy-value", reference))
        if reference != selected.model_policy_secret:
            raise AssertionError("A model-key credential must never be read")
        return raw
    def cloud(*args):
        calls.append(args)
        if args != ("secrets", "versions", "describe", "6", "--secret", "hirewiz-model-key"):
            raise AssertionError("Only exact selected model-key metadata is observed")
        return {"state": "ENABLED", "name": "synthetic/versions/6"}
    boundary._secret = secret
    boundary._cloud = cloud
    boundary._command = lambda *_: (_ for _ in ()).throw(AssertionError("No process/provider dispatch"))
    boundary._verify_model_policy(selected, expense)
    return calls


def revision(selected, service):
    # Independent fixture; do not construct it with the production mapper.
    expected = {**release.SAFE_ENV, "APP_RELEASE": selected.release,
                "SERVICE_ROLE": "api" if service == release.SERVICES[0] else "worker"}
    secrets = [("DATABASE_URL", selected.runtime_secret), ("HIREWIZ_EXPENSE_POLICY_JSON", selected.expense_secret)]
    if service in release.SERVICES[:2]:
        expected.update(
            DOCUMENT_WORKER_URL="https://hirewiz-document-inspector-mqff62d75a-uc.a.run.app/inspect",
            DOCUMENT_WORKER_IMAGE_DIGEST="us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-document-inspector@sha256:624597726ce2fefa2b3574fbf0a8ec4ccdc02a826380d76c5edc8ebbd59aa107",
            DOCUMENT_WORKER_POLICY_SHA256="dbd15ce3120bb6211bb6c1d7b166aaddbd8b80e00a72d564e6274ef10db3aaf1",
            DOCUMENT_WORKER_DOCX_RENDERER_VERSION="LibreOffice 25.2.3.2 520(Build:2)",
            DOCUMENT_WORKER_DOCX_FONT_MANIFEST_SHA256="abaf429caeeeb5fbcc000c878a315c89e23ecd3dc912bb74df10761c144f7d60",
        )
        expected.update(LLM_MODEL=selected.model_primary, LLM_API_BASE=selected.model_api_base)
        secrets += [("LLM_MODEL_COST_POLICY_JSON", selected.model_policy_secret), ("LLM_API_KEY", selected.model_key_secret)]
    if service != release.SERVICES[0]:
        expected.update(WORKER_LABEL="analysis-worker" if service == release.SERVICES[1] else "employer-worker",
                        WORKER_ALLOWED_TOPICS="analysis.run" if service == release.SERVICES[1] else "employer.search,employer.refresh,employer.apply,employer.artifact-delete")
    env = [{"name": name, "value": value} for name, value in expected.items()]
    for name, reference in secrets:
        secret_name, key = reference.split(":")
        env.append({"name": name, "valueFrom": {"secretKeyRef": {"name": secret_name, "key": key}}})
    env.append({"name": "RAZORPAY_WEBHOOK_SECRET", "valueFrom": {"secretKeyRef": {"name": "payment-webhook", "key": "7"}}})
    annotations = ({
        "run.googleapis.com/network-interfaces": '[{"network":"hirewiz-private","subnetwork":"hirewiz-callers-us-central1"}]',
        "run.googleapis.com/vpc-access-egress": "private-ranges-only",
    } if service in release.SERVICES[:2] else {})
    return {"metadata": {"labels": {"serving.knative.dev/service": service}, "annotations": annotations},
            "spec": {"serviceAccountName": selected.runtime_service_account, "containers": [{"env": env}]},
            "status": {"imageDigest": selected.image, "conditions": [{"type": "Ready", "status": "True"}]}}


def read_revision(selected, service, observed):
    boundary = release.NativeBoundary(ROOT)
    calls = []
    def cloud(*args):
        calls.append(args)
        if args[:3] == ("run", "revisions", "describe"):
            return observed
        if args[:3] == ("run", "services", "describe"):
            return {"metadata": {"annotations": {"run.googleapis.com/invoker-iam-disabled": "false"}}}
        if args[:2] == ("projects", "get-iam-policy"):
            return {"bindings": []}
        if args[:3] == ("run", "services", "get-iam-policy"):
            members = [f"serviceAccount:hirewiz-tasks@{release.PROJECT}.iam.gserviceaccount.com"]
            if service == release.SERVICES[1]:
                members.append(f"serviceAccount:hirewiz-scheduler@{release.PROJECT}.iam.gserviceaccount.com")
            return {"bindings": [{"role": "roles/run.invoker", "members": members}]}
        raise AssertionError("Unexpected native action")
    boundary._cloud = cloud
    boundary._verify_http_health = lambda *_: calls.append(("synthetic-tagged-health",))
    boundary.verify_revision(selected, service, service + "-00042-test")
    return calls


class ModelEnvironmentTests(unittest.TestCase):
    def test_strict_plan_rejects_ambiguous_new_pins_before_observations(self):
        selected = plan()
        selected.validate()
        for changes in [
            {"model_policy_secret": "model-policy:latest"}, {"model_key_secret": "model-key:0"},
            {"model_policy_sha256": "d" * 63}, {"model_primary": "gpt-4o-mini"},
            {"model_api_base": "https://generativelanguage.googleapis.com?key=secret"},
            {"model_key_secret": selected.expense_secret},
        ]:
            with self.subTest(changes=tuple(changes)), self.assertRaises(release.ReleaseDenied):
                replace(selected, **changes).validate()

    def test_authoritative_policy_and_existing_fallbacks_accept_without_key_access(self):
        expense, model = policies()
        calls = verify_model(model, expense)
        self.assertEqual(calls, [("policy-value", "hirewiz-model-cost-policy:5"),
                                ("secrets", "versions", "describe", "6", "--secret", "hirewiz-model-key")])
        self.assertEqual(release._gemini_fallbacks(ROOT), tuple(q["model"] for q in model["pricing_quotes"]))

    def test_model_authority_refuses_hash_expiry_duplicates_funding_and_unfunded_limits(self):
        expense, good = policies()
        failures = []
        value = copy.deepcopy(good)
        value["pricing_quotes"].pop()
        failures.append(value)
        value = copy.deepcopy(good)
        value["pricing_quotes"][0].update(valid_from="2020-01-01T00:00:00Z", expires_at="2020-01-02T00:00:00Z")
        failures.append(value)
        value = copy.deepcopy(good)
        value["pricing_quotes"].append(value["pricing_quotes"][0])
        failures.append(value)
        value = copy.deepcopy(good)
        value["expense_funding"] = "prepaid"
        failures.append(value)
        value = copy.deepcopy(good)
        value["operation_limits_micros"]["resume_tailor"] = 500001
        failures.append(value)
        value = copy.deepcopy(good)
        value["operation_limits_micros"]["unknown_operation"] = 1
        failures.append(value)
        value = copy.deepcopy(good)
        value["pricing_quotes"][0]["api_base"] = "https://other.example"
        failures.append(value)
        for index, value in enumerate(failures):
            with self.subTest(index=index), self.assertRaises(release.ReleaseDenied):
                verify_model(value, expense)
        with self.assertRaisesRegex(release.ReleaseDenied, "duplicate_input_key"):
            verify_model(good, expense, raw='{"version":"one","version":"two"}')
        boundary = release.NativeBoundary(ROOT)
        boundary._secret = lambda _: json.dumps(good)
        with self.assertRaisesRegex(release.ReleaseDenied, "model_policy_secret_hash_mismatch"):
            boundary._verify_model_policy(plan(), expense)

    def test_disabled_numeric_model_key_metadata_does_not_read_the_credential(self):
        boundary = release.NativeBoundary(ROOT)
        boundary._cloud = lambda *_: {"state": "DISABLED", "name": "synthetic/versions/6"}
        boundary._command = lambda *_: self.fail("Credential value access is forbidden")
        with self.assertRaisesRegex(release.ReleaseDenied, "exact_secret_version_not_enabled"):
            boundary._verify_secret_version("hirewiz-model-key:6")

    def test_staging_maps_exact_service_scope_and_preserves_unrelated_settings(self):
        selected = plan()
        for service in release.SERVICES:
            with self.subTest(service=service):
                boundary = release.NativeBoundary(ROOT)
                calls = []
                boundary._cloud = lambda *args, calls=calls, service=service: (calls.append(args) or {"status": {"latestCreatedRevisionName": service + "-00042-test"}})
                self.assertEqual(boundary.stage(selected, service), service + "-00042-test")
                self.assertEqual(len(calls), 1)
                args = calls[0]
                env = dict(item.split("=", 1) for item in args[args.index("--update-env-vars") + 1][3:].split("|"))
                refs = dict(item.split("=", 1) for item in args[args.index("--update-secrets") + 1].split(","))
                removed = set(args[args.index("--remove-env-vars") + 1].split(","))
                removed_secrets = set(args[args.index("--remove-secrets") + 1].split(","))
                self.assertEqual(refs["DATABASE_URL"], selected.runtime_secret)
                self.assertEqual(refs["HIREWIZ_EXPENSE_POLICY_JSON"], selected.expense_secret)
                self.assertIn("--no-traffic", args)
                for key in ("OPTIONAL_AI_GENERATION_ENABLED", "EMPLOYER_AUTO_SUBMIT_ENABLED", "RAZORPAY_CHECKOUT_ENABLED", "CANDIDATE_ACCOUNT_LIFECYCLE_ENABLED"):
                    self.assertEqual(env[key], "false")
                self.assertTrue(set(release.MODEL_ALIASES) <= removed & removed_secrets)
                self.assertTrue({"RAZORPAY_WEBHOOK_SECRET", "ANALYSIS_TASK_TOKEN", "GOOGLE_CLOUD_PROJECT"}.isdisjoint(removed | removed_secrets))
                if service in release.SERVICES[:2]:
                    self.assertEqual(env["LLM_MODEL"], selected.model_primary)
                    self.assertEqual(env["LLM_API_BASE"], selected.model_api_base)
                    self.assertEqual(refs["LLM_MODEL_COST_POLICY_JSON"], selected.model_policy_secret)
                    self.assertEqual(refs["LLM_API_KEY"], selected.model_key_secret)
                else:
                    self.assertTrue(set(release.MODEL_ENV) <= removed & removed_secrets)
                    self.assertTrue(set(release.MODEL_ENV).isdisjoint(env.keys() | refs.keys()))

    def test_exact_revision_contract_accepts_all_three_scopes_and_callbacks(self):
        for service in release.SERVICES:
            with self.subTest(service=service):
                calls = read_revision(plan(), service, revision(plan(), service))
                self.assertEqual(calls[-1], ("synthetic-tagged-health",))

    def test_revision_refuses_wrong_and_ambiguous_model_config_before_health(self):
        selected = plan()
        service = release.SERVICES[0]
        for name, mutation in [
            ("LLM_MODEL", {"value": "gemini-unreviewed"}),
            ("LLM_API_BASE", {"value": "https://proxy.example"}),
            ("LLM_API_KEY", {"value": "unapproved-literal"}),
            ("LLM_MODEL_COST_POLICY_JSON", {"valueFrom": {"secretKeyRef": {"name": "wrong-policy", "key": "5"}}}),
            ("LLM_API_KEY", {"valueFrom": {"secretKeyRef": {"name": "hirewiz-model-key", "key": "latest"}}}),
        ]:
            with self.subTest(name=name, mutation=tuple(mutation)):
                observed = revision(selected, service)
                item = next(item for item in observed["spec"]["containers"][0]["env"] if item["name"] == name)
                item.update(mutation)
                with self.assertRaisesRegex(release.ReleaseDenied, "joined_revision_identity_or_safety_mismatch"):
                    read_revision(selected, service, observed)
        observed = revision(selected, service)
        observed["spec"]["containers"][0]["env"].append({"name": "LLM_MODEL", "value": selected.model_primary})
        with self.assertRaisesRegex(release.ReleaseDenied, "joined_revision_identity_or_safety_mismatch"):
            read_revision(selected, service, observed)

    def test_employer_refuses_even_empty_model_entries_and_vendor_aliases(self):
        for name in (*release.MODEL_ENV, *release.MODEL_ALIASES):
            with self.subTest(name=name):
                observed = revision(plan(), release.SERVICES[2])
                observed["spec"]["containers"][0]["env"].append({"name": name, "value": ""})
                with self.assertRaisesRegex(release.ReleaseDenied, "joined_revision_identity_or_safety_mismatch"):
                    read_revision(plan(), release.SERVICES[2], observed)

    def test_unknown_service_and_unconditional_fence_never_dispatch(self):
        boundary = release.NativeBoundary(ROOT)
        boundary._cloud = lambda *_: self.fail("Unknown identity/fence cannot dispatch")
        with self.assertRaisesRegex(release.ReleaseDenied, "joined_service_identity_invalid"):
            boundary.stage(plan(), "unknown-worker")
        with self.assertRaisesRegex(release.ReleaseDenied, "native_external_consumer_and_provider_fence_verifier_missing"):
            boundary.verify_fences(plan())

    def test_authority_pin_matches_exact_source_and_reads_same_erasure_set(self):
        source = (ROOT / "infra/gcp/monetary_release.py").read_bytes()
        tree = ast.parse((ROOT / "backend/scripts/nologin_role_preparation.py").read_bytes())
        pinned = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "RELEASE_SOURCE_SHA256" for target in node.targets))
        self.assertEqual(pinned, hashlib.sha256(source).hexdigest())
        self.assertEqual(len(release.ERASABLE_TABLES), 39)
        self.assertTrue({"payment_orders", "model_cost_liabilities"}.isdisjoint(release.ERASABLE_TABLES))

    def test_pure_controls_never_initialize_application_or_provider_modules(self):
        before = set(sys.modules)
        expense, model = policies()
        verify_model(model, expense)
        introduced = set(sys.modules) - before
        self.assertTrue({"app.main", "backend.app.main", "google.genai"}.isdisjoint(introduced))


if __name__ == "__main__":
    unittest.main()
