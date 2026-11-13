"""Mandatory current27 CI behavior checks, not provider or rollout authority.

Both phases execute their original isolated pytest child once. Owned synthetic
runner tests cannot qualify an application, native resource, or database.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "infra/gcp"), str(ROOT)]
FAMILY = "hirewiz_schema27_app_v1"
COMPATIBILITY_SUITES = (
    "tests/test_notification_schema27_step_postgres.py",
    "tests/test_notification_schema_compat_postgres.py",
    "tests/test_schema27_native_migration.py",
    "tests/test_neon_schema27_release_postgres.py",
    "tests/test_digest_delivery_integration.py",
    "tests/test_mail_budget_postgres.py",
    "tests/test_schema27_app_compatibility_postgres.py",
    "tests/test_interview_input_guards.py",
    "tests/test_interview_input_postgres.py",
    "tests/test_credit_release_margin_gate.py",
    "tests/test_payment_expenses.py",
    "tests/test_payment_funding.py",
    "tests/test_payment_funding_postgres.py",
    "tests/test_payment_funding_operator.py",
    "tests/test_unified_payment_expense_admission.py",
    "tests/test_opportunity_browsing.py",
    "tests/test_opportunity_browsing_postgres.py",
    "tests/test_free_core_funding_period.py",
    "tests/test_free_core_funding_period_postgres.py",
    "tests/test_unified_analysis_cancellation.py",
    "tests/test_learning_resource_delivery.py",
    "tests/test_native_expense_reconciliation.py",
    "tests/test_curated_learning_reuse.py",
    "tests/test_credit_input_unsupported.py",
)
PROTOCOL_SUITES = (
    "tests/test_notification_digest_emulator.py",
    "tests/test_notification_digest_native.py",
    "tests/test_notification_schema26_reader_compatibility.py",
    "tests/test_notification_mail_imports.py",
    "tests/test_notification_preference_admission.py",
    "tests/test_notification_preferences_projection.py",
    "tests/test_notification_digest_content.py",
    "tests/test_notification_schema27_contract.py",
    "tests/test_notification_consent_contract.py",
    "tests/test_digest_unsubscribe.py",
    "tests/test_neon_schema27_release_transport.py",
)
SUITES = {"compatibility": COMPATIBILITY_SUITES, "protocol": PROTOCOL_SUITES}
REQUIRED_CASES = {
    "compatibility": {
        ("test_notification_schema27_step_postgres", "test_additive27_exact_catalog_acl_and_old_rows_fence_survive"): 1,
        ("test_notification_schema27_step_postgres", "test_live_writer_fk_lock_timeout_is_atomic_no_migration_replay"): 1,
        ("test_notification_schema27_step_postgres", "test_current26_migration_advisory_lock_blocks27_before_any_ddl"): 1,
        ("test_schema27_native_migration", "test_real_pg_lost_commit_ack_joins_only_exact_same_transaction_never_ddl_again"): 1,
        ("test_schema27_native_migration", "test_real_pg_current_invocation_known_commit_ack_then26_restore_is_contradiction"): 1,
        ("test_notification_schema_compat_postgres", "test_inflight_schema26_privacy_token_completes_on_exact26_or27_with_original_audit"): 2,
        ("test_digest_delivery_integration", "test_api_consent_to_real_native_and_postgres_budget_to_intercepted_http_once"): 1,
        ("test_schema27_app_compatibility_postgres", "test_actual_prior26_and_current27_processes_exchange_preserved_storage"): 2,
        ("test_interview_input_postgres", "test_final_interview_snapshot_serializes_real_mutations_through_credit_commit"): 6,
        ("test_interview_input_postgres", "test_preexisting_writer_order_declines_without_deadlock_provider_replay_or_charge"): 2,
        ("test_interview_input_postgres", "test_retained_mismatched_resume_refuses_before_supplier_and_refunds"): 1,
        ("test_credit_release_margin_gate", "test_launch_review_cannot_relax_either_approved_margin_floor"): 3,
        ("test_credit_release_margin_gate", "test_commercial_policy_guard_also_denies_direct_execution_without_a_release_review"): 3,
        ("test_credit_release_margin_gate", "test_production_runtime_rejects_floor_before_canary_audience_or_database"): 3,
        ("test_payment_funding_postgres", "test_actual_pg_union_projection_is_single_statement_and_owner_detached"): 1,
        ("test_payment_funding_postgres", "test_actual_pg_competing_loss_allocations_cannot_spend_original_source_twice"): 2,
        ("test_payment_expenses", "test_unified_only_unlinked_call_uses_tied_original_conversion_and_linked_id_excluded"): 1,
        ("test_payment_expenses", "test_actual_owner_erasure_detaches_but_cannot_remove_refund_cost_facts"): 1,
        ("test_payment_funding", "test_source_reference_cannot_be_redeclared_to_reset_or_increase_capacity"): 1,
        ("test_payment_funding", "test_changed_preimage_needs_explicit_review_then_only_incremental_capacity"): 1,
        ("test_payment_funding", "test_no_new_allocation_from_expired_funding_review_but_renewal_preserves_consumption"): 1,
        ("test_unified_payment_expense_admission", "test_new_order_pauses_before_quote_acceptance_or_provider_io"): 1,
        ("test_unified_payment_expense_admission", "test_new_paid_hold_refuses_before_credit_or_dispatch_reservation"): 1,
        ("test_unified_payment_expense_admission", "test_capture_refund_and_duplicates_settle_while_new_admission_paused"): 1,
        ("test_unified_payment_expense_admission", "test_real_receipt_funded_loss_reopens_new_checkout_after_capture_and_refund"): 1,
        ("test_payment_funding_operator", "test_lost_commit_ack_reconciles_without_replay"): 1,
        ("test_payment_funding_operator", "test_known_commit_then_restored_missing_audit_is_denied"): 1,
        ("test_payment_funding_operator", "test_clock_refresh_after_native_gate_stops_expired_admission"): 1,
        ("test_payment_funding_operator", "test_clock_refresh_after_money_lock_stops_before_first_mutation"): 1,
        ("test_payment_funding_operator", "test_prepare_uses_post_gate_creation_clock"): 1,
        ("test_payment_funding_operator", "test_actual_boundary_requires_native_gate_distinct27_and_original_receipt"): 3,
        ("test_opportunity_browsing", "test_new_page_avoids_actual_legacy_body_prefix_and_finds_role201"): 1,
        ("test_opportunity_browsing", "test_default_page_is_bounded_closed_metadata_with_one_auth_two_sql"): 1,
        ("test_opportunity_browsing", "test_tied_times_deleted_anchor_and_newest_insert_do_not_break_older_traversal"): 1,
        ("test_opportunity_browsing", "test_cursor_is_owner_bound_tamper_evident_and_not_a_workspace_token"): 1,
        ("test_opportunity_browsing", "test_stage_global_counts_and_existing_archive_semantics"): 1,
        ("test_opportunity_browsing", "test_actual_http_retains_existing_auth_and_feature_gate"): 1,
        ("test_opportunity_browsing_postgres", "test_actual_pg_literal_filters_counts_keyset_owner_and_metadata_only"): 1,
        ("test_free_core_funding_period", "test_review_renewal_cannot_spend_the_same_settled_allocation_twice"): 1,
        ("test_free_core_funding_period", "test_partial_measured_cost_uses_actual_without_resetting_on_review_renewal"): 1,
        ("test_free_core_funding_period", "test_old_unknown_cost_carries_forward_once_in_same_or_new_period"): 2,
        ("test_free_core_funding_period", "test_settled_other_period_is_not_charged_against_reviewed_new_allocation"): 1,
        ("test_free_core_funding_period", "test_new_funding_period_does_not_reset_rolling_global_operation_quota"): 1,
        ("test_free_core_funding_period", "test_new_funding_period_does_not_reset_one_parse_per_account_quota"): 1,
        ("test_free_core_funding_period", "test_new_period_is_not_permission_to_bypass_the_common_cash_budget"): 1,
        ("test_free_core_funding_period", "test_detached_settled_expense_still_consumes_the_same_period"): 1,
        ("test_free_core_funding_period", "test_measured_overrun_remains_spent_after_policy_version_renewal"): 1,
        ("test_free_core_funding_period_postgres", "test_reviewed_period_allocation_is_atomic_across_two_owners"): 2,
        ("test_unified_analysis_cancellation", "test_acknowledged_cancel_after_budget_entry_refuses_next_request"): 2,
        ("test_unified_analysis_cancellation", "test_omitted_run_id_does_not_bypass_cancelled_source"): 2,
        ("test_unified_analysis_cancellation", "test_cancel_before_repair_preserves_prior_cost_and_blocks_next_attempt"): 2,
        ("test_unified_analysis_cancellation", "test_original_inflight_completion_survives_cancel_and_customer_refund"): 2,
        ("test_unified_analysis_cancellation", "test_budget_rejects_cross_bound_or_missing_analysis_source"): 8,
        ("test_unified_analysis_cancellation", "test_terminal_run_cannot_admit_new_generation"): 3,
        ("test_unified_analysis_cancellation", "test_explicit_other_run_id_is_refused_and_true_nonrun_budget_stays_supported"): 1,
        ("test_unified_analysis_cancellation", "test_real_analysis_admission_and_budget_bind_before_late_cancel"): 1,
        ("test_unified_analysis_cancellation", "test_actual_pg_queued_cancel_run_lock_wins_without_owner_lock_deadlock"): 1,
        ("test_unified_analysis_cancellation", "test_actual_pg_claim_commit_wins_then_cancel_keeps_unknown_supplier_cost"): 1,
        ("test_unified_analysis_cancellation", "test_cancelled_settlement_is_idempotent_and_cannot_mean_delivered"): 1,
        ("test_unified_analysis_cancellation", "test_actual_worker_cancellations_do_not_trip_real_failure_circuit"): 2,
        ("test_native_expense_reconciliation", "test_cancelled_product_still_needs_independent_terminal_native_receipt"): 2,
        ("test_learning_resource_delivery", "test_actual_http_curated_does_not_publish_unreviewed_catalogue_url"): 1,
        ("test_learning_resource_delivery", "test_actual_http_gaps_does_not_publish_unreviewed_compatibility_courses"): 1,
        ("test_learning_resource_delivery", "test_funded_free_saved_replay_refreshes_links_without_new_hold_charge_or_payload_change"): 2,
        ("test_learning_resource_delivery", "test_malformed_reviews_fail_closed_urls_without_editing_prose"): 6,
        ("test_learning_resource_delivery", "test_aliases_are_currently_reviewed_but_catalogue_replacements_never_rewrite_saved_urls"): 1,
        ("test_learning_resource_delivery", "test_partial_review_compatibility_gaps_retains_only_exact_reviewed_catalogue_link"): 1,
        ("test_learning_resource_delivery", "test_paid_saved_replay_and_generic_get_preserve_receipt_and_charge_with_partial_revocation"): 1,
        ("test_learning_resource_delivery", "test_invalid_review_dates_or_evidence_cannot_publish_links"): 4,
        ("test_learning_resource_delivery", "test_actual_http_legacy_unavailable_generation_fallback_projects_resource_links"): 1,
        ("test_learning_resource_delivery", "test_generic_legacy_learning_is_projected_but_unrelated_results_are_unchanged"): 1,
        ("test_credit_input_unsupported", "test_actual_http_absent_approved_fact_is_explicit_user_refusal"): 1,
        ("test_credit_input_unsupported", "test_actual_http_complete_analysis_prompt_over_envelope_is_unsupported"): 2,
        ("test_credit_input_unsupported", "test_actual_http_complete_matching_context_over_envelope_is_unsupported"): 1,
        ("test_credit_input_unsupported", "test_actual_http_cross_owner_input_still_returns404_without_writes"): 1,
        ("test_credit_input_unsupported", "test_actual_http_disabled_runtime_refuses_before_source_inspection"): 1,
        ("test_credit_input_unsupported", "test_actual_http_expired_qualification_remains503_even_with_oversized_source"): 1,
        ("test_credit_input_unsupported", "test_actual_http_invalid_route_arity_is_service_pause_not_unsupported"): 1,
        ("test_credit_input_unsupported", "test_actual_http_missing_measurement_bound_is_service_pause"): 1,
        ("test_credit_input_unsupported", "test_actual_http_other_measured_overflow_is_unsupported"): 2,
        ("test_credit_input_unsupported", "test_actual_http_owned_source_over_qualified_size_is_unsupported_without_writes"): 1,
        ("test_credit_input_unsupported", "test_actual_http_product_pause_precedes_unsupported_advice_for_both_quote_families"): 2,
        ("test_credit_input_unsupported", "test_actual_http_source_refusal_is_fixed_safe_but_processing_outage_stays503"): 2,
        ("test_credit_input_unsupported", "test_actual_http_unsupported_source_format_refuses_before_preparation"): 1,
        ("test_credit_input_unsupported", "test_invalid_server_measurement_is_not_a_user_input_error"): 4,
        ("test_curated_learning_reuse", "test_changed_current_resume_or_match_does_not_reuse_old_plan"): 4,
        ("test_curated_learning_reuse", "test_context_builder_preserves_original_curated_hash"): 1,
        ("test_curated_learning_reuse", "test_foreign_and_missing_matches_are_the_same_owned_404"): 2,
        ("test_curated_learning_reuse", "test_funded_saved_plan_is_readable_without_current_optional_capacity"): 3,
        ("test_curated_learning_reuse", "test_lookup_never_substitutes_other_or_unavailable_stored_runs"): 6,
        ("test_curated_learning_reuse", "test_missing_saved_curated_plan_is_a_null_read_not_a_creation"): 1,
        ("test_curated_learning_reuse", "test_saved_links_receive_fresh_projection_without_mutating_stored_plan"): 1,
        ("test_curated_learning_reuse", "test_saved_lookup_requires_positive_match_id"): 4,
    },
    "protocol": {
        ("test_notification_digest_emulator", "test_real_emulator_consent_send_and_complete_census"): 1,
        ("test_notification_digest_emulator", "test_real_emulator_optout_and_session_revocation_prevent_new_send"): 1,
        ("test_notification_digest_emulator", "test_real_emulator_rolled_back_consent_head_cannot_hide_history"): 1,
        ("test_notification_schema26_reader_compatibility", "test_actual_old_reader_accepts_existing_rows_then_refuses_valid_new_digest_record"): 1,
        ("test_notification_mail_imports", "test_fresh_pure_import_has_no_sql_configuration_orm_or_network"): 3,
    },
}
MAX_REPORT_BYTES = 4 * 1024**2
CHILD_SECONDS = 900


class Schema27QualificationDenied(RuntimeError):
    pass


def need(value, reason):
    if not value:
        raise Schema27QualificationDenied("schema27_qualification_" + reason)


def environment(source, *, platform):
    from scripts.qualify_schema26_retained_v3 import _environment

    result = _environment(source, platform=platform)
    need(source.get("HIREWIZ_APP_COMPATIBILITY_REQUIRED") == "true"
         and source.get("HIREWIZ_AUTHORITY_EMULATOR_REQUIRED") == "true", "mandatory_context")
    return result


def source_identity(root):
    """Consume the explicit current application role, never the held native runner."""
    import unified_credit_schema25_release as release

    from evaluation.native_acquisition_v2.qualification import current_application_identity
    from scripts import schema27_app_release_contract as contract

    need(Path(release.__file__).resolve() == root / "infra/gcp/unified_credit_schema25_release.py",
         "release_import_origin")
    need(Path(contract.__file__).resolve() == root / "backend/scripts/schema27_app_release_contract.py",
         "contract_import_origin")
    prior = release.prior_app_files(root, contract.PRIOR_RELEASE)
    stamp = contract.source_fingerprint(root, prior)
    identity = current_application_identity(root, family=FAMILY)
    validate_application_identity(identity, contract, stamp)
    return stamp


def validate_application_identity(identity, contract, stamp):
    source = identity.get("application_source", {})
    need(identity.get("family") == source.get("family") == FAMILY
         and source.get("qualification_sha256") == stamp
         and (source.get("app_sha256"), source.get("app_files")) ==
             (contract.CANDIDATE_APP_SHA256, contract.CANDIDATE_APP_FILES), "current_application_binding")
    need(identity.get("source_only") is True
         and all(identity.get(key) is False for key in ("native_runner_eligible", "retained_producer_proof",
             "native_approval", "processing_authorized", "authorizes_retail")), "non_native_application_role")


def public_cases(root, phase):
    need(phase in SUITES, "phase_required")
    result = {}
    for suite in SUITES[phase]:
        path = root / "backend" / suite
        need(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= 1024**2,
             "suite_source_required")
        tree = ast.parse(path.read_bytes())
        names = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name.startswith("test")}
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names.update(alias.asname or alias.name for alias in node.names
                             if (alias.asname or alias.name).startswith("test"))
        need(names, "suite_cases_required")
        result[path.stem] = names
    return result


def validate_report(path, *, phase, allowed):
    need(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= MAX_REPORT_BYTES,
         "report_required")
    raw = path.read_bytes()
    need(b"<!doctype" not in raw.lower() and b"<!entity" not in raw.lower(), "report_invalid")
    try:
        cases = ElementTree.fromstring(raw).findall(".//testcase")
    except ElementTree.ParseError:
        raise Schema27QualificationDenied("schema27_qualification_report_invalid") from None
    need(0 < len(cases) <= 4096, "report_cases_required")
    seen: set[tuple[str, str]] = set()
    counts: dict[tuple[str, str], int] = {}
    for case in cases:
        module, full_name = case.get("classname", "").split(".")[-1], case.get("name", "")
        name = full_name.split("[", 1)[0]
        need(module in allowed and name in allowed[module] and len(full_name) <= 65536,
             "public_case_required")
        key = (module, full_name)
        need(key not in seen, "duplicate_case")
        seen.add(key)
        need(not any(case.find(kind) is not None for kind in ("skipped", "failure", "error")),
             "every_case_must_pass")
        counts[module, name] = counts.get((module, name), 0) + 1
    need(all(any(module == key[0] for key in counts) for module in allowed), "suite_missing")
    need(all(counts.get(key) == count for key, count in REQUIRED_CASES[phase].items()),
         "mandatory_cases_missing")
    return len(cases)


def run(root, phase):
    need(phase in SUITES, "phase_required")
    env = environment(dict(os.environ), platform=sys.platform)
    stamp = source_identity(root)
    allowed = public_cases(root, phase)
    from tests.expense_policy_fixtures import synthetic_expense_policy
    from tests.model_cost_fixtures import synthetic_policy

    env.update(HIREWIZ_METADATA_PACING_REQUIRED="true",
               HIREWIZ_EXPENSE_POLICY_JSON=json.dumps(synthetic_expense_policy()),
               LLM_MODEL_COST_POLICY_JSON=json.dumps(synthetic_policy()))
    parent = Path(env["RUNNER_TEMP"])
    directory = Path(tempfile.mkdtemp(prefix="hirewiz-schema27-" + phase + "-", dir=parent))
    directory.chmod(0o700)
    before = directory.stat()
    owner = (before.st_dev, before.st_ino, before.st_uid)
    report = directory / "junit.xml"
    try:
        result = subprocess.run([sys.executable, "-I", "-B", "-m", "pytest", "--noconftest", "-q",
            *SUITES[phase], "--junitxml", str(report)], cwd=root / "backend", env=env,
            stdin=subprocess.DEVNULL, capture_output=True, timeout=CHILD_SECONDS, check=False)
    except BaseException:
        raise Schema27QualificationDenied("schema27_qualification_child_outcome_unknown") from None
    # No raw child diagnostic, request, account, output, or assertion value is published.
    need(result.returncode == 0, "child_failed")
    need(len(result.stdout) <= MAX_REPORT_BYTES and len(result.stderr) <= MAX_REPORT_BYTES, "output_bound")
    count = validate_report(report, phase=phase, allowed=allowed)
    after = directory.stat()
    need(not directory.is_symlink() and stat.S_IMODE(after.st_mode) == 0o700
         and (after.st_dev, after.st_ino, after.st_uid) == owner
         and set(directory.iterdir()) == {report}, "report_custody_changed")
    need(source_identity(root) == stamp, "source_changed_during_child")
    report.unlink()
    directory.rmdir()
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True, choices=tuple(SUITES))
    arguments = parser.parse_args()
    try:
        count = run(ROOT, arguments.phase)
    except Exception:
        raise SystemExit("schema27_qualification_unavailable_or_child_outcome_unknown") from None
    print(f"Schema27 {arguments.phase} passed: {count} original cases; no rollout authority.")
