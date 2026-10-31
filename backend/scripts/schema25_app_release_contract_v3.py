"""Finite prospective e23→schema26 app qualification; never serving/migration proof.

Expected prior source/build pins are prospective. Actual capture must independently
prove that exact prior serves and schema26/fence was genuinely applied. Historical
v1/v2 contracts and actual-source qualification remain unchanged.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from scripts import evidence_writer_schema26_contract as schema26
from scripts import schema25_app_release_contract as v1
from scripts import schema25_app_release_contract_v2 as v2

CONTRACT = "hirewiz_schema26_app_rolling_v3"
PRIOR_RELEASE = "e23c940e6133323c3c1ff11e646a4ca9b2b0b165"
PRIOR_APP_SHA256 = "fa3b778e5fa1b9fe900dc5625e18682a0f0987b68259bb8eed8ff3ae5f40800a"
# Exact reviewed-source inventory; this pin is not native qualification.
CANDIDATE_APP_SHA256 = '605015f59cf9143810ec25cb9dd4206b3b7e2e592bc9b205b8d58a5b3d2323fe'
CANDIDATE_APP_FILES = 289
COMPATIBILITY_STEP = "Qualify schema26 app v3 compatibility"
COMPATIBILITY_COMMAND = ".venv/bin/python -I -B scripts/qualify_schema26_compatibility.py"
PROTOCOL_STEP = "Qualify schema26 app v3 native protocol"
PROTOCOL_COMMAND = ".venv/bin/python -I -B -m pytest --noconftest -q tests/test_schema25_app_protocol_v3.py"
RETAINED_STEP = "Qualify retained actual-source schema25 app v2"
RETAINED_COMMAND = ".venv/bin/python scripts/qualify_schema25_retained_v2.py"
HISTORICAL_CURRENT_IGNORES = ("tests/test_schema25_app_compatibility_v2_postgres.py",
    "tests/test_schema25_app_rolling_v2.py", "tests/test_schema25_app_alias_retirement.py",
    "tests/test_schema25_app_protocol_v2.py")
STAGE5_FLAGS = v2.STAGE5_FLAGS
TRANSPORT_PATHS = v2.TRANSPORT_PATHS
PRIVACY_FLAG = "PRIVACY_ERASURE_RECOVERY_ENABLED"
NATIVE_SUITES = ("tests/test_schema25_app_compatibility_v3_postgres.py",
    "tests/test_evidence_writer_fence_postgres.py", "tests/test_schema26_catalog_postgres.py",
    "tests/test_evidence_review_postgres.py", "tests/test_source_transcription_review_postgres.py",
    "tests/test_unified_synchronous_postgres.py", "tests/test_privacy_erasure_postgres.py",
    "tests/test_evidence_writer_schema26_step_postgres.py",
    "tests/test_unified_browser_submission_postgres.py", "tests/test_submission_canary_postgres.py")
PROTOCOL_SUITES = (*v2.PROTOCOL_SUITES, "tests/test_evidence_writer_adapters.py",
    "tests/test_evidence_writer_fence.py", "tests/test_evidence_writer_schema26_contract.py",
    "tests/test_neon_schema26_release_transport.py", "tests/test_owned_dashboard.py",
    "tests/test_source_transcription.py", "tests/test_source_transcription_review.py",
    "tests/test_source_transcription_consumers.py", "tests/test_native_acquisition_channel.py",
    "tests/test_native_acquisition_job.py", "tests/test_qualify_schema26_compatibility.py",
    "tests/test_schema26_operator_successors.py", "tests/test_privacy_erasure_recovery.py",
    "tests/test_mail_budget.py", "tests/test_evidence_writer_schema26_step.py",
    "tests/test_submission_contract.py", "tests/test_unified_browser_submission.py",
    "tests/test_submission_canary.py", "tests/test_submission_source_package.py",
    "tests/test_schema26_maintenance_guard.py", "tests/test_schema26_lifecycle_join.py",
    "tests/test_schema26_native_read_ports.py", "tests/test_schema26_native_migration.py",
    "tests/test_schema26_admin_graph_constructor.py")
PROOF_ROOTS = (*PROTOCOL_SUITES, *NATIVE_SUITES,
    "tests/test_schema25_app_rolling_v3.py", "tests/test_schema25_app_protocol_v3.py",
    "tests/test_qualify_schema25_retained_v2.py", "tests/browser_paired_assistance_chrome_bridge.py",
    "tests/browser_assistance_chrome_bridge.py", "tests/browser_submit_chrome_bridge.py",
    "tests/test_browser_assistance_isolated.py")
PROTOCOL_FIXTURES = ('backend/pyproject.toml', 'backend/tests/expense_policy_fixtures.py', 'backend/tests/fixtures/candidate_ingress.py', 'backend/tests/fixtures/evidence_fence.py', 'backend/tests/fixtures/firestore_emulator.py', 'backend/tests/fixtures/gcp_closure.py', 'backend/tests/fixtures/gcp_partitioned_publication.py', 'backend/tests/fixtures/gcp_publication.py', 'backend/tests/fixtures/pairing_authority.py', 'backend/tests/fixtures/reviewed_runtime.py', 'backend/tests/fixtures/stage5_native.py', 'backend/tests/fixtures/transcription_source.py', 'backend/tests/model_cost_fixtures.py', 'backend/tests/test_ai_efficiency.py', 'backend/tests/test_browser_pairing_production.py', 'backend/tests/test_browser_submit_native.py', 'backend/tests/test_canary_admission.py', 'backend/tests/test_candidate_production_factory.py', 'backend/tests/test_candidate_scoped_lifecycle_emulator.py', 'backend/tests/test_document_processor.py', 'backend/tests/test_document_worker_deployment.py', 'backend/tests/test_employer_admissions.py', 'backend/tests/test_employer_admissions_postgres.py', 'backend/tests/test_employer_services.py', 'backend/tests/test_evidence_review.py', 'backend/tests/test_evidence_review_postgres.py', 'backend/tests/test_evidence_writer_adapters.py', 'backend/tests/test_evidence_writer_fence_postgres.py', 'backend/tests/test_free_core.py', 'backend/tests/test_gcp_journal_sdk.py', 'backend/tests/test_gcp_pairing_emulator.py', 'backend/tests/test_gcp_password_lifetime_emulator.py', 'backend/tests/test_gcp_password_reauth_emulator.py', 'backend/tests/test_gcp_rpc.py', 'backend/tests/test_gcp_scoped_authority_emulator.py', 'backend/tests/test_native_acquisition_channel.py', 'backend/tests/test_native_input_acquisition.py', 'backend/tests/test_native_metadata_admission.py', 'backend/tests/test_neon_linux_execution.py', 'backend/tests/test_neon_schema19_release_transport.py', 'backend/tests/test_owned_bootstrap.py', 'backend/tests/test_pairing_kms.py', 'backend/tests/test_privacy_erasure_postgres.py', 'backend/tests/test_resume_source.py', 'backend/tests/test_rolling_release.py', 'backend/tests/test_schema25_app_compatibility_postgres.py', 'backend/tests/test_schema25_app_compatibility_v3_postgres.py', 'backend/tests/test_schema25_app_rolling_release.py', 'backend/tests/test_schema26_catalog_postgres.py', 'backend/tests/test_source_transcription_review.py', 'backend/tests/test_source_transcription_review_postgres.py', 'backend/tests/test_supported_evaluation.py', 'backend/tests/test_unified_credits.py', 'backend/tests/test_unified_execution.py', 'backend/tests/test_unified_synchronous.py', 'backend/tests/test_unified_synchronous_postgres.py', 'backend/tests/browser_submit_owned.py', 'backend/tests/browser_submission_unified_fixture.py', 'backend/tests/test_browser_hosted_network.py', 'backend/tests/test_browser_submit_postgres.py', 'backend/tests/test_schema25_maintenance.py')
HISTORICAL_PATHS = ("backend/scripts/schema25_app_release_contract.py",
    "backend/scripts/schema25_app_release_contract_v2.py", "backend/scripts/qualify_schema25_retained_v1.py",
    "backend/tests/test_schema25_app_compatibility_postgres.py",
    "backend/tests/test_schema25_app_compatibility_v2_postgres.py", "backend/tests/test_schema25_app_rolling_v2.py",
    "backend/tests/test_schema25_app_protocol_v2.py", "backend/tests/test_schema25_app_alias_retirement.py",
    "infra/gcp/schema25_app_rolling_release.py", "infra/gcp/schema25_app_alias_retirement.py",
    "backend/tests/conftest.py", "backend/uv.lock", "frontend/package-lock.json")
# Delivered source and the actual finite owned-wire/package proof inputs. These
# paths attest source custody only; an availability GET grants no native rights.
COMPANION_EXTENSION_PATHS = tuple("browser-companion/extension/" + name for name in (
    "assistance-runtime.js", "assistance-transport.js", "background.js", "config.js", "device.js",
    "greenhouse-native-network.js", "greenhouse-owned-upload.js", "greenhouse-resume-component.js", "local-adapter.js",
    "lost-key-recovery.js", "manifest.json", "pairing-runtime.js", "pairing-transport.js", "popup.css", "popup.html",
    "popup.js", "protocol.js", "razorpay-adapter.js", "startup-diagnostics.js", "submission-billing.js", "transport.js"))
COMPANION_PROOF_ROOTS = (
    "browser-companion/scripts/check-package.js", "browser-companion/scripts/native-fixture-build.js",
    "browser-companion/tests/protocol.test.js", "browser-companion/tests/pairing-transport.test.js",
    "browser-companion/tests/pairing-runtime.test.js", "browser-companion/tests/assistance-transport.test.js",
    "browser-companion/tests/startup-diagnostics.test.js", "browser-companion/tests/device-phase-diagnostics.test.js",
    "browser-companion/tests/lost-key-recovery.test.js", "browser-companion/tests/package-release.test.js",
    "browser-companion/tests/hosted-network-browser.test.js", "browser-companion/tests/native-runtime-browser.test.js",
    "browser-companion/tests/paired-assistance-browser.test.js", "browser-companion/tests/lost-key-recovery-browser.test.js",
    "browser-companion/tests/browser.test.js", "browser-companion/tests/assistance-browser.test.js",
    "browser-companion/tests/submission-billing.test.js", "browser-companion/tests/submission-billing-browser.test.js",
    "browser-companion/tests/razorpay-live-adapter.test.js", "browser-companion/tests/assistance-runtime.test.js",
    "browser-companion/tests/razorpay-fixture.test.js", "browser-companion/tests/razorpay-browser.test.js")
PAIRING_PATHS = (*COMPANION_EXTENSION_PATHS, *COMPANION_PROOF_ROOTS,
    "browser-companion/fixtures/authority.js", "browser-companion/tests/request-deadlines.js",
    "browser-companion/tests/native-https-fixture.js", "browser-companion/tests/pairing-readiness-fixture.js",
    "browser-companion/fixtures/server.js", "browser-companion/scripts/fixture-build.js",
    "browser-companion/fixtures/razorpay/adapter.js", "browser-companion/fixtures/razorpay/form.js",
    "browser-companion/fixtures/razorpay/schema.js", "browser-companion/fixtures/razorpay/server.js",
    "browser-companion/fixtures/razorpay/hosted-form.js", "browser-companion/fixtures/razorpay/submit-form.js",
    "browser-companion/fixtures/razorpay/typed-form.js",
    "browser-companion/package.json", "browser-companion/package-lock.json", "browser-companion/scripts/package-reviewed.py",
    "backend/tests/browser_paired_assistance_chrome_bridge.py",
    "backend/tests/browser_assistance_chrome_bridge.py", "backend/tests/browser_submit_chrome_bridge.py",
    "backend/tests/browser_submit_owned.py", "backend/tests/test_browser_assistance_isolated.py",
    "frontend/app/api/browser-pairing/device/[operation]/route.ts",
    "frontend/app/api/browser-pairing/device/[operation]/route.test.ts",
    "frontend/lib/browserPairingGateway.ts", "frontend/lib/browserTransportTimeouts.ts",
    "frontend/lib/browserHostedNetworkReview.ts", "frontend/components/ui/Button.tsx", "frontend/lib/cn.ts",
    "frontend/package.json", "frontend/vitest.config.mts", "frontend/vitest.setup.ts", "frontend/tsconfig.json",
    "frontend/components/BrowserAssistanceReview.tsx", "frontend/components/BrowserAssistanceReview.test.tsx",
    "frontend/public/downloads/hirewiz-browser-companion-0.8.6.zip",
    "frontend/public/downloads/hirewiz-browser-companion-0.8.7.zip",
    "frontend/public/downloads/hirewiz-browser-companion-0.8.8.zip",
    "browser-companion/tests/legacy-submission-dispatch-fixture.js",
    "frontend/lib/browserSubmissionBilling.ts", "frontend/lib/browserSubmissionBilling.test.ts",
    "frontend/lib/browserAssistanceGateway.ts", "frontend/lib/browserAssistanceGateway.test.ts",
    "frontend/components/BrowserSubmissionReview.test.tsx",
    "frontend/components/EmployerApplicationPanel.tsx", "frontend/components/EmployerApplicationPanel.test.tsx")
HISTORICAL_COMPANION_PACKAGES = (('frontend/public/downloads/hirewiz-browser-companion-0.2.0.zip', 'd7ac1d2abdfbcd0871aa1613b95f0cc2798249679d33101ddf49499679d463e8'), ('frontend/public/downloads/hirewiz-browser-companion-0.3.0.zip', '088107e95a4570f829ed32292ca520c9d29fff2d64966ae8f5daa1840a40f618'), ('frontend/public/downloads/hirewiz-browser-companion-0.4.0.zip', '880f4510dacf4b6ea40f741322c1560067c5296209ba8f81553522aa76d9ab5d'), ('frontend/public/downloads/hirewiz-browser-companion-0.5.0.zip', '6a2cdd049d4049dc95a01219bba8c304881860dd45590e179877058b7370c97f'), ('frontend/public/downloads/hirewiz-browser-companion-0.6.0.zip', '901fd8723544fa42c691f616c9498ed9463c06c923ae4cb25407f81d471ecfe9'), ('frontend/public/downloads/hirewiz-browser-companion-0.7.0.zip', '9aecb5881c014352c920d92f2bde67ddadf42b197b017a74bf0b5d0175c4baac'), ('frontend/public/downloads/hirewiz-browser-companion-0.8.0.zip', '887d6d37c9cfd6c6e2388a8e0e1b97c0e9518199035786a740f4b1e73b0e1675'), ('frontend/public/downloads/hirewiz-browser-companion-0.8.1.zip', '174cc0b6170c1efeeb8b12ddd460734db89689f5860eca4ae4c6bdd894f9b18a'), ('frontend/public/downloads/hirewiz-browser-companion-0.8.2.zip', 'c952be0f5cf7411a0a5a314b6624fa0264762b0c5668f7eb45805b257de53107'), ('frontend/public/downloads/hirewiz-browser-companion-0.8.3.zip', '8f5597efa2549fa0b6ecf76545ce4cbc9b78f9e1488f7ad8c1fd74ba6c051d45'), ('frontend/public/downloads/hirewiz-browser-companion-0.8.4.zip', 'a1324aeda3b168a519ed47ab7a2a13228c91d5e6aa5ab2bea9ab9ba0d09f8c40'), ('frontend/public/downloads/hirewiz-browser-companion-0.8.5.zip', '039f0236d3f391b45e602c52de7970adbb1c8930511b813b49e6c3302f99555a'), ('frontend/public/downloads/hirewiz-browser-companion-0.8.6.zip', 'c3c749b8996757469e6cca9be446d069c82d614edc610aa7fbe764c17c7a342f'), ('frontend/public/downloads/hirewiz-browser-companion-0.8.7.zip', 'ae48e6133229d558a5f4a653828d237fb0fdd043cd509640e5c9c076aa9d8a75'))
MIGRATION26_PATHS = ('backend/alembic.ini', 'backend/scripts/evidence_writer_schema26_step.py', 'infra/gcp/schema26_migration_metadata.py', 'backend/tests/test_evidence_writer_schema26_step.py', 'backend/tests/test_evidence_writer_schema26_step_postgres.py', 'backend/scripts/browser_schema19_step.py', 'backend/scripts/browser_schema19_contract.py', 'backend/scripts/native_resume_schema20_step.py', 'backend/scripts/native_resume_schema20_contract.py', 'backend/scripts/email_password_schema21_step.py', 'backend/scripts/email_password_schema21_contract.py', 'backend/scripts/unified_credit_schema25_step.py', 'backend/scripts/unified_credit_schema25_contract.py', 'backend/scripts/unified_credit_schema25_manifest.json', 'backend/alembic/env.py', 'infra/gcp/schema25_migration_metadata.py')
NEW_PATHS = (*MIGRATION26_PATHS, *PAIRING_PATHS,
    'backend/tests/fixtures/browser_submission_v5.json', 'frontend/app/api/browser-assistance/candidate/[operation]/route.ts', 'frontend/app/api/browser-assistance/device/[operation]/route.ts', 'frontend/components/ui/LoadingBlock.tsx', 'frontend/lib/candidateAuthServer.ts', 'frontend/lib/candidateIngressFetch.ts', 'frontend/lib/employerJobs.ts', 'frontend/lib/resumeSource.ts', 'frontend/lib/searchPreferences.ts', 'frontend/lib/types.ts', 'frontend/lib/useProgressQuery.ts', "Dockerfile", ".dockerignore",
    "infra/gcp/v3_readonly_adapter.py", "infra/gcp/schema26_maintenance_guard.py",
    "infra/gcp/schema26_lifecycle_join.py", "infra/gcp/schema26_native_migration.py", "backend/scripts/schema25_app_release_contract_v3.py",
    "infra/gcp/schema26_app_rolling_v3.py", "backend/tests/test_schema25_app_rolling_v3.py",
    "backend/tests/test_schema25_app_compatibility_v3_postgres.py", "backend/tests/test_schema25_app_protocol_v3.py",
    "backend/scripts/qualify_schema26_compatibility.py", "backend/tests/test_qualify_schema26_compatibility.py",
    "backend/tests/test_schema26_catalog_postgres.py",
    "backend/scripts/qualify_schema25_retained_v2.py", "backend/tests/test_qualify_schema25_retained_v2.py",
    "backend/scripts/evidence_writer_schema26_contract.py", "backend/scripts/neon_direct_identity.py",
    "backend/scripts/neon_execution_files.py", "backend/scripts/monetary_cutover_preflight.py",
    *(name for name, _ in HISTORICAL_COMPANION_PACKAGES),
    "backend/app/domains/credits/reconciliation_schema26.py", "backend/scripts/reconcile_native_expenses_schema26.py",
    "backend/scripts/review_canary_audience_schema26.py", "backend/tests/test_schema26_operator_successors.py",
    "backend/tests/test_supported_evaluation_services_postgres.py",
    "backend/tests/test_model_cost_result_lifetime_postgres.py",
    "docs/runbooks/schema26-app-v3-qualification.md", "frontend/lib/ownerProfile.test.tsx")

HTTP_PATHS = {'/api/auth/dashboard': '41015050ca3e00275c84be7c17ac5825fb323aacb81f29d1c7fe59cc3290e8d8', '/api/auth/privacy-erasure/confirm': '5d71115415ee37fd172c9397c936d99e3d89c6360de5e3104dce449740138da3', '/api/auth/privacy-erasure/request': '989596113900a76291cfb29f9d6ce2756552c15ccabfd587b6cedcf070647e56', '/api/v1/evidence-items/transcription-review/{resume_id}': 'ce5716e3b997f80f1a6d2a5f37df1ec259e07e6fd8b0e05ba63edff10f805f6a', '/api/v1/evidence-items/{evidence_id}': '4dc85e1f8a0e249a55c1ecd9eb43c5614c912edeb60e6e9717b02982ce25a68c', '/api/v1/evidence-items/{evidence_id}/transcription-review': 'e4bd0e334ffc3be625c946ff651a1ca3661d13e5a03d982de926e6b31c61ec83'}
HTTP_SCHEMAS = {'CandidateRequest': '48ca20811f1c54a93053f1356f9e10444240733ac4bea7e6c7441f02e31a9653', 'DashboardOpportunities': '0337e64b8cb96f25d9fddbeda5f0fe35484598101da937716980f8de2ee12d27', 'DashboardOpportunity': '8c844a01a334ddc6ff7f66a9c97698f15738f2646139dfb3e08c34054ea5a1d1', 'DashboardReminder': '83979e649ca361f5fabdf30fef3379a0ee361700c15e560612f90b92cd4d195f', 'DashboardResumeSignal': '8cdbecba7f818c7b6cc4b1dfcefcfb9cf89673d49f0a67c6b26c397591a6f274', 'DashboardSummary': '0e36bc6ad4686650753cc08d5700c5743ad565dbe8a3e4591f33b8cc7800a77c', 'ErasureCompleted': 'c979a12a196d3a0e91370b24c0369b02a718481e85701c7cb2e24b7176eded26', 'ErasureConfirm': 'e8ec671f74c32d33e9faabe51a7b90ca0bcab4a3ae9cb136bd0b2ee57abea5ef', 'ErasureRequest': '22f796938f083c3f08c25e59a00b8cd0da086d34daf1f78e2cdd697e8a8f29cf', 'ErasureRequested': '70660ea70a1e3a288b1a325e2a4d20711162f05bb217684260c44ffbb553f570', 'EvidenceResponse': 'c227750a2fda2a980ba30772562e947e72ce9f810ba113405b1e88837146de2e', 'EvidenceUpdate': '61ad43dc56dc413dcc92c6c5a624c5599a4cd46380df761346d400173e7993b3', 'OwnedDashboardView': 'c67e0b406ebbd841f777388383f2d22153cdd95777b6aab914f9226134bd82b6', 'TranscriptionReviewAction': '97d31002f30dac044d837acbdf08b85a0654334eeb0b2131ebca9322c59d04ac', 'TranscriptionReviewResponse': '8fbad92f42eb522ca7865cccf301f049148726c363e5c7ce0debd8846f19194a', 'TranscriptionSectionReview': 'be8c71dd374bf97a93c6750903b7c4d59af848f817a4cbc9ce6246cbd99b2834'}
FOLLOWON_PATHS = ('.github/workflows/ci.yml', 'backend/alembic/versions/20261031_0026_evidence_writer_fence.py', 'backend/app/domains/analysis/native_input_acquisition.py', 'backend/app/domains/analysis/operations.py', 'backend/app/domains/analysis/service.py', 'backend/app/domains/analysis/supported_evaluation.py', 'backend/app/domains/analysis/supported_evaluation_synchronous.py', 'backend/app/domains/candidate_accounts/account_erasure.py', 'backend/app/domains/candidate_accounts/dashboard.py', 'backend/app/domains/candidate_accounts/deletion_provenance.py', 'backend/app/domains/candidate_accounts/email_erasure.py', 'backend/app/domains/candidate_accounts/email_reset.py', 'backend/app/domains/candidate_accounts/service.py', 'backend/app/domains/candidate_ingress/contracts.py', 'backend/app/domains/career/evidence_fence_definition.py', 'backend/app/domains/career/evidence_write_fence.py', 'backend/app/domains/career/schemas.py', 'backend/app/domains/career/service.py', 'backend/app/domains/career/transcription_review.py', 'backend/app/domains/credits/reconciliation_schema26.py', 'backend/app/domains/credits/synchronous.py', 'backend/app/domains/notifications/mail_budget.py', 'backend/app/domains/notifications/password_reset.py', 'backend/app/domains/notifications/privacy_erasure.py', 'backend/app/domains/notifications/service.py', 'backend/app/main.py', 'backend/app/models.py', 'backend/app/routers/auth.py', 'backend/app/routers/jobs.py', 'backend/app/routers/llm.py', 'backend/app/routers/password_reset.py', 'backend/app/routers/privacy_erasure.py', 'backend/app/routers/rag.py', 'backend/app/routers/recommendations.py', 'backend/app/routers/resume.py', 'backend/app/routers/v1/career.py', 'backend/app/services/provider_input_guard.py', 'backend/app/services/source_transcription.py', 'backend/openapi.json', 'backend/scripts/evidence_writer_schema26_contract.py', 'backend/scripts/native_acquisition_channel.py', 'backend/scripts/native_acquisition_job.py', 'backend/scripts/neon_direct_identity.py', 'backend/scripts/qualify_schema26_compatibility.py', 'backend/scripts/reconcile_native_expenses_schema26.py', 'backend/scripts/review_canary_audience_schema26.py', 'backend/scripts/schema25_app_release_contract_v3.py', 'backend/tests/fixtures/evidence_fence.py', 'backend/tests/fixtures/transcription_source.py', 'backend/tests/test_candidate_lifetime_migration_postgres.py', 'backend/tests/test_career_workspace.py', 'backend/tests/test_employer_admissions_postgres.py', 'backend/tests/test_evidence_review.py', 'backend/tests/test_evidence_review_postgres.py', 'backend/tests/test_evidence_writer_adapters.py', 'backend/tests/test_evidence_writer_fence.py', 'backend/tests/test_evidence_writer_fence_postgres.py', 'backend/tests/test_evidence_writer_schema26_contract.py', 'backend/tests/test_expense_budget_postgres.py', 'backend/tests/test_fresh_user_no_ai_journey.py', 'backend/tests/test_generation_gate.py', 'backend/tests/test_learning_rag_privacy.py', 'backend/tests/test_mail_budget.py', 'backend/tests/test_model_cost_budgets_postgres.py', 'backend/tests/test_model_cost_liabilities_postgres.py', 'backend/tests/test_model_cost_result_lifetime_postgres.py', 'backend/tests/test_native_acquisition_channel.py', 'backend/tests/test_native_acquisition_job.py', 'backend/tests/test_neon_schema26_release_transport.py', 'backend/tests/test_owned_dashboard.py', 'backend/tests/test_privacy_erasure_postgres.py', 'backend/tests/test_privacy_erasure_recovery.py', 'backend/tests/test_qualify_schema26_compatibility.py', 'backend/tests/test_schema25_app_compatibility_v3_postgres.py', 'backend/tests/test_schema25_app_protocol_v3.py', 'backend/tests/test_schema25_app_rolling_v3.py', 'backend/tests/test_schema26_catalog_postgres.py', 'backend/tests/test_schema26_operator_successors.py', 'backend/tests/test_source_transcription.py', 'backend/tests/test_source_transcription_consumers.py', 'backend/tests/test_source_transcription_review.py', 'backend/tests/test_source_transcription_review_postgres.py', 'backend/tests/test_supported_evaluation_services_postgres.py', 'backend/tests/test_unified_credit_migrations_postgres.py', 'backend/tests/test_unified_synchronous.py', 'backend/tests/test_unified_synchronous_postgres.py', 'docs/runbooks/NATIVE_QUALITY_INPUT_ACQUISITION.md', 'docs/runbooks/development-native-job-entry.md', 'docs/runbooks/evidence-review-versions.md', 'docs/runbooks/evidence-writer-schema26.md', 'docs/runbooks/owned-dashboard-snapshot.md', 'docs/runbooks/schema26-app-v3-qualification.md', 'frontend/app/api/privacy-erasure/[operation]/route.test.ts', 'frontend/app/api/privacy-erasure/[operation]/route.ts', 'frontend/app/dashboard/page.test.tsx', 'frontend/app/dashboard/page.tsx', 'frontend/app/privacy-erasure/confirm/page.tsx', 'frontend/app/privacy-erasure/page.tsx', 'frontend/app/workspace/[id]/page.test.tsx', 'frontend/app/workspace/[id]/page.tsx', 'frontend/components/Nav.test.tsx', 'frontend/components/Nav.tsx', 'frontend/components/PasswordResetAnalytics.test.tsx', 'frontend/components/PrivacyErasure.test.tsx', 'frontend/components/PrivacyErasure.tsx', 'frontend/components/SessionProviderWrapper.tsx', 'frontend/instrumentation-client.ts', 'frontend/lib/accountTransportClient.ts', 'frontend/lib/accountTransportServer.test.ts', 'frontend/lib/accountTransportServer.ts', 'frontend/lib/candidateIngressServer.ts', 'frontend/lib/career.ts', 'frontend/lib/fixtures/ownedDashboard.ts', 'frontend/lib/generated/api.ts', 'frontend/lib/ownedDashboardClient.ts', 'frontend/lib/ownedDashboardContract.test.ts', 'frontend/lib/ownedDashboardContract.ts', 'frontend/lib/passwordResetPrivacy.test.ts', 'frontend/lib/passwordResetPrivacy.ts', 'frontend/lib/privacyErasureClient.ts', 'frontend/lib/privacyErasureLink.test.ts', 'frontend/lib/privacyErasureLink.ts', 'frontend/next.config.mjs', 'infra/gcp/schema26_app_rolling_v3.py')


def need(value, reason):
    v1.need(value, "v3_" + reason)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def git_bytes(root, name):
    return subprocess.run(["git", "show", PRIOR_RELEASE + ":" + name], cwd=root,
        check=True, capture_output=True, timeout=10).stdout


def additive_http(root):
    old = json.loads(git_bytes(root, "backend/openapi.json"))
    current = json.loads((root / "backend/openapi.json").read_bytes())
    for group, changes in (("paths", HTTP_PATHS), ("schemas", HTTP_SCHEMAS)):
        before = old["paths"] if group == "paths" else old["components"]["schemas"]
        after = current["paths"] if group == "paths" else current["components"]["schemas"]
        for name, expected in changes.items():
            need(name in after and digest(after[name]) == expected, "reviewed_http_delta_changed")
            if name in before:
                after[name] = before[name]
            else:
                after.pop(name)
    need(current == old, "unreviewed_http_change")


def verify_ci(root):
    import yaml

    steps = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())["jobs"]["backend"]["steps"]
    pg = "postgresql+psycopg://hirewiz:hirewiz@127.0.0.1:55433/hirewiz_admission_test"
    native_env = {"HIREWIZ_APP_COMPATIBILITY_REQUIRED": "true", "HIREWIZ_AUTHORITY_EMULATOR_REQUIRED": "true",
        "HIREWIZ_TEST_POSTGRES_URL": pg, "HIREWIZ_OWNED_REDIS_SERVER": "${{ steps.native-metadata-redis.outputs.binary }}"}
    for name, command, env in ((COMPATIBILITY_STEP, COMPATIBILITY_COMMAND,
            {"HIREWIZ_APP_COMPATIBILITY_REQUIRED": "true", "HIREWIZ_TEST_POSTGRES_URL": pg}),
            (RETAINED_STEP, RETAINED_COMMAND, native_env), (PROTOCOL_STEP, PROTOCOL_COMMAND, native_env)):
        found = [step for step in steps if step.get("name") == name]
        need(found == [{"name": name, "working-directory": "backend", "run": command, "env": env}],
            "mandatory_ci_step_changed")
    retained1 = [step for step in steps if step.get("name") == "Qualify retained actual-source schema25 app v1"]
    need(retained1 == [{"name": "Qualify retained actual-source schema25 app v1", "working-directory": "backend",
        "env": {"HIREWIZ_TEST_POSTGRES_URL": pg, "HIREWIZ_APP_COMPATIBILITY_REQUIRED": "true"},
        "run": ".venv/bin/python scripts/qualify_schema25_retained_v1.py"}], "retained_v1_step_changed")
    full = [step for step in steps if step.get("name") == "Run backend tests"]
    exact = ".venv/bin/pytest -q -p scripts.ci_postgres_lock_diagnostics " + " ".join(
        "--ignore=" + name for name in HISTORICAL_CURRENT_IGNORES) + "\n"
    need(len(full) == 1 and exact in full[0]["run"]
        and full[0]["env"].get("HIREWIZ_AUTHORITY_EMULATOR_REQUIRED") == "true"
        and full[0]["env"].get("HIREWIZ_OWNED_REDIS_SERVER") == native_env["HIREWIZ_OWNED_REDIS_SERVER"],
        "current_and_retained_suites_required")
    checkout = [step for step in steps if step.get("uses", "").startswith("actions/checkout@")]
    need(len(checkout) == 1 and checkout[0].get("with", {}).get("fetch-depth") == 0, "git_history_required")


def source_fingerprint(root: Path, prior_files):
    import sys
    sys.path.insert(0, str(root / "infra/gcp"))
    import document_source_candidate as document
    import unified_credit_schema25_release as release

    schema26.verify_source(root)
    need(release.prior_app_digest(prior_files) == PRIOR_APP_SHA256, "prior_app_changed")
    current = {str(path.relative_to(root)): path.read_bytes() for path in (root / "backend/app").rglob("*")
        if path.is_file() and path.suffix in {".py", ".json"} and "__pycache__" not in path.parts}
    need(len(current) == CANDIDATE_APP_FILES and release.prior_app_digest(current) == CANDIDATE_APP_SHA256,
        "candidate_app_unqualified")
    for name in HISTORICAL_PATHS:
        need((root / name).read_bytes() == git_bytes(root, name), "historical_contract_or_lock_changed")
    policy = root / document.POLICY_PATH
    need(hashlib.sha256(policy.read_bytes()).hexdigest() == document.POLICY_SHA256, "worker_policy_changed")
    worker = document.validate_policy(policy.read_bytes(), root)
    need(all(hashlib.sha256((root / name).read_bytes()).hexdigest() == expected for name, expected in worker.items()),
        "worker_source_changed")
    need(all((root / name).read_bytes() == git_bytes(root, name) for name in document.CONTRACT_PATHS),
        "direct_document_qualification_changed")
    need(all(hashlib.sha256((root / name).read_bytes()).hexdigest() == expected
        for name, expected in HISTORICAL_COMPANION_PACKAGES), "historical_companion_package_changed")
    verify_ci(root)
    additive_http(root)
    names = {*v2.QUALIFICATION_PATHS, *v2.TRANSPORT_PATHS, *v2.BOOTSTRAP_PATHS, *HISTORICAL_PATHS,
        *NEW_PATHS, *FOLLOWON_PATHS, *PROTOCOL_FIXTURES, *("backend/" + name for name in PROOF_ROOTS),
        *worker, *document.CONTRACT_PATHS, document.POLICY_PATH}
    entries = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sorted(names)}
    entries.update(prior_app=PRIOR_APP_SHA256, candidate_app=CANDIDATE_APP_SHA256,
        http_delta=digest([HTTP_PATHS, HTTP_SCHEMAS]), schema26=schema26.CONTRACT,
        financial_invariants=digest(v1.INVARIANTS))
    return digest(entries)
