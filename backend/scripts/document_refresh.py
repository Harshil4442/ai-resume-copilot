"""Build immutable scanner candidates or audit serving definition expiry.

The refresh mode builds a fixed-renderer definitions-only successor, requires
new native required-case acceptance, and performs a bounded fail-closed worker/caller
pin transition. Monitor and candidate modes grant no promotion. IAM provisioning
is separate. Vendor output stays in RAM; unknown writes never authorize replay.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import io
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import time
import uuid
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
# File-mode maintenance starts with backend/scripts on sys.path. Resolve local
# package imports from this trusted source root before loading serving readers.
sys.path.insert(0, str(ROOT))
PROJECT, REGION, SERVICE = "ai-resume-parser-482412", "us-central1", "hirewiz-document-inspector"
REPOSITORY = f"{REGION}-docker.pkg.dev/{PROJECT}/cloud-run-source-deploy/{SERVICE}"
RENDERER_BASE = REPOSITORY + "@sha256:0a7e550623f0485945f9bbe52573091aabedfd612eb353e6a8bbb920e2ffa94f"
BUILDER = f"hirewiz-builder-20261014@{PROJECT}.iam.gserviceaccount.com"
DOCKER_BUILDER = "gcr.io/cloud-builders/docker@sha256:40c2fb4fcd0ad51376eef166c2e7b2b40a3508d5776e2bf33db3783ab39d0f2e"
AGE_LIMIT = timedelta(days=3)
ALERT_BEFORE = timedelta(hours=24)
CANDIDATE_MAX_AGE = timedelta(hours=24)
MAX_OUTPUT = 1024 * 1024

# Exact source-owned codes safe for the private final-continuation failure
# artifact. Vendor exception text and response bodies never enter this set.
FINAL_READ_ONLY_FAILURE_CODES = frozenset("""
    document_final_readback_claim_changed
    document_final_readback_coordinator_changed
    document_final_readback_exact_artifact_required
    document_final_readback_exact_candidate_required
    document_final_readback_exact_mode_required
    document_final_readback_failed_journal_changed
    document_final_readback_failure_receipt_changed
    document_final_readback_flags_changed
    document_final_readback_native_acceptance_changed
    document_final_readback_original_flags_changed
    document_final_readback_prior_changed
    document_final_readback_private_artifact_required
    document_final_readback_request_changed
    document_final_readback_revision_changed
    document_final_readback_source_tree_changed
    document_final_readback_state_changed
    document_final_readback_write_route_refused
    document_final_analysis_admission_changed
    document_final_analysis_chain_changed
    document_final_analysis_open_changed
    document_final_analysis_open_revision_changed
    document_final_analysis_pins_changed
    document_final_analysis_request_changed
    document_final_claim_ack_changed
    document_final_claim_exists_or_unknown_reconcile_only
    document_final_claim_preimages_changed
    document_final_claim_readback_changed
    document_final_claim_unknown_reconcile_only
    document_final_coordinator_admission_changed
    document_final_exact_artifact_required
    document_final_native_acceptance_changed
    document_final_original_flags_changed
    document_final_preclaim_state_changed
    document_final_previous_journal_changed
    document_final_prewrite_read_deadline_reconcile_only
    document_final_prewrite_state_changed
    document_final_prior_claim_changed
    document_final_second_claim_changed
    document_final_second_claim_preimage_changed
    document_final_state_changed
    document_native_build_image_changed
    document_native_caller_pin_unverified
    document_native_caller_policy_unverified
    document_native_caller_profile_worker_changed
    document_native_cleanup_absence_unverified
    document_native_cleanup_resource_remains
    document_native_cleanup_unknown
    document_native_cleanup_unknown_reconcile_only
    document_native_client_mode_invalid
    document_native_complete_logs_unavailable
    document_native_contract_version_invalid
    document_native_create_binding_changed
    document_native_create_not_terminal
    document_native_create_uid_unverified
    document_native_current_execution_missing
    document_native_current_execution_reference_invalid
    document_native_current_job_must_be_retained
    document_native_current_labels_invalid
    document_native_current_service_changed
    document_native_current_transient_exhausted
    document_native_current_verification_unavailable
    document_native_deadline_reconcile_only
    document_native_definition_invalid
    document_native_definitions_expired
    document_native_event_scope_or_uniqueness_invalid
    document_native_event_time_invalid
    document_native_event_window_invalid
    document_native_execution_not_singleton
    document_native_execution_not_successful
    document_native_existing_journal_reconcile_only
    document_native_fresh_job_absence_unverified
    document_native_historical_create_refused
    document_native_historical_renderer_profile_refused
    document_native_job_changed
    document_native_job_exists_reconcile_only
    document_native_job_previously_run
    document_native_journal_binding_changed
    document_native_journal_bound
    document_native_journal_file_invalid
    document_native_legacy_source_changed
    document_native_legacy_source_invalid
    document_native_log_page_bound
    document_native_log_pagination_invalid
    document_native_maintenance_authority_unverified
    document_native_nonce_invalid
    document_native_operation_changed
    document_native_operation_failed_reconcile_only
    document_native_operation_name_invalid
    document_native_outcome_unknown_reconcile_only
    document_native_parser_changed
    document_native_payload_body_changed
    document_native_payload_changed
    document_native_payload_image_literal_changed
    document_native_payload_shape_changed
    document_native_policy_changed
    document_native_private_journal_required
    document_native_readonly_transient_exhausted
    document_native_readonly_verification_unavailable
    document_native_release_mutation_refused
    document_native_renderer_build_profile_changed
    document_native_renderer_or_fonts_changed
    document_native_renderer_profile_invalid
    document_native_required_cases_not_complete
    document_native_required_event_missing
    document_native_required_receipt_invalid
    document_native_required_v2
    document_native_response_bound
    document_native_retirement_mode_invalid
    document_native_retirement_proof_unavailable
    document_native_retirement_requires_expired_v2
    document_native_retirement_requires_v2
    document_native_run_binding_changed
    document_native_run_not_terminal
    document_native_run_uid_unverified
    document_native_runtime_broad_authority_refused
    document_native_runtime_identity_changed
    document_native_runtime_inventory_changed
    document_native_runtime_policy_invalid
    document_native_runtime_source_changed
    document_native_scan_metadata_disagrees
    document_native_sdk_redirect_budget_invalid
    document_native_sdk_required
    document_native_serving_image_changed
    document_native_serving_not_ready
    document_native_serving_policy_unverified
    document_native_serving_revision_changed
    document_native_serving_revision_unverified
    document_native_serving_traffic_changed
    document_native_six_case_v1
    document_native_source_unavailable
    document_native_successor_image_required
    document_native_v2_successor_required_for_retirement
    document_native_worker_configuration_changed
    document_native_worker_image_unverified
    document_native_worker_invokers_changed
    document_partial_api_maintenance_close_changed
    document_partial_api_maintenance_operation_changed
    document_partial_api_open_chain_changed
    document_partial_api_open_operation_changed
    document_partial_api_open_revision_changed
    document_partial_api_open_template_changed
    document_partial_api_pins_changed
    document_partial_caller_admission_changed
    document_partial_claim_ack_changed
    document_partial_claim_exists_or_unknown_reconcile_only
    document_partial_claim_preimages_changed
    document_partial_claim_readback_changed
    document_partial_claim_unknown_reconcile_only
    document_partial_exact_artifact_required
    document_partial_final_join_changed
    document_partial_native_acceptance_changed
    document_partial_open_revision_already_exists
    document_partial_operation_marker_changed
    document_partial_operation_unresolved
    document_partial_original_claim_changed
    document_partial_original_flags_changed
    document_partial_preclaim_state_changed
    document_partial_prewrite_state_changed
    document_partial_serving_phase_changed
    document_partial_source_file_changed
    document_partial_source_name_refused
    document_partial_verified_marker_changed
    document_refresh_identity_transient_exhausted_reconcile_only
    document_refresh_read_only_transient_exhausted_reconcile_only
    document_transition_ack_unknown_reconcile_only
    document_transition_action_refused
    document_transition_admission_flag_unverified
    document_transition_already_current_reconcile_only
    document_transition_caller_identity_changed
    document_transition_container_changed
    document_transition_current_acceptance_required
    document_transition_deadline_reconcile_only
    document_transition_environment_changed
    document_transition_existing_journal_reconcile_only
    document_transition_existing_mixed_pins_reconcile_only
    document_transition_final_join_changed
    document_transition_handle_changed
    document_transition_handle_refused
    document_transition_immutable_image_required
    document_transition_journal_bound
    document_transition_journal_name_refused
    document_transition_journal_unverified
    document_transition_native_metadata_invalid
    document_transition_native_outcome_unknown_reconcile_only
    document_transition_operation_failed_reconcile_only
    document_transition_operation_refused
    document_transition_pin_refused
    document_transition_preimage_changed
    document_transition_prewrite_read_deadline_reconcile_only
    document_transition_private_journal_required
    document_transition_private_rescue_journal_required
    document_transition_readback_changed
    document_transition_readback_deadline_reconcile_only
    document_transition_rescue_acceptance_changed
    document_transition_rescue_candidate_changed
    document_transition_rescue_claim_ack_changed
    document_transition_rescue_claim_exists_or_unknown_reconcile_only
    document_transition_rescue_claim_preimages_changed
    document_transition_rescue_claim_readback_changed
    document_transition_rescue_claim_unknown_reconcile_only
    document_transition_rescue_close_admission_changed
    document_transition_rescue_close_journal_changed
    document_transition_rescue_close_operation_unresolved
    document_transition_rescue_close_readback_changed
    document_transition_rescue_close_verification_changed
    document_transition_rescue_flags_changed
    document_transition_rescue_guarded_api_changed
    document_transition_rescue_journal_file_changed
    document_transition_rescue_journal_scope_changed
    document_transition_rescue_mode_invalid
    document_transition_rescue_predecessor_changed
    document_transition_rescue_upload_preimage_changed
    document_transition_rescue_v2_predecessor_required
    document_transition_revision_read_deadline_reconcile_only
    document_transition_revision_refused
    document_transition_service_identity_changed
    document_transition_service_refused
    document_transition_serving_template_changed
    document_transition_unrelated_configuration_changed
    document_transition_worker_environment_changed
    document_transition_worker_policy_unverified
document_refresh_auth
document_refresh_build_deadline_reconcile_only
document_refresh_build_failed_reconcile_only
document_refresh_build_identity_changed
document_refresh_build_nonce_invalid
document_refresh_build_outcome_unknown_reconcile_only
document_refresh_build_recipe_changed
document_refresh_built_header_mismatch
document_refresh_candidate_not_fresh
document_refresh_clamd_incomplete
document_refresh_contract
document_refresh_contract_unavailable
document_refresh_contract_version_invalid
document_refresh_corpus_incomplete
document_refresh_definition_disagreement
document_refresh_definition_header_invalid
document_refresh_duplicate_json_key
document_refresh_expired_v2_requires_dispatch
document_refresh_expired_v2_requires_explicit_refresh
document_refresh_expiry_alert
document_refresh_explicit_native_build_required
document_refresh_final
document_refresh_fixed_base_changed
document_refresh_fresh_output_directory_required
document_refresh_future_definitions_refused
document_refresh_historical_source_changed
document_refresh_immutable_image_required
document_refresh_layout_incomplete
document_refresh_local_proof_unverified
document_refresh_metadata_bound
document_refresh_metadata_invalid
document_refresh_module_refused
document_refresh_module_unavailable
document_refresh_monitor_status_refused
document_refresh_native_build_execution_inputs_changed
document_refresh_native_build_identity_unverified
document_refresh_native_build_image_unverified
document_refresh_native_build_invalid
document_refresh_native_build_readback_changed
document_refresh_native_build_steps_changed
document_refresh_native_definition_time_invalid
document_refresh_native_operation_unavailable_reconcile_only
document_refresh_native_source_changed
document_refresh_native_source_corpus_incomplete
document_refresh_native_source_unavailable
document_refresh_native_source_unverified
document_refresh_nonfinite_json
document_refresh_operation_unavailable
document_refresh_partial
document_refresh_prior_journal_bound
document_refresh_prior_journal_refused
document_refresh_prior_owned_history_unavailable
document_refresh_probe
document_refresh_pulled_digest_unverified
document_refresh_recovery
document_refresh_recovery_requires_explicit_refresh
document_refresh_renderer_build_profile_changed
document_refresh_renderer_changed_review_required
document_refresh_resume
document_refresh_serving_not_ready
document_refresh_serving_pin_drift
document_refresh_serving_successor_unverified
document_refresh_serving_traffic_drift
document_refresh_source_archive_bound
document_refresh_source_archive_changed
document_refresh_source_changed
document_refresh_source_upload_identity_unverified
document_refresh_source_upload_unknown_reconcile_only
document_refresh_transition
document_refresh_transition_journal_required
document_refresh_unavailable
document_rescue_exact_candidate_required
document_rescue_exact_transition_mode_required
document_rescue_original_artifact_private_required
document_rescue_original_journal_changed
document_rescue_prior_build_changed
document_rescue_prior_history_required
document_rescue_prior_job_changed
document_rescue_prior_journal_invalid
document_rescue_prior_owned_journal_required
document_rescue_transition_arguments_mode_required
document_resume_arguments_mode_required
document_resume_original_candidate_required
document_resume_other_history_required
document_source_archive_bound
document_source_archive_changed
document_source_file_bound
document_source_historical_profile_refused
document_source_manifest_invalid
document_source_path_invalid
document_source_policy_changed
document_source_recipe_changed
""".split())


class RefreshDenied(RuntimeError):
    """Fixed nonsecret refusal; never echo vendor output or exception text."""


def need(value: Any, code: str) -> None:
    if not value:
        raise RefreshDenied(code)


def accepted() -> Any:
    spec = importlib.util.spec_from_file_location("document_refresh_contract", ROOT / "infra/gcp/document_worker.py")
    need(spec is not None and spec.loader is not None, "document_refresh_contract_unavailable")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def command(argv: list[str], *, seconds: int) -> bytes:
    try:
        result = subprocess.run(argv, capture_output=True, timeout=seconds, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise RefreshDenied("document_refresh_operation_unavailable") from None
    need(result.returncode == 0 and len(result.stdout) <= MAX_OUTPUT,
         "document_refresh_operation_unavailable")
    return result.stdout


def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        need(key not in result, "document_refresh_duplicate_json_key")
        result[key] = value
    return result


def decode(raw: bytes) -> Any:
    need(type(raw) is bytes and 0 < len(raw) <= MAX_OUTPUT, "document_refresh_metadata_bound")
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                         parse_constant=lambda _: (_ for _ in ()).throw(RefreshDenied("document_refresh_nonfinite_json")))
    except (ValueError, UnicodeError, RecursionError):
        raise RefreshDenied("document_refresh_metadata_invalid") from None


def definition(raw: bytes, *, now: datetime) -> dict[str, Any]:
    """Same official header format and three-day limit as the request scanner."""
    need(type(raw) is bytes and len(raw) <= 256 and now.tzinfo is not None,
         "document_refresh_definition_header_invalid")
    try:
        match = re.fullmatch(r"ClamAV (1\.4\.3)/([0-9]{1,10})/([A-Za-z0-9 :]{24})", raw.decode("ascii").strip())
        need(match is not None, "document_refresh_definition_header_invalid")
        assert match is not None
        issued = datetime.strptime(match[3], "%a %b %d %H:%M:%S %Y").replace(tzinfo=UTC)
    except (ValueError, UnicodeError):
        raise RefreshDenied("document_refresh_definition_header_invalid") from None
    remaining = issued + AGE_LIMIT - now
    need(remaining <= AGE_LIMIT, "document_refresh_future_definitions_refused")
    return {"engine": "ClamAV", "version": match[1], "definitions": match[2],
            "issued_at_utc": issued.isoformat(), "expires_at_utc": (issued + AGE_LIMIT).isoformat(),
            "seconds_remaining": int(remaining.total_seconds()),
            "status": "expired" if remaining < timedelta(0) else "refresh_required" if remaining <= ALERT_BEFORE else "fresh"}


def header(docker: list[str], image: str) -> bytes:
    need(re.fullmatch(r"sha256:[a-f0-9]{64}", image) is not None
         or re.fullmatch(re.escape(REPOSITORY) + r"@sha256:[a-f0-9]{64}", image) is not None,
         "document_refresh_immutable_image_required")
    return command(docker + ["run", "--rm", "--network=none", "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges", "--pids-limit=32", "--memory=256m", "--cpus=0.5",
        "--entrypoint=/usr/bin/clamscan", image, "--database=/var/lib/clamav", "--version"], seconds=30)


def _serving_pin(service: Any, contract: Any) -> tuple[str, Any]:
    """Bind the native v2 serving revision; every successor needs fresh proof."""
    need(type(service) is dict and service.get("name") == f"projects/{PROJECT}/locations/{REGION}/services/{SERVICE}"
         and service.get("uid") == "7014a5ba-faee-4fbf-8272-a437b1de49ad"
         and re.fullmatch(r"[1-9][0-9]{0,18}", str(service.get("generation", "")))
         and str(service.get("generation")) == str(service.get("observedGeneration"))
         and service.get("reconciling", False) is False
         and service.get("terminalCondition", {}).get("state") == "CONDITION_SUCCEEDED",
         "document_refresh_serving_not_ready")
    ready = service.get("latestReadyRevision")
    need(type(ready) is str and ready == service.get("latestCreatedRevision")
         and re.fullmatch(re.escape(service["name"] + "/revisions/" + SERVICE) + r"-[a-z0-9-]{1,36}", ready),
         "document_refresh_serving_not_ready")
    containers = service.get("template", {}).get("containers", [])
    need(type(containers) is list and len(containers) == 1, "document_refresh_serving_pin_drift")
    image = containers[0].get("image", "")
    need(re.fullmatch(re.escape(REPOSITORY) + r"@sha256:[a-f0-9]{64}", image),
         "document_refresh_serving_pin_drift")
    traffic = service.get("trafficStatuses")
    need(type(traffic) is list and len(traffic) == 1 and type(traffic[0].get("percent")) is int
         and traffic[0]["percent"] == 100 and traffic[0].get("revision") in {ready, ready.rsplit("/", 1)[-1]}
         and not traffic[0].get("tag"), "document_refresh_serving_traffic_drift")
    verified = None
    if image != contract.ACCEPTED_IMAGE:
        sys.path.insert(0, str(ROOT / "infra/gcp"))
        from document_refresh_probe import verify_current_successor

        verified = verify_current_successor(image, service)
        need(verified.image == image, "document_refresh_serving_successor_unverified")
    return image, verified


def serving_image(service: Any, contract: Any) -> str:
    return _serving_pin(service, contract)[0]


def monitor(*, now: datetime) -> dict[str, Any]:
    contract = accepted()
    http = native_http()
    try:
        service = native_json(http, "GET", f"https://run.googleapis.com/v2/projects/{PROJECT}/locations/{REGION}/services/{SERVICE}")
        image, native = _serving_pin(service, contract)
    finally:
        http.close()
    if native is None:
        # Only the initial accepted base lacks this successor's durable native
        # definition receipt. Do not download a multi-GB image every hour after
        # refresh: immutable native proof already binds its exact definitions.
        command(["docker", "pull", image], seconds=180)
        result = definition(header(["docker"], image), now=now)
    else:
        expires = datetime.fromisoformat(native.definition_expires_at)
        need(expires.tzinfo is not None and expires - now <= AGE_LIMIT,
             "document_refresh_native_definition_time_invalid")
        issued, remaining = expires - AGE_LIMIT, expires - now
        result = {"status": "expired" if remaining < timedelta(0) else
                  "refresh_required" if remaining <= ALERT_BEFORE else "fresh",
                  "issued_at_utc": issued.isoformat(), "expires_at_utc": expires.isoformat(),
                  "seconds_remaining": int(remaining.total_seconds()), "source": "fresh_native_immutable_image_receipt"}
    return {"mode": "monitor", "image": image, "native_serving_pin_observed": True,
            "definitions": result, "promotion_performed": False}


def proof_contract(proof: Any, contract: Any, source_hashes: dict[str, str], *, now: datetime,
                   historical_v1: bool = False, image: str | None = None,
                   build_identity: dict[str, Any] | None = None, source_revision: bool = False) -> dict[str, Any]:
    need(type(historical_v1) is bool and type(source_revision) is bool and not (historical_v1 and source_revision),
         "document_refresh_contract_version_invalid")
    policy = contract.POLICY_SHA256
    renderer_version, font_manifest = contract.ACCEPTED_RENDERER, contract.ACCEPTED_FONT_MANIFEST
    if source_revision:
        candidate = infra("document_candidate")
        policy = infra("document_source_candidate").POLICY_SHA256
        renderer_version, font_manifest = candidate.RENDERER, candidate.FONT
    profile = infra("document_renderer_profiles") if image is not None or build_identity is not None else None
    need(build_identity is None or type(build_identity) is dict
         and image == build_identity.get("immutable_image"), "document_refresh_renderer_build_profile_changed")
    if profile is not None and (image == profile.IMAGE or profile.uses_profile(build_identity)):
        need(not historical_v1 and profile.uses_profile(build_identity)
             and source_hashes == profile.SOURCES, "document_refresh_renderer_build_profile_changed")
        renderer_version, font_manifest = profile.RENDERER, profile.FONT
    if historical_v1:
        # A retained failed V1 probe is read-only recovery evidence. It cannot
        # satisfy today's candidate or native V2 acceptance contract.
        sys.path.insert(0, str(ROOT / "infra/gcp"))
        from document_refresh_probe import LEGACY_POLICY, legacy_source

        need(source_hashes == json.loads(legacy_source("policy.json"))["source_sha256"],
             "document_refresh_historical_source_changed")
        policy = LEGACY_POLICY
    need(build_identity is None or build_identity.get("policy_sha256", policy) == policy,
         "document_refresh_candidate_renderer_review_required")
    need(type(proof) is dict and proof.get("platform") == "linux/amd64"
         and proof.get("scope") == "local_native_docker_only"
         and proof.get("cloud_run_isolation_proven") is False
         and proof.get("runtime_source_bytes_verified") is True
         and proof.get("restricted_container_metadata_verified") is True
         and proof.get("source_sha256") == source_hashes
         and re.fullmatch(r"sha256:[a-f0-9]{64}", proof.get("image_id", "")),
         "document_refresh_local_proof_unverified")
    corpus = proof.get("corpus", [])
    cases = {"clean_pdf", "clean_docx", "page_counter", "active_pdf", "includetext", "eicar_exact_file",
             "eicar_zip_member", "missing_definitions"}
    if not historical_v1:
        cases.update({"clean_tex", "clean_texzip", "eicar_tex_source", "eicar_texzip_member"})
    need(type(corpus) is list and len(corpus) == len(cases)
         and {row.get("case") for row in corpus} == cases
         and all(row.get("passed") is True for row in corpus), "document_refresh_corpus_incomplete")
    need(all(row.get("outcome") == "clean" and row.get("native_source_empty") is True
             for row in corpus if row["case"] in {"clean_tex", "clean_texzip"})
         and all(row.get("outcome") == "detected" for row in corpus
                 if row["case"] in {"eicar_tex_source", "eicar_texzip_member"}),
         "document_refresh_native_source_corpus_incomplete")
    layouts = proof.get("docx_layout_corpus", [])
    need(type(layouts) is list and len(layouts) == (3 if source_revision else 2)
         and {row.get("case") for row in layouts} == ({"styled_multipage_fit", "rendered_reflow_refusal", "unsupported_source_font_refusal"}
             if source_revision else {"styled_multipage_fit", "rendered_reflow_refusal"})
         and all(row.get("passed") is True for row in layouts), "document_refresh_layout_incomplete")
    fit = next(row for row in layouts if row["case"] == "styled_multipage_fit")
    renderer = fit.get("layout", {}).get("renderer", {})
    need(type(renderer) is dict and renderer.get("renderer_version") == renderer_version
         and renderer.get("font_manifest_sha256") == font_manifest
         and renderer.get("policy_sha256") == policy
         and renderer.get("image_digest") == "local-document-image@" + proof["image_id"]
         and re.fullmatch(r"[a-f0-9]{64}", fit.get("layout", {}).get("rendered_fonts_sha256", "")),
         "document_refresh_renderer_changed_review_required")
    if source_revision:
        from infra.gcp.document_source_payload import EXTRA_CASES
        reading = proof.get("pdf_reading_order_corpus", [])
        need(type(reading) is list and len(reading) == len(EXTRA_CASES)
             and tuple(row.get("case") for row in reading) == EXTRA_CASES
             and all(row.get("passed") is True for row in reading), "document_refresh_reading_order_incomplete")
    clamd = proof.get("request_clamd_corpus", [])
    need(type(clamd) is list and len(clamd) == 11
         and len({row.get("case") for row in clamd}) == 11
         and all(row.get("daemon_stopped_before_writer") is True and row.get("native_log_removed") is True
                 and row.get("daemon_returncode") == 0 for row in clamd), "document_refresh_clamd_incomplete")
    scans = [row["scan"] for row in corpus if row.get("scan")]
    need(bool(scans) and all(row == scans[0] for row in scans), "document_refresh_definition_disagreement")
    try:
        number, issued = scans[0]["definitions"].split("/", 1)
        timestamp = datetime.fromisoformat(issued)
        need(timestamp.tzinfo is not None and re.fullmatch(r"[0-9]{1,10}", number),
             "document_refresh_definition_header_invalid")
        age = now - timestamp
    except (KeyError, TypeError, ValueError):
        raise RefreshDenied("document_refresh_definition_header_invalid") from None
    need(timedelta(0) <= age < CANDIDATE_MAX_AGE, "document_refresh_candidate_not_fresh")
    return {"issued_at_utc": timestamp.isoformat(), "expires_at_utc": (timestamp + AGE_LIMIT).isoformat(),
            "definition_number": number, "local_cases": 21 if historical_v1 else 31 if source_revision else 25,
            "renderer_version": renderer_version,
            "rendered_fonts_sha256": fit["layout"]["rendered_fonts_sha256"]}


def write_new(path: Path, value: Any) -> str:
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return hashlib.sha256(raw).hexdigest()


# The old one-file refresh profile remains unchanged. This distinct profile
# publishes current parser/validator source; its native V2 proof is still separate.
FULL_SOURCE_KIND = "native_cloud_build_full_source_v1"
# Keep the existing narrowly authorized nonce-owned source namespace.
# Exact Build/result tags and steps distinguish the two finite profiles.
FULL_SOURCE_PREFIX = "hirewiz-document-refresh-source/"
FULL_SOURCE_POLICY = "infra/document-processing/policy.json"
FULL_SOURCE_DOCKERFILE = "infra/document-processing/Dockerfile"
FULL_SOURCE_MAX_BYTES = 4 * 1024 * 1024


def full_source_snapshot(*, source_revision: bool = False) -> tuple[dict[str, bytes], dict[str, str], str]:
    """Exactly the source-only candidate inventory and its separately pinned policy."""
    sys.path.insert(0, str(ROOT / "backend"))
    from scripts import document_test_image as engines

    need(type(source_revision) is bool, "document_refresh_contract_version_invalid")
    candidate = infra("document_candidate")
    need(set(engines.SOURCES) == candidate.SOURCES
         and len(engines.SOURCES) == len(candidate.SOURCES)
         and engines.RUNTIME == candidate.RUNTIME
         and len(set(engines.RUNTIME.values())) == len(engines.RUNTIME),
         "document_source_manifest_invalid")
    if source_revision:
        return infra("document_source_candidate").working_snapshot(ROOT)
    contents = {}
    for name in (*engines.SOURCES, FULL_SOURCE_POLICY):
        path = ROOT
        for component in name.split("/"):
            need(component not in {"", ".", ".."}, "document_source_path_invalid")
            path = path / component
            need(not path.is_symlink(), "document_source_path_invalid")
        need(path.is_file() and 0 < path.stat().st_size <= 1024 * 1024,
             "document_source_file_bound")
        contents[name] = path.read_bytes()
    hashes = {name: hashlib.sha256(contents[name]).hexdigest() for name in engines.SOURCES}
    policy = contents[FULL_SOURCE_POLICY]
    policy_value = decode(policy)
    need(type(policy_value) is dict and policy_value.get("source_sha256") == hashes
         and hashlib.sha256(policy).hexdigest() == candidate.POLICY_SHA256,
         "document_source_policy_changed")
    need(sum(map(len, contents.values())) <= FULL_SOURCE_MAX_BYTES,
         "document_source_archive_bound")
    return contents, hashes, hashlib.sha256(policy).hexdigest()


def accepted_source_hashes() -> dict[str, str]:
    """The frozen V2 inventory; current candidate source cannot rewrite it."""
    profile = infra("document_renderer_profiles")
    path = ROOT / "infra/gcp/document_accepted_v2/policy.json"
    need(not path.is_symlink(), "document_refresh_accepted_policy_changed")
    raw = path.read_bytes()
    need(0 < len(raw) <= MAX_OUTPUT and hashlib.sha256(raw).hexdigest() == profile.BUILD["policy_sha256"],
         "document_refresh_accepted_policy_changed")
    value = decode(raw)
    need(value.get("source_sha256") == profile.SOURCES, "document_refresh_accepted_policy_changed")
    return dict(profile.SOURCES)


def _full_source_tar(*, source_revision: bool = False, source_commit: str | None = None, frozen_v3: bool = False) -> bytes:
    if frozen_v3:
        need(not source_revision and source_commit is None, "document_refresh_contract_version_invalid")
        contents = infra("document_source_candidate").historical_v3_contents(ROOT)
    elif source_commit is not None:
        need(source_revision, "document_refresh_contract_version_invalid")
        profile = infra("document_source_candidate")
        current = profile.committed(source_commit, root=ROOT)
        contents = {name: profile.git(ROOT, "show", source_commit + ":" + name) for name in current.sources}
        contents[profile.POLICY_PATH] = current.policy_raw
    else:
        contents, _, _ = full_source_snapshot(source_revision=source_revision)
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, content in sorted(contents.items()):
            row = tarfile.TarInfo(name)
            row.size, row.mode, row.mtime, row.uid, row.gid = len(content), 0o644, 0, 0, 0
            archive.addfile(row, io.BytesIO(content))
    raw = output.getvalue()
    need(len(raw) <= FULL_SOURCE_MAX_BYTES, "document_source_archive_bound")
    return raw


def full_source_archive(*, source_revision: bool = False, source_commit: str | None = None) -> bytes:
    import gzip

    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as zipped:
        zipped.write(_full_source_tar(source_revision=source_revision, source_commit=source_commit))
    raw = output.getvalue()
    need(len(raw) <= FULL_SOURCE_MAX_BYTES, "document_source_archive_bound")
    return raw


def archive_full_source(raw: bytes, *, source_revision: bool = False, source_commit: str | None = None, frozen_v3: bool = False) -> str:
    """Compare bounded bytes with canonical source; never parse untrusted tar."""
    import zlib

    need(type(raw) is bytes and 0 < len(raw) <= FULL_SOURCE_MAX_BYTES,
         "document_source_archive_bound")
    expected = _full_source_tar(source_revision=source_revision, source_commit=source_commit, frozen_v3=frozen_v3)
    try:
        decoder = zlib.decompressobj(wbits=31)
        actual = decoder.decompress(raw, len(expected) + 1)
        need(decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail
             and actual == expected, "document_source_archive_changed")
    except zlib.error:
        raise RefreshDenied("document_source_archive_changed") from None
    return hashlib.sha256(raw).hexdigest()


def full_source_build_request(nonce: str, source: dict[str, str], *, historical_profile: bool = False,
                              source_revision: bool = False, source_commit: str | None = None, frozen_v3: bool = False) -> dict[str, Any]:
    need(type(nonce) is str and re.fullmatch(r"[a-f0-9]{32}", nonce),
         "document_refresh_build_nonce_invalid")
    need(type(historical_profile) is bool, "document_refresh_contract_version_invalid")
    need(type(source_revision) is bool and type(frozen_v3) is bool
         and sum((historical_profile, source_revision, frozen_v3)) <= 1, "document_refresh_contract_version_invalid")
    current = None
    if source_revision:
        current = infra("document_source_candidate").committed(source_commit, root=ROOT, clean=source_commit is None)
        hashes = current.sources
    elif frozen_v3:
        candidate_release = infra("document_candidate_release")
        need(nonce == candidate_release.BUILD_NONCE, "document_refresh_renderer_build_profile_changed")
        hashes = infra("document_source_candidate").frozen_v3(ROOT)[1]["source_sha256"]
    elif historical_profile:
        profile = infra("document_renderer_profiles")
        need(nonce == profile.BUILD["nonce"], "document_refresh_renderer_build_profile_changed")
        hashes = profile.SOURCES
    else:
        _, hashes, _ = full_source_snapshot()
    tag = REPOSITORY + (":source-revision-" if source_revision else ":source-") + nonce
    body = build_request(nonce, source)
    arguments = ["build", "--pull=false", "--platform=linux/amd64", "--build-arg",
                 "DOCUMENT_DEFINITIONS_NONCE=" + nonce, "--file", FULL_SOURCE_DOCKERFILE]
    for name, digest in sorted(hashes.items()):
        arguments.extend(("--label", "hirewiz.document-test.source." + name + "=" + digest))
    if current is not None:
        arguments.extend(("--label", "hirewiz.document-source.commit=" + current.commit,
                          "--label", "hirewiz.document-source.qualification=" + current.qualification_sha256))
        body["tags"] = [infra("document_source_candidate").KIND, current.commit, current.qualification_sha256]
    arguments.extend(("--tag", tag, "."))
    body.update(steps=[{"name": DOCKER_BUILDER, "args": arguments},
                       {"name": DOCKER_BUILDER, "args": ["push", tag]}], images=[tag])
    return body


def source_definitions_build_request(nonce: str, source: dict[str, str], commit: str | None, parent: str | None) -> dict[str, Any]:
    need(type(commit) is str and type(parent) is str, "document_source_recipe_changed")
    assert isinstance(parent, str)
    current = infra("document_source_candidate").committed(commit, root=ROOT)
    infra("document_source_candidate").definition_recipe(parent, ROOT)
    body = build_request(nonce, source)
    tag = REPOSITORY + ":source-definitions-" + nonce
    body.update(steps=[{"name": DOCKER_BUILDER, "args": ["build", "--pull=false", "--platform=linux/amd64", "--build-arg",
        "DOCUMENT_DEFINITIONS_NONCE=" + nonce, "--label", "hirewiz.document-source.commit=" + current.commit,
        "--label", "hirewiz.document-source.qualification=" + current.qualification_sha256, "--tag", tag, "."]},
        {"name": DOCKER_BUILDER, "args": ["push", tag]}], images=[tag],
        tags=[infra("document_source_candidate").DEFINITIONS_KIND, current.commit, current.qualification_sha256,
              parent.split("@sha256:", 1)[1]])
    return body


def build_contract(value: Any, nonce: str, recipe_sha: str, *, historical_v1: bool = False,
                   full_source: bool = False, historical_profile: bool = False,
                   candidate_v3: bool = False, source_revision: bool = False,
                   source_commit: str | None = None, frozen_v3: bool = False,
                   source_definitions: bool = False, source_parent: str | None = None) -> dict[str, Any]:
    """Check actual native Build fields; a caller-supplied summary is insufficient."""
    need(type(value) is dict and re.fullmatch(r"[a-f0-9]{32}", nonce), "document_refresh_native_build_invalid")
    build_id = value.get("id", "")
    need(re.fullmatch(r"[a-f0-9-]{36}", build_id) and value.get("projectId") == PROJECT
         and value.get("name") in {f"projects/{PROJECT}/locations/{REGION}/builds/{build_id}",
             f"projects/157225724590/locations/{REGION}/builds/{build_id}"}
         and value.get("status") == "SUCCESS" and value.get("serviceAccount") in {
             f"projects/{PROJECT}/serviceAccounts/{BUILDER}", f"projects/157225724590/serviceAccounts/{BUILDER}"},
         "document_refresh_native_build_identity_unverified")
    need(type(full_source) is bool and not (historical_v1 and full_source), "document_refresh_contract_version_invalid")
    need(type(historical_profile) is bool and (not historical_profile or full_source),
         "document_refresh_contract_version_invalid")
    need(type(candidate_v3) is bool and (not candidate_v3 or not (historical_v1 or full_source or historical_profile)),
         "document_refresh_contract_version_invalid")
    need(type(source_revision) is bool and type(frozen_v3) is bool
         and (not source_revision or full_source and not (historical_v1 or historical_profile or candidate_v3 or frozen_v3))
         and (not frozen_v3 or full_source and not (historical_v1 or historical_profile or candidate_v3)),
         "document_refresh_contract_version_invalid")
    need(type(source_definitions) is bool and (not source_definitions or not (full_source or source_revision
         or candidate_v3 or historical_v1 or historical_profile or frozen_v3)), "document_refresh_contract_version_invalid")
    tag = REPOSITORY + (":source-definitions-" if source_definitions else ":source-revision-" if source_revision
                        else ":source-" if full_source else ":refresh-") + nonce
    expected = [["build", "--pull=false", "--platform=linux/amd64", "--build-arg",
        "DOCUMENT_DEFINITIONS_NONCE=" + nonce, "--tag", tag, "."], ["push", tag]]
    if full_source:
        expected = [row["args"] for row in full_source_build_request(
            nonce, {}, historical_profile=historical_profile, source_revision=source_revision,
            source_commit=source_commit, frozen_v3=frozen_v3)["steps"]]
        if source_revision:
            need(value.get("tags") == full_source_build_request(nonce, {}, source_revision=True,
                 source_commit=source_commit)["tags"], "document_refresh_native_build_steps_changed")
    if source_definitions:
        wanted = source_definitions_build_request(nonce, {}, source_commit, source_parent)
        expected = [row["args"] for row in wanted["steps"]]
        need(value.get("tags") == wanted["tags"], "document_refresh_native_build_steps_changed")
    steps = value.get("steps")
    need(type(steps) is list and len(steps) == 2 and all(
        row.get("name") == DOCKER_BUILDER and row.get("args") == args
        and row.get("dir", "") in ("", ".") and not row.get("env") and not row.get("secretEnv")
        and not row.get("volumes") and not row.get("script") and not row.get("entrypoint")
        and not row.get("automapSubstitutions") and not row.get("allowFailure") and not row.get("allowExitCodes")
        for row, args in zip(steps, expected, strict=True)), "document_refresh_native_build_steps_changed")
    options = value.get("options", {})
    need(type(options) is dict and options.get("logging") == "CLOUD_LOGGING_ONLY"
         and value.get("timeout") == "900s"
         and not any(options.get(key) for key in ("env", "secretEnv", "volumes", "automapSubstitutions", "pool"))
         and not any(value.get(key) for key in ("availableSecrets", "secrets", "substitutions", "automapSubstitutions")),
         "document_refresh_native_build_execution_inputs_changed")
    images = value.get("results", {}).get("images", [])
    need(type(images) is list and len(images) == 1 and images[0].get("name") == tag
         and re.fullmatch(r"sha256:[a-f0-9]{64}", images[0].get("digest", "")),
         "document_refresh_native_build_image_unverified")
    source = value.get("source", {}).get("storageSource", {})
    resolved = value.get("sourceProvenance", {}).get("resolvedStorageSource", {})
    need(source == resolved and source.get("bucket") == PROJECT + "_cloudbuild"
         and type(source.get("object")) is str
         and source["object"] == (FULL_SOURCE_PREFIX if full_source else "hirewiz-document-refresh-source/") + nonce + "/source.tar.gz"
         and re.fullmatch(r"[1-9][0-9]{0,19}", str(source.get("generation", ""))),
         "document_refresh_native_source_unverified")
    renderer_base = RENDERER_BASE
    if candidate_v3:
        candidate_profile = infra("document_candidate")
        need(recipe_sha == candidate_profile.REFRESH_SHA256, "document_source_recipe_changed")
        renderer_base = candidate_profile.IMAGE
    if historical_v1:
        sys.path.insert(0, str(ROOT / "infra/gcp"))
        from document_refresh_probe import LEGACY_BASE_IMAGE

        renderer_base = LEGACY_BASE_IMAGE
    result = {"version": 1, "kind": infra("document_source_candidate").DEFINITIONS_KIND if source_definitions else infra("document_source_candidate").KIND if source_revision else FULL_SOURCE_KIND if full_source else "native_cloud_build", "nonce": nonce, "build_id": build_id,
        "build_resource": value.get("name"), "builder_service_account": BUILDER,
        "source_bucket": source["bucket"], "source_object": source["object"],
        "source_generation": str(source["generation"]), "refresh_dockerfile_sha256": recipe_sha,
        "fixed_renderer_base": renderer_base, "immutable_image": REPOSITORY + "@" + images[0]["digest"]}
    if source_definitions:
        source_profile = infra("document_source_candidate")
        current = source_profile.committed(source_commit, root=ROOT)
        need(recipe_sha == hashlib.sha256(source_profile.definition_recipe(source_parent, ROOT)).hexdigest(),
             "document_source_recipe_changed")
        result.update(fixed_renderer_base=source_parent, policy_sha256=current.policy,
            source_commit=current.commit, qualification_sha256=current.qualification_sha256)
    if candidate_v3:
        need(recipe_sha == hashlib.sha256((ROOT / "infra/document-processing/refresh-courier-v3.Dockerfile").read_bytes()).hexdigest(),
             "document_source_recipe_changed")
        result["policy_sha256"] = candidate_profile.POLICY_SHA256
    if full_source:
        if historical_profile:
            profile = infra("document_renderer_profiles")
            policy = profile.BUILD["policy_sha256"]
            need(recipe_sha == profile.BUILD["refresh_dockerfile_sha256"], "document_source_recipe_changed")
        elif frozen_v3:
            _, frozen = infra("document_source_candidate").frozen_v3(ROOT)
            policy = infra("document_candidate").POLICY_SHA256
            need(recipe_sha == frozen["source_sha256"][FULL_SOURCE_DOCKERFILE], "document_source_recipe_changed")
        elif source_revision:
            current = infra("document_source_candidate").committed(source_commit, root=ROOT)
            policy = current.policy
            need(recipe_sha == current.sources[FULL_SOURCE_DOCKERFILE], "document_source_recipe_changed")
            result.update(source_commit=current.commit, qualification_sha256=current.qualification_sha256)
        else:
            contents, _, policy = full_source_snapshot()
            need(recipe_sha == hashlib.sha256(contents[FULL_SOURCE_DOCKERFILE]).hexdigest(),
                 "document_source_recipe_changed")
        # Existing acceptance receipts retain their historical recipe-key name.
        # Full-source builds do not claim to reuse the old renderer image.
        result.update(fixed_renderer_base=None, policy_sha256=policy)
        if historical_profile:
            need(result == {key: value for key, value in profile.BUILD.items()
                            if key != "source_archive_sha256"},
                 "document_refresh_renderer_build_profile_changed")
    return result


def archive_recipe(raw: bytes, expected_sha: str) -> str:
    need(type(raw) is bytes and 0 < len(raw) <= 262144, "document_refresh_source_archive_bound")
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            rows = archive.getmembers()
            need(len(rows) == 1 and rows[0].isfile() and rows[0].name in {"Dockerfile", "./Dockerfile"}
                 and 0 < rows[0].size <= 16384, "document_refresh_source_archive_changed")
            stream = archive.extractfile(rows[0])
            need(stream is not None, "document_refresh_source_archive_changed")
            assert stream is not None
            need(hashlib.sha256(stream.read(16385)).hexdigest() == expected_sha,
                 "document_refresh_source_archive_changed")
    except (tarfile.TarError, OSError):
        raise RefreshDenied("document_refresh_source_archive_changed") from None
    return hashlib.sha256(raw).hexdigest()


def native_http():
    sys.path.insert(0, str(ROOT / "infra/gcp"))
    from document_refresh_auth import ServiceHttp

    return ServiceHttp(os.environ.get("DOCUMENT_REFRESH_CONTROLLER_UID", ""))


def native_json(http: Any, method: str, uri: str, body: Any = None) -> Any:
    raw = None if body is None else json.dumps(body, separators=(",", ":"), allow_nan=False).encode()
    response, content = http.request(uri, method=method, body=raw,
        headers={"Content-Type": "application/json"}, redirections=0)
    need(response.status in (200, 201), "document_refresh_native_operation_unavailable_reconcile_only")
    return decode(content)


def source_archive(recipe: bytes) -> bytes:
    import gzip

    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode="w") as archive:
            row = tarfile.TarInfo("Dockerfile")
            row.size, row.mode, row.mtime, row.uid, row.gid = len(recipe), 0o644, 0, 0, 0
            archive.addfile(row, io.BytesIO(recipe))
    return output.getvalue()


def read_native_source(http: Any, identity: dict[str, Any]) -> bytes:
    from urllib.parse import quote, urlencode

    uri = "https://storage.googleapis.com/download/storage/v1/b/" + identity["source_bucket"] + "/o/" \
        + quote(identity["source_object"], safe="") + "?" + urlencode({"alt": "media", "generation": identity["source_generation"]})
    response, raw = http.request(uri, method="GET", redirections=0)
    need(response.status == 200, "document_refresh_native_source_unavailable")
    return raw


def build_request(nonce: str, source: dict[str, str]) -> dict[str, Any]:
    tag = REPOSITORY + ":refresh-" + nonce
    return {"source": {"storageSource": source}, "serviceAccount": f"projects/{PROJECT}/serviceAccounts/{BUILDER}",
        "steps": [{"name": DOCKER_BUILDER, "args": ["build", "--pull=false", "--platform=linux/amd64", "--build-arg",
            "DOCUMENT_DEFINITIONS_NONCE=" + nonce, "--tag", tag, "."]},
            {"name": DOCKER_BUILDER, "args": ["push", tag]}], "images": [tag],
        "options": {"logging": "CLOUD_LOGGING_ONLY"}, "timeout": "900s"}


def native_build(directory: Path, recipe: bytes, *, nonce: str | None = None, full_source: bool = False,
                 candidate_v3: bool = False, source_revision: bool = False, source_definitions: bool = False,
                 source_parent: str | None = None) -> dict[str, Any]:
    from urllib.parse import urlencode

    nonce = uuid.uuid4().hex if nonce is None else nonce
    need(type(nonce) is str and re.fullmatch(r"[a-f0-9]{32}", nonce), "document_refresh_build_nonce_invalid")
    need(type(full_source) is bool, "document_refresh_contract_version_invalid")
    need(type(candidate_v3) is bool and not (candidate_v3 and full_source), "document_refresh_contract_version_invalid")
    if candidate_v3:
        need(recipe == (ROOT / "infra/document-processing/refresh-courier-v3.Dockerfile").read_bytes(),
             "document_source_recipe_changed")
        need(hashlib.sha256(recipe).hexdigest() == infra("document_candidate").REFRESH_SHA256, "document_source_recipe_changed")
    need(type(source_revision) is bool and (not source_revision or full_source and not candidate_v3),
         "document_refresh_contract_version_invalid")
    need(type(source_definitions) is bool and (not source_definitions or not (full_source or candidate_v3 or source_revision)),
         "document_refresh_contract_version_invalid")
    current = infra("document_source_candidate").committed(root=ROOT, clean=True) if source_revision or source_definitions else None
    source_commit = current.commit if current is not None else None
    if source_definitions:
        need(recipe == infra("document_source_candidate").definition_recipe(source_parent, ROOT), "document_source_recipe_changed")
        infra("document_source_release").parent_before_build(source_parent, current)
    recipe_sha = hashlib.sha256(recipe).hexdigest()
    if full_source:
        contents, _, _ = full_source_snapshot(source_revision=source_revision)
        need(recipe == contents[FULL_SOURCE_DOCKERFILE], "document_source_recipe_changed")
    archive = full_source_archive(source_revision=source_revision, source_commit=source_commit) if full_source else source_archive(recipe)
    archive_sha = archive_full_source(archive, source_revision=source_revision, source_commit=source_commit) if full_source else archive_recipe(archive, recipe_sha)
    if current is not None:
        need(infra("document_source_candidate").committed(root=ROOT, clean=True).identity == current.identity,
             "document_refresh_source_changed")
    source_name = (FULL_SOURCE_PREFIX if full_source else "hirewiz-document-refresh-source/") + nonce + "/source.tar.gz"
    write_new(directory / "build-started.json", {"nonce": nonce, "recipe_sha256": recipe_sha,
        "source_archive_sha256": archive_sha, "automatic_replay": False,
        "unknown_outcome": "reconcile_exact_native_object_and_build_only"})
    http = native_http()
    try:
        uri = "https://storage.googleapis.com/upload/storage/v1/b/" + PROJECT + "_cloudbuild/o?" + urlencode({
            "uploadType": "media", "name": source_name, "ifGenerationMatch": "0"})
        response, raw = http.request(uri, method="POST", body=archive,
            headers={"Content-Type": "application/gzip"}, redirections=0)
        need(response.status in (200, 201), "document_refresh_source_upload_unknown_reconcile_only")
        obj = decode(raw)
        need(obj.get("name") == source_name and obj.get("bucket") == PROJECT + "_cloudbuild"
             and re.fullmatch(r"[1-9][0-9]{0,19}", str(obj.get("generation", ""))),
             "document_refresh_source_upload_identity_unverified")
        source = {"bucket": obj["bucket"], "object": obj["name"], "generation": str(obj["generation"])}
        write_new(directory / "source-upload-acknowledged.json", source)
        body = (full_source_build_request(nonce, source, source_revision=source_revision, source_commit=source_commit) if full_source
                else source_definitions_build_request(nonce, source, source_commit, source_parent) if source_definitions else build_request(nonce, source))
        write_new(directory / "build-create-started.json", {"nonce": nonce,
            "request_sha256": hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "automatic_replay": False})
        operation = native_json(http, "POST", f"https://cloudbuild.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/builds", body)
        build_id = operation.get("metadata", {}).get("build", {}).get("id", "")
        need(re.fullmatch(r"[a-f0-9-]{36}", build_id), "document_refresh_build_outcome_unknown_reconcile_only")
        write_new(directory / "build-acknowledged.json", {"nonce": nonce, "build_id": build_id,
            "operation_name": operation.get("name"), "automatic_replay": False})
        deadline = time.monotonic() + 960
        while True:
            value = native_json(http, "GET", f"https://cloudbuild.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/builds/{build_id}")
            need(value.get("id") == build_id, "document_refresh_build_identity_changed")
            if value.get("status") == "SUCCESS":
                break
            need(value.get("status") in {"PENDING", "QUEUED", "WORKING"}, "document_refresh_build_failed_reconcile_only")
            need(time.monotonic() < deadline, "document_refresh_build_deadline_reconcile_only")
            time.sleep(min(10, max(0, deadline - time.monotonic())))
        identity = (build_contract(value, nonce, recipe_sha, full_source=True, source_revision=source_revision, source_commit=source_commit) if full_source
                    else build_contract(value, nonce, recipe_sha, candidate_v3=candidate_v3,
                         source_definitions=source_definitions, source_parent=source_parent, source_commit=source_commit))
        stored_source = read_native_source(http, identity)
        native_archive_sha = archive_full_source(stored_source, source_revision=source_revision, source_commit=source_commit) if full_source else archive_recipe(stored_source, recipe_sha)
        need(native_archive_sha == archive_sha, "document_refresh_native_source_changed")
        identity["source_archive_sha256"] = native_archive_sha
        if source_revision or source_definitions:
            assert current is not None
            identity["source_sha256"] = current.sources
        elif candidate_v3:
            identity["source_sha256"] = infra("document_source_candidate").frozen_v3(ROOT)[1]["source_sha256"]
        write_new(directory / "native-build-identity.json", identity)
        return identity
    finally:
        http.close()


def verify_native_build(identity: dict[str, Any], *, historical_v1: bool = False) -> dict[str, Any]:
    """Fresh native Build+generation-bound source read; zero provider mutations."""
    keys = {"version", "kind", "nonce", "build_id", "build_resource", "builder_service_account",
        "source_bucket", "source_object", "source_generation", "source_archive_sha256",
        "refresh_dockerfile_sha256", "fixed_renderer_base", "immutable_image",
        "source_sha256", "local_native_proof_sha256"}
    source_revision = type(identity) is dict and identity.get("kind") in {infra("document_source_candidate").KIND, infra("document_source_candidate").DEFINITIONS_KIND}
    full_source = type(identity) is dict and identity.get("kind") in {FULL_SOURCE_KIND, infra("document_source_candidate").KIND, infra("document_source_candidate").DEFINITIONS_KIND}
    if source_revision:
        keys.update({"source_commit", "qualification_sha256"})
    if full_source:
        need(not historical_v1, "document_source_historical_profile_refused")
        keys.add("policy_sha256")
    need(type(identity) is dict and set(identity) == keys and type(identity.get("version")) is int
         and identity["version"] == 1 and identity.get("kind") in {"native_cloud_build", FULL_SOURCE_KIND, infra("document_source_candidate").KIND, infra("document_source_candidate").DEFINITIONS_KIND}
         and type(identity.get("build_id")) is str and re.fullmatch(r"[a-f0-9-]{36}", identity["build_id"])
         and type(identity.get("nonce")) is str and re.fullmatch(r"[a-f0-9]{32}", identity["nonce"]),
         "document_refresh_native_build_identity_unverified")
    need(re.fullmatch(r"[a-f0-9]{64}", identity.get("local_native_proof_sha256", "")),
         "document_refresh_build_recipe_changed")
    actual = (verify_current_build(identity["build_id"], identity["nonce"], identity["immutable_image"], historical_v1=True)
              if historical_v1 else verify_current_build(identity["build_id"], identity["nonce"], identity["immutable_image"]))
    need(all(identity[key] == value for key, value in actual.items()),
         "document_refresh_native_build_readback_changed")
    return actual


def verify_current_build(build_id: str, nonce: str, image: str, *, historical_v1: bool = False,
                         candidate_v3: bool = False) -> dict[str, Any]:
    """Dedicated service-identity verifier for scheduled maintenance."""
    return _verify_current_build(build_id, nonce, image, release_read_only=False, historical_v1=historical_v1, candidate_v3=candidate_v3)


def verify_current_build_release(build_id: str, nonce: str, image: str, *, historical_v1: bool = False,
                                 candidate_v3: bool = False) -> dict[str, Any]:
    """Fixed normal-manager read-only entry; no ADC or controller-UID dependency."""
    return _verify_current_build(build_id, nonce, image, release_read_only=True, historical_v1=historical_v1, candidate_v3=candidate_v3)


def _verify_current_build(build_id: str, nonce: str, image: str, *, release_read_only: bool, historical_v1: bool = False,
                          candidate_v3: bool = False) -> dict[str, Any]:
    """Reconstruct current native provenance without asserting a lost local proof."""
    need(type(build_id) is str and re.fullmatch(r"[a-f0-9-]{36}", build_id)
         and type(nonce) is str and re.fullmatch(r"[a-f0-9]{32}", nonce)
         and type(image) is str and re.fullmatch(re.escape(REPOSITORY) + r"@sha256:[a-f0-9]{64}", image),
         "document_refresh_native_build_identity_unverified")
    need(type(historical_v1) is bool, "document_refresh_contract_version_invalid")
    profile = infra("document_renderer_profiles")
    historical_profile = image == profile.IMAGE
    need(type(candidate_v3) is bool and (not candidate_v3 or not (historical_v1 or historical_profile)),
         "document_refresh_contract_version_invalid")
    if historical_profile:
        need(not historical_v1 and build_id == profile.BUILD["build_id"] and nonce == profile.BUILD["nonce"],
             "document_refresh_renderer_build_profile_changed")
    if historical_v1:
        sys.path.insert(0, str(ROOT / "infra/gcp"))
        from document_refresh_probe import legacy_source

        recipe = legacy_source("refresh.Dockerfile")
    elif candidate_v3:
        recipe = (ROOT / "infra/document-processing/refresh-courier-v3.Dockerfile").read_bytes()
    else:
        recipe = (ROOT / "infra/document-processing/refresh.Dockerfile").read_bytes()
    recipe_sha = hashlib.sha256(recipe).hexdigest()
    if release_read_only:
        sys.path.insert(0, str(ROOT / "infra/gcp"))
        from document_refresh_auth import ReleaseReadOnlyHttp

        http = ReleaseReadOnlyHttp()
    else:
        http = native_http()
    try:
        value = native_json(http, "GET", f"https://cloudbuild.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/builds/{build_id}")
        images = value.get("results", {}).get("images", [])
        source_definitions = (type(images) is list and len(images) == 1 and type(images[0]) is dict
                              and images[0].get("name") == REPOSITORY + ":source-definitions-" + nonce)
        source_parent = None
        source_revision = (type(images) is list and len(images) == 1 and type(images[0]) is dict
                           and images[0].get("name") == REPOSITORY + ":source-revision-" + nonce)
        full_source = (source_revision or type(images) is list and len(images) == 1 and type(images[0]) is dict
                       and images[0].get("name") == REPOSITORY + ":source-" + nonce)
        frozen_v3 = full_source and not source_revision and image == infra("document_candidate").IMAGE
        source_commit = None
        if source_revision or source_definitions:
            tags = value.get("tags", [])
            need(type(tags) is list and len(tags) == (4 if source_definitions else 3)
                 and tags[0] == (infra("document_source_candidate").DEFINITIONS_KIND if source_definitions else infra("document_source_candidate").KIND)
                 and type(tags[1]) is str and re.fullmatch(r"[a-f0-9]{40}", tags[1]),
                 "document_refresh_native_build_identity_unverified")
            source_commit = tags[1]
            if source_definitions:
                need(type(tags[3]) is str and re.fullmatch(r"[a-f0-9]{64}", tags[3]), "document_source_recipe_changed")
                source_parent = REPOSITORY + "@sha256:" + tags[3]
                recipe_sha = hashlib.sha256(infra("document_source_candidate").definition_recipe(source_parent, ROOT)).hexdigest()
        need(not (full_source and historical_v1), "document_source_historical_profile_refused")
        need(not (candidate_v3 and full_source), "document_source_historical_profile_refused")
        need(not historical_profile or full_source, "document_refresh_renderer_build_profile_changed")
        if full_source:
            recipe_sha = (profile.BUILD["refresh_dockerfile_sha256"] if historical_profile else
                          infra("document_source_candidate").frozen_v3(ROOT)[1]["source_sha256"][FULL_SOURCE_DOCKERFILE]
                          if frozen_v3 else hashlib.sha256((ROOT / FULL_SOURCE_DOCKERFILE).read_bytes()).hexdigest())
            actual = build_contract(value, nonce, recipe_sha, full_source=True, historical_profile=historical_profile,
                source_revision=source_revision, source_commit=source_commit, frozen_v3=frozen_v3)
        else:
            actual = build_contract(value, nonce, recipe_sha, historical_v1=historical_v1, candidate_v3=candidate_v3,
                source_definitions=source_definitions, source_parent=source_parent, source_commit=source_commit)
        need(actual["immutable_image"] == image, "document_refresh_native_build_readback_changed")
        stored_source = read_native_source(http, actual)
        if historical_profile:
            need(type(stored_source) is bytes and 0 < len(stored_source) <= FULL_SOURCE_MAX_BYTES
                 and hashlib.sha256(stored_source).hexdigest() == profile.BUILD["source_archive_sha256"],
                 "document_source_archive_changed")
            archive_sha = profile.BUILD["source_archive_sha256"]
        else:
            archive_sha = archive_full_source(stored_source, source_revision=source_revision, source_commit=source_commit,
                frozen_v3=frozen_v3) if full_source else archive_recipe(stored_source, recipe_sha)
    finally:
        http.close()
    if historical_v1:
        source_hashes = json.loads(legacy_source("policy.json"))["source_sha256"]
    elif source_revision or source_definitions:
        source_hashes = infra("document_source_candidate").committed(source_commit, root=ROOT).sources
    elif frozen_v3:
        source_hashes = infra("document_source_candidate").frozen_v3(ROOT)[1]["source_sha256"]
    elif candidate_v3:
        source_hashes = infra("document_source_candidate").frozen_v3(ROOT)[1]["source_sha256"]
    elif historical_profile or not full_source:
        source_hashes = accepted_source_hashes()
    else:
        source_hashes = full_source_snapshot()[1]
    actual.update(source_archive_sha256=archive_sha, source_sha256=source_hashes)
    need(image != profile.IMAGE or profile.matches_build(actual),
         "document_refresh_renderer_build_profile_changed")
    return actual


def candidate(directory: Path, *, publish: bool, now: datetime, nonce: str | None = None) -> dict[str, Any]:
    # Import only the existing synthetic build/check helper; never the API app.
    sys.path.insert(0, str(ROOT / "backend"))
    from scripts import document_test_image as engines

    contract = accepted()
    need(publish, "document_refresh_explicit_native_build_required")
    before = engines.source_hashes()
    docker = engines.docker_command()
    # FreshClam changes only the database directory; no apt/compiler/font changes.
    # Never use the previous daily successor as the parent.
    recipe = ROOT / "infra/document-processing/refresh.Dockerfile"
    raw = recipe.read_bytes()
    need(raw.count(RENDERER_BASE.encode()) == 2, "document_refresh_fixed_base_changed")
    recipe_sha = hashlib.sha256(raw).hexdigest()
    identity = native_build(directory, raw) if nonce is None else native_build(directory, raw, nonce=nonce)
    digest = identity["immutable_image"]
    command(docker + ["pull", digest], seconds=180)
    installed = decode(command(docker + ["image", "inspect", digest], seconds=15))
    need(type(installed) is list and len(installed) == 1
         and digest in installed[0].get("RepoDigests", []), "document_refresh_pulled_digest_unverified")
    image = installed[0]["Id"]
    need(recipe.read_bytes() == raw, "document_refresh_source_changed")
    proof = engines.check("linux/amd64", image=image)
    facts = proof_contract(proof, contract, before, now=datetime.now(UTC), image=digest,
                           build_identity={**identity, "source_sha256": before})
    need(engines.source_hashes() == before, "document_refresh_source_changed")
    proof_sha = write_new(directory / "local-native-proof.json", proof)
    image = proof["image_id"]
    observed = definition(header(docker, image), now=datetime.now(UTC))
    need(observed["issued_at_utc"] == facts["issued_at_utc"] and observed["status"] == "fresh",
         "document_refresh_built_header_mismatch")
    identity.update(source_sha256=before, local_native_proof_sha256=proof_sha)
    return {"mode": "candidate", "status": "candidate_requires_native_acceptance", "image_id": image,
            "fixed_renderer_base": RENDERER_BASE, "refresh_dockerfile_sha256": recipe_sha,
            "candidate_registry_digest": digest, "previous_accepted_image": contract.ACCEPTED_IMAGE,
            "build_identity": identity,
            "local_proof_sha256": proof_sha, "freshness": facts, "source_sha256": before,
            "native_isolation_proven": False, "native_corpus_proven": False,
            "font_manifest_native_verified": False, "promotion_performed": False,
            "next_actions": ["native_stage_without_customer_traffic", "native_source_font_and_isolation_acceptance",
                "native_positive_negative_corpus_and_lifecycle_acceptance", "review_worker_and_client_pin_transition",
                "controlled_worker_and_api_rollout", "verify_exact_serving_image_header_and_complete_upload_path"]}


def full_source_candidate(directory: Path, *, publish: bool, now: datetime,
                          nonce: str | None = None, source_revision: bool = False) -> dict[str, Any]:
    """Publish/check exact source only; no Job, deployment, IAM or promotion."""
    sys.path.insert(0, str(ROOT / "backend"))
    from scripts import document_test_image as engines

    need(publish, "document_refresh_explicit_native_build_required")
    need(type(source_revision) is bool, "document_refresh_contract_version_invalid")
    contents, before, policy = full_source_snapshot(source_revision=True) if source_revision else full_source_snapshot()
    options: dict[str, Any] = {"full_source": True, **({"source_revision": True} if source_revision else {})}
    identity = (native_build(directory, contents[FULL_SOURCE_DOCKERFILE], **options)
                if nonce is None else native_build(directory, contents[FULL_SOURCE_DOCKERFILE], nonce=nonce, **options))
    digest = identity["immutable_image"]
    docker = engines.docker_command()
    command(docker + ["pull", digest], seconds=180)
    installed = decode(command(docker + ["image", "inspect", digest], seconds=15))
    need(type(installed) is list and len(installed) == 1 and digest in installed[0].get("RepoDigests", []),
         "document_refresh_pulled_digest_unverified")
    proof = (engines.check("linux/amd64", image=installed[0]["Id"], source_revision=True) if source_revision
             else engines.check("linux/amd64", image=installed[0]["Id"]))
    facts = proof_contract(proof, accepted(), before, now=datetime.now(UTC), image=digest,
                           build_identity={**identity, "source_sha256": before}, source_revision=source_revision)
    after = full_source_snapshot(source_revision=True) if source_revision else full_source_snapshot()
    need(after[1:] == (before, policy), "document_refresh_source_changed")
    proof_sha = write_new(directory / "local-native-proof.json", proof)
    observed = definition(header(docker, proof["image_id"]), now=datetime.now(UTC))
    need(observed["issued_at_utc"] == facts["issued_at_utc"] and observed["status"] == "fresh",
         "document_refresh_built_header_mismatch")
    identity.update(source_sha256=before, local_native_proof_sha256=proof_sha)
    return {"mode": "full-source-candidate", "status": "candidate_requires_native_acceptance",
            "candidate_registry_digest": digest, "image_id": proof["image_id"], "build_identity": identity,
            "source_sha256": before, "policy_sha256": policy, "freshness": facts,
            "local_proof_sha256": proof_sha, "native_isolation_proven": False,
            "native_corpus_proven": False, "promotion_performed": False}


def source_definitions_candidate(directory: Path, *, publish: bool, now: datetime,
                                 parent: str, nonce: str | None = None) -> dict[str, Any]:
    sys.path.insert(0, str(ROOT / "backend"))
    from scripts import document_test_image as engines

    need(publish, "document_refresh_explicit_native_build_required")
    source_profile = infra("document_source_candidate")
    _, before, policy = full_source_snapshot(source_revision=True)
    recipe = source_profile.definition_recipe(parent, ROOT)
    identity = native_build(directory, recipe, nonce=nonce, source_definitions=True, source_parent=parent)
    image = identity["immutable_image"]
    docker = engines.docker_command()
    command(docker + ["pull", image], seconds=180)
    installed = decode(command(docker + ["image", "inspect", image], seconds=15))
    need(type(installed) is list and len(installed) == 1 and image in installed[0].get("RepoDigests", []),
         "document_refresh_pulled_digest_unverified")
    proof = engines.check("linux/amd64", image=installed[0]["Id"], source_revision=True)
    facts = proof_contract(proof, accepted(), before, now=datetime.now(UTC), image=image,
        build_identity=identity, source_revision=True)
    need(full_source_snapshot(source_revision=True)[1:] == (before, policy), "document_refresh_source_changed")
    proof_sha = write_new(directory / "local-native-proof.json", proof)
    observed = definition(header(docker, proof["image_id"]), now=datetime.now(UTC))
    need(observed["issued_at_utc"] == facts["issued_at_utc"] and observed["status"] == "fresh",
         "document_refresh_built_header_mismatch")
    identity.update(local_native_proof_sha256=proof_sha)
    return {"status": "candidate_requires_native_acceptance", "candidate_registry_digest": image,
        "build_identity": identity, "policy_sha256": policy, "source_sha256": before,
        "native_isolation_proven": False, "native_corpus_proven": False, "promotion_performed": False}


def infra(name: str) -> Any:
    need(name in {"document_refresh_probe", "document_refresh_transition", "document_refresh_recovery", "document_candidate_release",
                  "document_renderer_profiles", "document_refresh_resume", "document_candidate",
                  "document_refresh_partial", "document_refresh_final", "document_refresh_final_readback",
                  "document_consumed_native", "document_source_candidate", "document_source_release"},
         "document_refresh_module_refused")
    if name in {"document_source_candidate", "document_source_release"}:
        sys.path.insert(0, str(ROOT))
        return importlib.import_module("infra.gcp." + name)
    spec = importlib.util.spec_from_file_location("hirewiz_" + name, ROOT / "infra/gcp" / (name + ".py"))
    need(spec is not None and spec.loader is not None, "document_refresh_module_unavailable")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def rescue_prior_journal(history: Path, service: dict[str, Any], clients: Any,
                         probe: Any) -> Path:
    """Find the actual old owned Job journal before any new Build or PATCH."""
    need(history.is_absolute() and history.is_dir() and not history.is_symlink(),
         "document_rescue_prior_history_required")
    image = service["template"]["containers"][0]["image"]
    labels = service.get("labels", {})
    paths = []
    for binding in history.glob("*/native-proof/binding.json"):
        need(not binding.is_symlink() and binding.stat().st_size <= MAX_OUTPUT,
             "document_rescue_prior_journal_invalid")
        saved = decode(binding.read_bytes())
        need(type(saved) is dict, "document_rescue_prior_journal_invalid")
        if saved.get("image") == image:
            paths.append((binding.parent, saved))
    need(len(paths) == 1, "document_rescue_prior_owned_journal_required")
    path, saved = paths[0]
    build = saved.get("build_identity")
    need(type(build) is dict and build.get("kind") == "native_cloud_build"
         and build.get("immutable_image") == image
         and build.get("build_id") == labels.get("hirewiz-refresh-build")
         and build.get("nonce") == labels.get("hirewiz-refresh-nonce"),
         "document_rescue_prior_build_changed")
    verify_native_build(build)
    owner = probe.Journal(path, image, build)
    job = probe.owned_job(clients, owner, image, build["nonce"])
    execution = probe.owned_execution(clients, owner, job, image, build["nonce"])
    need(job["name"].rsplit("/", 1)[-1] == labels.get("hirewiz-refresh-job"),
         "document_rescue_prior_job_changed")
    probe.validate_payload(probe.native_payload(clients, job, execution), image,
                           execution["name"], datetime.now(UTC), require_fresh=False,
                           build_identity=build)
    return path


def refresh(directory: Path, *, publish: bool, now: datetime, prior_journal: Path | None = None,
            history: Path | None = None, recover_failed_native_job: str | None = None,
            full_source: bool = False, retire_expired_v2: bool = False,
            rescue_expired_v2: bool = False,
            upgrade_current_v2_to_v3: bool = False, reviewed_application_context: bool = False,
            consumed_history_bundle: Path | None = None, application_context: Any = None,
            consumed_history: Any = None, source_revision: bool = False) -> dict[str, Any]:
    """Full controlled pipeline. No serialized acceptance result grants authority."""
    need(recover_failed_native_job is None or publish is True, "document_refresh_recovery_requires_explicit_refresh")
    need(type(source_revision) is bool and (not source_revision or full_source and publish
         and not (retire_expired_v2 or rescue_expired_v2 or upgrade_current_v2_to_v3)),
         "document_refresh_source_revision_mode_required")
    need(type(full_source) is bool and type(retire_expired_v2) is bool
         and type(rescue_expired_v2) is bool and type(upgrade_current_v2_to_v3) is bool
         and sum((retire_expired_v2, rescue_expired_v2, upgrade_current_v2_to_v3)) <= 1,
         "document_refresh_contract_version_invalid")
    need(not retire_expired_v2 or (publish and not full_source and recover_failed_native_job is None),
         "document_refresh_expired_v2_requires_explicit_v3_refresh")
    need(not rescue_expired_v2 or (publish and not full_source
         and recover_failed_native_job is None and prior_journal is None and history is not None),
         "document_refresh_expired_v2_requires_explicit_refresh")
    need(not upgrade_current_v2_to_v3 or (publish and not full_source and recover_failed_native_job is None),
         "document_refresh_fresh_v2_upgrade_requires_explicit_refresh")
    probe = infra("document_refresh_probe")
    if reviewed_application_context:
        need(not ((full_source and not source_revision) or rescue_expired_v2 or retire_expired_v2 or upgrade_current_v2_to_v3)
             and application_context is None and consumed_history is None,
             "document_refresh_reviewed_app_context_mode_required")
        boundary_module = infra("document_consumed_native")
        required = boundary_module.history.required(history)
        need(not required or consumed_history_bundle is not None and consumed_history_bundle.is_dir(),
             "document_refresh_exact_consumed_history_bundle_required")
        if required:
            boundary_module.history.load_bundle(consumed_history_bundle)
        clients, delegate = probe.Clients(), None
        try:
            delegate = native_http()
            boundary = boundary_module.NativeAudit(ROOT, consumed_history_bundle if required else None,
                                                   clients, delegate, probe)
            if required:
                assert consumed_history_bundle is not None
                disposition = boundary_module.history.audit(consumed_history_bundle, boundary)
                write_new(directory / "consumed-history-audit.json", disposition.report())
            else:
                disposition = None
                original = boundary.verify_current_applications()
                boundary.verify_current_document(None)
                need(boundary.verify_current_applications() == original,
                     "document_refresh_current_app_context_changed")
            return refresh(directory, publish=publish, now=now, prior_journal=prior_journal,
                history=history, recover_failed_native_job=recover_failed_native_job,
                application_context=boundary.applications, consumed_history=disposition,
                full_source=full_source, source_revision=source_revision)
        finally:
            clients.close()
            if delegate is not None:
                delegate.close()
    need(consumed_history_bundle is None, "document_refresh_reviewed_app_context_mode_required")
    need(not source_revision or application_context is not None,
         "document_refresh_source_revision_app_context_required")
    if rescue_expired_v2:
        clients = probe.Clients()
        try:
            service = clients.preflight()["service"]
            image = service["template"]["containers"][0]["image"]
            probe.verify_expired_current_v2_for_retirement(image, service)
            infra("document_refresh_transition").verify_guarded_api(clients, probe)
            assert history is not None
            prior_journal = rescue_prior_journal(history, service, clients, probe)
        finally:
            clients.close()
    context = ({"consumed_history": consumed_history, "application_context": application_context}
               if application_context is not None else {})
    nonce = infra("document_refresh_recovery").prepare(
        directory, history, recover_failed_native_job, refresh_module=sys.modules[__name__],
        retire_expired_v2=retire_expired_v2, rescue_expired_v2=rescue_expired_v2,
        rescue_prior_journal=prior_journal if rescue_expired_v2 else None, **context)
    preflight = probe.Clients()
    try:
        serving = preflight.preflight()["service"]
    finally:
        preflight.close()
    current_image = serving["template"]["containers"][0]["image"]
    current_policy = probe.policy_for_serving(serving, current_image)
    if rescue_expired_v2:
        need(current_policy == probe.POLICY and current_image != probe.BASE_IMAGE,
             "document_refresh_rescue_v2_predecessor_changed")
    if upgrade_current_v2_to_v3:
        need(current_policy == probe.POLICY and current_image != probe.BASE_IMAGE,
             "document_refresh_fresh_v2_upgrade_requires_v2")
        return infra("document_candidate_release").refresh(directory, publish=publish,
            now=now, nonce=nonce, upgrade_current_v2_to_v3=True)
    if retire_expired_v2:
        need(current_policy == probe.POLICY and current_image != probe.BASE_IMAGE,
             "document_refresh_expired_v2_predecessor_required")
        return infra("document_candidate_release").refresh(directory, publish=publish,
            now=now, nonce=nonce, retire_expired_v2=True)
    from infra.gcp.document_source_candidate import POLICY_SHA256 as source_policy
    if current_policy == source_policy and not source_revision:
        need(not full_source and application_context is not None, "document_refresh_source_revision_app_context_required")
        value = source_definitions_candidate(directory, publish=publish, now=now, nonce=nonce, parent=current_image)
        return infra("document_source_release").promote(value["build_identity"], directory / "native-proof",
            directory / "transition", application_context=application_context)
    if source_revision:
        value = full_source_candidate(directory, publish=publish, now=now, nonce=nonce, source_revision=True)
        return infra("document_source_release").promote(value["build_identity"], directory / "native-proof",
            directory / "transition", application_context=application_context)
    if current_policy == infra("document_candidate").POLICY_SHA256:
        need(not full_source, "document_refresh_candidate_full_source_review_required")
        return infra("document_candidate_release").refresh(directory, publish=publish, now=now, nonce=nonce)
    selected = full_source_candidate if full_source else candidate
    value = (selected(directory, publish=publish, now=now) if nonce is None else
             selected(directory, publish=publish, now=now, nonce=nonce))
    finish_context = {"application_context": application_context} if application_context is not None else {}
    return finish_refresh(directory, value, prior_journal=prior_journal, history=history,
                          rescue_expired_v2=rescue_expired_v2, **finish_context)


def resume_refresh(directory: Path, original: Path, *, manifest_sha256: str, publish: bool,
                   now: datetime, prior_journal: Path | None = None, history: Path | None = None) -> dict[str, Any]:
    """Continue one acknowledged candidate; source upload/Build are never sent."""
    need(history is not None, "document_resume_other_history_required")
    infra("document_refresh_recovery").prepare(directory, history, None,
        refresh_module=sys.modules[__name__], resuming_original=original)
    value = infra("document_refresh_resume").resume_candidate(directory, original,
        manifest_sha256=manifest_sha256, publish=publish, now=now, build=sys.modules[__name__])
    result = finish_refresh(directory, value, prior_journal=prior_journal, history=history)
    result.update(original_manifest_sha256=manifest_sha256, resumed_existing_build=True)
    return result


def resume_expired_v2_transition(directory: Path, original: Path) -> dict[str, Any]:
    """Continue the exact acknowledged rescue; never create a Build or Job."""
    controller = infra("document_refresh_transition")
    need(original.is_absolute() and original.resolve() == original and not original.is_symlink()
         and original.is_dir() and original.stat().st_uid == os.getuid()
         and stat.S_IMODE(original.stat().st_mode) == 0o700,
         "document_rescue_original_artifact_private_required")
    native = retain_prior_journal(original / "native-proof", directory / "native-proof")
    transition_journal = retain_prior_journal(original / "transition", directory / "transition")
    tree = hashlib.sha256()
    members = sorted((*native.glob("*.json"), *transition_journal.glob("*.json")),
                     key=lambda path: path.relative_to(directory).as_posix())
    need(len(members) == 16, "document_rescue_original_journal_changed")
    for member in members:
        tree.update(member.relative_to(directory).as_posix().encode() + b"\0"
                    + member.read_bytes() + b"\0")
    need(tree.hexdigest() == controller.RESCUE_JOURNAL_TREE_SHA256,
         "document_rescue_original_journal_changed")
    binding = decode((native / "binding.json").read_bytes())
    need(type(binding) is dict and binding.get("image") == controller.RESCUE_IMAGE
         and type(binding.get("build_identity")) is dict
         and binding["build_identity"].get("build_id") == controller.RESCUE_BUILD_ID
         and binding["build_identity"].get("nonce") == controller.RESCUE_NONCE,
         "document_rescue_exact_candidate_required")
    build = binding["build_identity"]
    http = native_http()
    try:
        result = controller.resume_failed_rescue(controller.RESCUE_IMAGE, build, native,
                                                  transition_journal, http=http,
                                                  source_journal_tree_sha256=tree.hexdigest(),
                                                  probe=infra("document_refresh_probe"))
    finally:
        http.close()
    result["source_run_id"] = controller.RESCUE_RUN_ID
    result["source_artifact_sha256"] = "39f09c28b9a745041b3052c8604fb1a420622de291ff6b2275fa9b4abdefb144"
    result["prior_cleanup"] = "retained_prior_job_not_deleted"
    return result


def resume_partial_v2_transition(directory: Path, original: Path) -> dict[str, Any]:
    """Reconcile the exact partial rescue; leave the emergency API close intact."""
    controller = infra("document_refresh_transition")
    partial = infra("document_refresh_partial")
    need(original.is_absolute() and original.resolve() == original and not original.is_symlink()
         and original.is_dir() and original.stat().st_uid == os.getuid()
         and stat.S_IMODE(original.stat().st_mode) == 0o700,
         "document_partial_original_artifact_private_required")
    native = retain_prior_journal(original / "native-proof", directory / "native-proof")
    transition_journal = retain_prior_journal(original / "transition", directory / "source-transition")
    tree = hashlib.sha256()
    members = sorted((*native.glob("*.json"), *transition_journal.glob("*.json")),
                     key=lambda path: path.relative_to(directory).as_posix())
    need(len(members) == 35, "document_partial_source_tree_changed")
    for member in members:
        name = member.relative_to(directory).as_posix().replace("source-transition/", "transition/")
        tree.update(name.encode() + b"\0" + member.read_bytes() + b"\0")
    need(tree.hexdigest() == partial.JOURNAL_TREE_SHA256,
         "document_partial_source_tree_changed")
    binding = decode((native / "binding.json").read_bytes())
    need(type(binding) is dict and binding.get("image") == controller.RESCUE_IMAGE
         and type(binding.get("build_identity")) is dict
         and binding["build_identity"].get("build_id") == controller.RESCUE_BUILD_ID
         and binding["build_identity"].get("nonce") == controller.RESCUE_NONCE,
         "document_partial_exact_candidate_required")
    http = native_http()
    try:
        return partial.resume_after_api_maintenance_close(
            controller.RESCUE_IMAGE, binding["build_identity"], native,
            transition_journal, directory / "partial-transition",
            source_tree_sha256=tree.hexdigest(), http=http,
            probe=infra("document_refresh_probe"), t=controller)
    finally:
        http.close()


def resume_final_v2_transition(directory: Path, original: Path) -> dict[str, Any]:
    """Reconcile the second consumed claim; send only the coordinator open."""
    controller = infra("document_refresh_transition")
    final = infra("document_refresh_final")
    need(original.is_absolute() and original.resolve() == original and not original.is_symlink()
         and original.is_dir() and original.stat().st_uid == os.getuid()
         and stat.S_IMODE(original.stat().st_mode) == 0o700,
         "document_final_original_artifact_private_required")
    native = retain_prior_journal(original / "native-proof", directory / "native-proof")
    source = retain_prior_journal(original / "source-transition", directory / "source-transition")
    previous = retain_prior_journal(original / "partial-transition", directory / "partial-transition")
    tree = hashlib.sha256()
    members = sorted((*native.glob("*.json"), *source.glob("*.json"), *previous.glob("*.json")),
                     key=lambda path: path.relative_to(directory).as_posix())
    need(len(members) == 40, "document_final_source_tree_changed")
    for member in members:
        tree.update(member.relative_to(directory).as_posix().encode() + b"\0"
                    + member.read_bytes() + b"\0")
    need(tree.hexdigest() == final.JOURNAL_TREE_SHA256, "document_final_source_tree_changed")
    binding = decode((native / "binding.json").read_bytes())
    need(type(binding) is dict and binding.get("image") == controller.RESCUE_IMAGE
         and type(binding.get("build_identity")) is dict
         and binding["build_identity"].get("build_id") == controller.RESCUE_BUILD_ID
         and binding["build_identity"].get("nonce") == controller.RESCUE_NONCE,
         "document_final_exact_candidate_required")
    http = native_http()
    try:
        return final.resume(controller.RESCUE_IMAGE, binding["build_identity"], native,
                            source, previous, directory / "final-transition",
                            source_tree_sha256=tree.hexdigest(), http=http,
                            probe=infra("document_refresh_probe"), t=controller)
    finally:
        http.close()


def reconcile_final_v2_transition(directory: Path, original: Path) -> dict[str, Any]:
    """Prove the third consumed claim and acknowledged open without another write."""
    controller = infra("document_refresh_transition")
    final = infra("document_refresh_final_readback")
    need(original.is_absolute() and original.resolve() == original and not original.is_symlink()
         and original.is_dir() and original.stat().st_uid == os.getuid()
         and stat.S_IMODE(original.stat().st_mode) == 0o700
         and {p.name for p in original.iterdir()} == {
             "native-proof", "source-transition", "partial-transition", "final-transition", "failure.json"},
         "document_final_readback_private_artifact_required")
    failure = final.partial.source_json(original, "failure.json", controller)
    need(failure == {"status": "maintenance_attention_required",
                     "fixed_code": "document_transition_readback_changed",
                     "state_requires_read_only_reconciliation": True,
                     "automatic_replay": False},
         "document_final_readback_failure_receipt_changed")
    native = retain_prior_journal(original / "native-proof", directory / "native-proof")
    source = retain_prior_journal(original / "source-transition", directory / "source-transition")
    previous = retain_prior_journal(original / "partial-transition", directory / "partial-transition")
    failed = retain_prior_journal(original / "final-transition", directory / "final-transition")
    members = sorted((*(native.glob("*.json")), *(source.glob("*.json")),
                      *(previous.glob("*.json")), *(failed.glob("*.json"))),
                     key=lambda path: path.relative_to(directory).as_posix())
    need(len(members) == 45, "document_final_readback_source_tree_changed")
    rows = [("failure.json", (original / "failure.json").read_bytes())]
    rows.extend((member.relative_to(directory).as_posix(), member.read_bytes()) for member in members)
    tree = hashlib.sha256()
    for name, raw in sorted(rows):
        tree.update(name.encode() + b"\0" + raw + b"\0")
    need(tree.hexdigest() == final.JOURNAL_TREE_SHA256,
         "document_final_readback_source_tree_changed")
    write_new(directory / "source-failure.json", failure)
    binding = decode((native / "binding.json").read_bytes())
    need(type(binding) is dict and binding.get("image") == controller.RESCUE_IMAGE
         and type(binding.get("build_identity")) is dict
         and binding["build_identity"].get("build_id") == controller.RESCUE_BUILD_ID
         and binding["build_identity"].get("nonce") == controller.RESCUE_NONCE,
         "document_final_readback_exact_candidate_required")
    delegate = native_http()
    try:
        http = final.ReadOnlyHttp(delegate, controller)
        return final.reconcile(controller.RESCUE_IMAGE, binding["build_identity"], native,
                               source, previous, failed, directory / "readback",
                               source_tree_sha256=tree.hexdigest(), http=http,
                               probe=infra("document_refresh_probe"), t=controller)
    finally:
        delegate.close()


def finish_refresh(directory: Path, value: dict[str, Any], *, prior_journal: Path | None = None,
                   history: Path | None = None, rescue_expired_v2: bool = False,
                   application_context: Any = None) -> dict[str, Any]:
    """Shared native one-send, transition and actual-prior retirement ordering."""
    probe = infra("document_refresh_probe")
    image, build = value["candidate_registry_digest"], value["build_identity"]
    native = directory / "native-proof"
    acceptance = probe.run_successor(image, build, native)
    # The transition freshly verifies Build/Job/Execution/logs again before writes.
    controller = infra("document_refresh_transition")
    http = native_http()
    try:
        if rescue_expired_v2:
            result = controller.transition(image, build, native, directory / "transition", http=http,
                                           probe=probe, rescue_expired_v2=True)
        else:
            options = {"application_context": application_context} if application_context is not None else {}
            result = controller.transition(image, build, native, directory / "transition", http=http, probe=probe, **options)
    finally:
        http.close()
    result["definition_expires_at"] = acceptance.definition_expires_at
    if prior_journal is None and history is not None and result["prior_image"] != RENDERER_BASE:
        matches = []
        for binding_path in history.glob("*/native-proof/binding.json"):
            # Retained failed journals are evidence, never a serving predecessor
            # or a deletion target, even if another Job proved identical bytes.
            acceptance_path = binding_path.parent / "acceptance.json"
            if (acceptance_path.is_file() and not acceptance_path.is_symlink()
                    and not binding_path.is_symlink() and binding_path.stat().st_size <= MAX_OUTPUT):
                binding = decode(binding_path.read_bytes())
                if binding.get("image") == result["prior_image"]:
                    matches.append(binding_path.parent)
        need(len(matches) == 1, "document_refresh_prior_owned_history_unavailable")
        prior_journal = matches[0]
    if prior_journal is not None:
        # Only the actual prior owned journal can retire that exact Job. A prior
        # acceptance JSON or current labels never synthesize old mutation ACKs.
        info = prior_journal.lstat()
        need(prior_journal.is_absolute() and not prior_journal.is_symlink()
             and info.st_uid == os.getuid(), "document_refresh_prior_journal_refused")
        retained = retain_prior_journal(prior_journal, directory / "prior-native-retirement")
        binding = decode((retained / "binding.json").read_bytes())
        cleanup_options = ({"caller_serving_validator": application_context.serving}
                           if application_context is not None else {})
        result["prior_cleanup"] = probe.cleanup_successor(binding["image"], binding["build_identity"], retained,
                                                         **cleanup_options)
    return result


def retain_prior_journal(source: Path, target: Path) -> Path:
    """Preserve genuine historical bytes and new Delete markers in this run's artifact."""
    info = source.lstat()
    need(source.resolve() == source and stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
         and stat.S_IMODE(info.st_mode) == 0o700 and not target.exists(),
         "document_refresh_prior_journal_refused")
    entries = list(source.iterdir())
    need(1 <= len(entries) <= 32, "document_refresh_prior_journal_bound")
    target.mkdir(mode=0o700)
    for entry in entries:
        need(re.fullmatch(r"[a-z][a-z0-9.-]{0,99}\.json", entry.name), "document_refresh_prior_journal_refused")
        fd = os.open(entry, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            status = os.fstat(stream.fileno())
            need(stat.S_ISREG(status.st_mode) and status.st_nlink == 1 and status.st_uid == os.getuid()
                 and stat.S_IMODE(status.st_mode) == 0o600 and 0 < status.st_size <= MAX_OUTPUT,
                 "document_refresh_prior_journal_refused")
            raw = stream.read(MAX_OUTPUT + 1)
        need(0 < len(raw) <= MAX_OUTPUT, "document_refresh_prior_journal_bound")
        fd = os.open(target / entry.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return target


def emit_status(result: dict[str, Any]) -> None:
    """Fixed nonsecret monitoring payload; absence of this heartbeat also alerts."""
    definitions = result.get("definitions", {})
    state = definitions.get("status", "unavailable") if result.get("mode") == "monitor" else "refreshed"
    need(state in {"fresh", "refresh_required", "expired", "unavailable", "refreshed"},
         "document_refresh_monitor_status_refused")
    payload = {"event": "hirewiz_document_definition_maintenance_v1", "status": state,
               "controller": "hirewiz-document-refresh", "version": 1}
    if type(definitions.get("seconds_remaining")) is int:
        payload["seconds_remaining"] = definitions["seconds_remaining"]
    http = native_http()
    try:
        native_json(http, "POST", "https://logging.googleapis.com/v2/entries:write", {
            "logName": "projects/" + PROJECT + "/logs/hirewiz-document-refresh",
            "resource": {"type": "global", "labels": {"project_id": PROJECT}},
            "entries": [{"severity": "INFO" if state in {"fresh", "refreshed"} else "ERROR", "jsonPayload": payload}]})
    finally:
        http.close()


def main() -> None:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--mode", choices=("monitor", "candidate", "full-source-candidate", "refresh", "full-source-refresh", "resume-full-source-refresh", "resume-expired-v2-transition", "resume-partial-v2-transition", "resume-final-v2-transition", "reconcile-final-v2-transition", "audit-consumed-v2-history", "reconcile"), required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--publish-candidate", action="store_true")
    parser.add_argument("--source-revision", action="store_true")
    parser.add_argument("--prior-native-journal", type=Path)
    parser.add_argument("--transition-journal", type=Path)
    parser.add_argument("--prior-history-directory", type=Path)
    parser.add_argument("--consumed-history-bundle", type=Path)
    parser.add_argument("--reviewed-application-context", action="store_true")
    parser.add_argument("--recover-failed-native-job")
    parser.add_argument("--retire-expired-v2", action="store_true")
    parser.add_argument("--rescue-expired-v2", action="store_true")
    parser.add_argument("--upgrade-current-v2-to-v3", action="store_true")
    parser.add_argument("--resume-candidate-directory", type=Path)
    parser.add_argument("--resume-manifest-sha256")
    parser.add_argument("--resume-transition-directory", type=Path)
    args = parser.parse_args()
    need(not args.source_revision or args.mode in {"full-source-candidate", "full-source-refresh"},
         "document_refresh_source_revision_mode_required")
    need(args.consumed_history_bundle is None or args.mode in {"refresh", "audit-consumed-v2-history"}
         or args.source_revision and args.mode == "full-source-refresh",
         "document_refresh_consumed_audit_mode_required")
    need(not args.reviewed_application_context or args.mode == "refresh"
         or args.source_revision and args.mode == "full-source-refresh",
         "document_refresh_reviewed_app_context_mode_required")
    need(args.mode == "resume-full-source-refresh" or args.resume_candidate_directory is None
         and args.resume_manifest_sha256 is None, "document_resume_arguments_mode_required")
    need(args.mode in {"resume-expired-v2-transition", "resume-partial-v2-transition", "resume-final-v2-transition", "reconcile-final-v2-transition"}
         or args.resume_transition_directory is None,
         "document_rescue_transition_arguments_mode_required")
    need(args.recover_failed_native_job is None or args.mode in {"refresh", "full-source-refresh"} and args.publish_candidate,
         "document_refresh_recovery_requires_explicit_refresh")
    need(not args.retire_expired_v2 or (args.mode == "refresh" and args.publish_candidate
         and not args.rescue_expired_v2
         and (os.getenv("GITHUB_ACTIONS") != "true" or os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch")),
         "document_refresh_expired_v2_requires_dispatch")
    need(not args.rescue_expired_v2 or (args.mode == "refresh" and args.publish_candidate
         and not args.retire_expired_v2 and args.recover_failed_native_job is None
         and (os.getenv("GITHUB_ACTIONS") != "true" or os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch")),
         "document_refresh_expired_v2_requires_dispatch")
    need(not args.upgrade_current_v2_to_v3 or (args.mode == "refresh" and args.publish_candidate
         and args.recover_failed_native_job is None and not args.retire_expired_v2
         and not args.rescue_expired_v2
         and (os.getenv("GITHUB_ACTIONS") != "true" or os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch")),
         "document_refresh_fresh_v2_upgrade_requires_dispatch")
    need(args.output_directory.is_absolute() and not args.output_directory.exists(),
         "document_refresh_fresh_output_directory_required")
    args.output_directory.mkdir(mode=0o700)
    if args.mode == "audit-consumed-v2-history":
        need(args.consumed_history_bundle is not None and not args.publish_candidate
             and args.prior_native_journal is None and args.prior_history_directory is None
             and args.transition_journal is None and args.recover_failed_native_job is None
             and not (args.retire_expired_v2 or args.rescue_expired_v2 or args.upgrade_current_v2_to_v3),
             "document_refresh_consumed_audit_mode_required")
        module = infra("document_consumed_native")
        module.history.load_bundle(args.consumed_history_bundle)
        probe = infra("document_refresh_probe")
        clients, delegate = probe.Clients(), None
        try:
            delegate = native_http()
            boundary = module.NativeAudit(ROOT, args.consumed_history_bundle, clients, delegate, probe)
            result = module.history.audit(args.consumed_history_bundle, boundary).report()
        finally:
            clients.close()
            if delegate is not None:
                delegate.close()
    elif args.mode == "monitor":
        result = monitor(now=datetime.now(UTC))
        emit_status(result)
    elif args.mode == "resume-full-source-refresh":
        need(args.resume_candidate_directory is not None and args.resume_manifest_sha256 is not None,
             "document_resume_original_candidate_required")
        result = resume_refresh(args.output_directory, args.resume_candidate_directory,
            manifest_sha256=args.resume_manifest_sha256, publish=args.publish_candidate, now=datetime.now(UTC),
            prior_journal=args.prior_native_journal, history=args.prior_history_directory)
        emit_status(result)
    elif args.mode == "resume-expired-v2-transition":
        need(args.resume_transition_directory is not None and not args.publish_candidate
             and args.prior_native_journal is None and args.prior_history_directory is None
             and args.transition_journal is None and args.recover_failed_native_job is None
             and not args.rescue_expired_v2,
             "document_rescue_exact_transition_mode_required")
        result = resume_expired_v2_transition(args.output_directory, args.resume_transition_directory)
        emit_status(result)
    elif args.mode == "resume-partial-v2-transition":
        need(args.resume_transition_directory is not None and not args.publish_candidate
             and args.prior_native_journal is None and args.prior_history_directory is None
             and args.transition_journal is None and args.recover_failed_native_job is None
             and not args.rescue_expired_v2,
             "document_partial_exact_transition_mode_required")
        result = resume_partial_v2_transition(args.output_directory, args.resume_transition_directory)
    elif args.mode == "resume-final-v2-transition":
        need(args.resume_transition_directory is not None and not args.publish_candidate
             and args.prior_native_journal is None and args.prior_history_directory is None
             and args.transition_journal is None and args.recover_failed_native_job is None
             and not args.rescue_expired_v2,
             "document_final_exact_transition_mode_required")
        result = resume_final_v2_transition(args.output_directory, args.resume_transition_directory)
    elif args.mode == "reconcile-final-v2-transition":
        need(args.resume_transition_directory is not None and not args.publish_candidate
             and args.prior_native_journal is None and args.prior_history_directory is None
             and args.transition_journal is None and args.recover_failed_native_job is None
             and not args.rescue_expired_v2,
             "document_final_readback_exact_mode_required")
        result = reconcile_final_v2_transition(args.output_directory, args.resume_transition_directory)
    elif args.mode in {"refresh", "full-source-refresh"}:
        arguments = {"publish": args.publish_candidate, "now": datetime.now(UTC),
                     "prior_journal": args.prior_native_journal, "history": args.prior_history_directory,
                     "recover_failed_native_job": args.recover_failed_native_job}
        if args.mode == "full-source-refresh":
            arguments["full_source"] = True
        if args.source_revision:
            arguments["source_revision"] = True
        if args.retire_expired_v2:
            arguments["retire_expired_v2"] = True
        if args.rescue_expired_v2:
            arguments["rescue_expired_v2"] = True
        if args.upgrade_current_v2_to_v3:
            arguments["upgrade_current_v2_to_v3"] = True
        if args.reviewed_application_context:
            arguments["reviewed_application_context"] = True
        if args.consumed_history_bundle is not None:
            arguments["consumed_history_bundle"] = args.consumed_history_bundle
        result = refresh(args.output_directory, **arguments)
        emit_status(result)
    elif args.mode == "full-source-candidate":
        result = full_source_candidate(args.output_directory, publish=args.publish_candidate, now=datetime.now(UTC),
                                       source_revision=args.source_revision)
    elif args.mode == "reconcile":
        need(args.transition_journal is not None, "document_refresh_transition_journal_required")
        http = native_http()
        try:
            result = infra("document_refresh_transition").reconcile(args.transition_journal, http=http)
        finally:
            http.close()
    else:
        result = candidate(args.output_directory, publish=args.publish_candidate, now=datetime.now(UTC))
    write_new(args.output_directory / "result.json", result)
    print(json.dumps(result, sort_keys=True))
    if args.mode == "monitor" and result["definitions"]["status"] != "fresh":
        raise RefreshDenied("document_refresh_expiry_alert")


def _final_failure_code(error: Exception) -> str:
    """Return only a reviewed literal; never return vendor exception text."""
    args = BaseException.__dict__["args"].__get__(error)
    code = args[0] if len(args) == 1 and type(args[0]) is str and len(args[0]) <= 180 else ""
    try:
        kind = _diagnostic_kind_code(error)[0]
    except Exception:
        kind = "unclassified"
    if kind in {"RefreshDenied", "NativeProofDenied", "IdentityDenied", "TransitionDenied"} \
            and code in FINAL_READ_ONLY_FAILURE_CODES:
        return code
    return "document_refresh_unavailable"


# Presentation only. No transport, identity, proof or retry decisions use these
# diagnostics. Unknown names/messages and external frame paths are never copied.
FAILURE_DIAGNOSTIC_BYTES = 32768
FAILURE_DIAGNOSTIC_NODES = 8
FAILURE_DIAGNOSTIC_FRAMES = 48
_DIAGNOSTIC_REPO_KINDS = {
    "backend/scripts/document_refresh.py": ("RefreshDenied", ""),
    "infra/gcp/document_refresh_probe.py": ("NativeProofDenied", ""),
    "infra/gcp/document_refresh_auth.py": ("IdentityDenied", ""),
    "infra/gcp/document_refresh_transition.py": ("TransitionDenied", ""),
    "infra/gcp/document_application_context.py": ("ApplicationDenied", "document_application_"),
    "infra/gcp/document_consumed_history.py": ("HistoryDenied", "document_consumed_history_"),
    "infra/gcp/document_source_candidate.py": ("SourceDenied", "document_source_revision_"),
}
_DIAGNOSTIC_VENDOR_KINDS = {
    "requests.exceptions": {"HTTPError": "HTTPError", "ConnectionError": "ConnectionError",
                            "Timeout": "Timeout", "SSLError": "SSLError"},
    "google.auth.exceptions": {"RefreshError": "RefreshError", "DefaultCredentialsError": "DefaultCredentialsError",
                               "TransportError": "TransportError"},
    "apitools.base.py.exceptions": {name: "HttpError" for name in (
        "HttpError", "HttpBadRequestError", "HttpUnauthorizedError", "HttpForbiddenError", "HttpNotFoundError",
        "HttpConflictError", "HttpTooManyRequestsError", "HttpInternalServerError", "HttpServiceUnavailableError",
        "HttpGatewayTimeoutError")},
    "google.api_core.exceptions": {name: "GoogleAPICallError" for name in (
        "GoogleAPICallError", "BadRequest", "Unauthorized", "Forbidden", "NotFound", "TooManyRequests",
        "InternalServerError", "ServiceUnavailable", "GatewayTimeout", "DeadlineExceeded")},
}


def _diagnostic_literals(path: Path, prefix: str) -> set[str]:
    """Only literal need/raise codes in this fixed, source-owned module qualify."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 262144:
        return set()
    tree = ast.parse(path.read_bytes())
    values = set()
    for node in ast.walk(tree):
        candidates = []
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else (
                node.func.attr if isinstance(node.func, ast.Attribute) else "")
            if name == "need":
                candidates.extend(node.args[1:2])
                candidates.extend(k.value for k in node.keywords if k.arg == "code")
            elif name.endswith("Denied"):
                candidates.extend(node.args[:1])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "need":
            candidates.extend(node.args.defaults)
        for candidate in candidates:
            if isinstance(candidate, ast.IfExp):
                candidates.extend((candidate.body, candidate.orelse))
            if isinstance(candidate, ast.Constant) and type(candidate.value) is str:
                value = prefix + candidate.value
                if re.fullmatch(r"document_[a-z0-9_]{1,170}", value):
                    values.add(value)
    return values


def _diagnostic_kind_code(error: BaseException) -> tuple[str, str | None]:
    cls = type(error)
    builtins = tuple((kind, kind.__name__) for kind in (
        Exception, RuntimeError, ValueError, TypeError, KeyError, AttributeError, AssertionError,
        NotImplementedError, OSError, FileNotFoundError, PermissionError, IsADirectoryError,
        NotADirectoryError, TimeoutError, ConnectionError, json.JSONDecodeError,
        subprocess.TimeoutExpired, subprocess.CalledProcessError))
    for known, kind in builtins:
        if cls is known:
            return kind, None
    # Never hash an unknown class or inspect its name/metaclass properties.
    modules = tuple(module for module in sys.modules.copy().values() if type(module) is ModuleType)
    for relative, (kind, prefix) in _DIAGNOSTIC_REPO_KINDS.items():
        path = ROOT / relative
        for module in modules:
            namespace = vars(module)
            filename = namespace.get("__file__")
            if namespace.get(kind) is not cls or type(filename) is not str or len(filename) > 4096:
                continue
            try:
                if Path(filename).resolve() != path.resolve():
                    continue
            except (OSError, ValueError, RuntimeError):
                continue
            try:
                codes = _diagnostic_literals(path, prefix)
                if kind == "HistoryDenied":
                    # NativeAudit supplies fixed permission reasons to history.need.
                    codes |= _diagnostic_literals(ROOT / "infra/gcp/document_consumed_native.py", prefix)
            except (OSError, ValueError, SyntaxError, RuntimeError):
                codes = set()
            args = BaseException.__dict__["args"].__get__(error)
            value = args[0] if len(args) == 1 and type(args[0]) is str and len(args[0]) <= 180 else ""
            if kind == "RefreshDenied":
                codes |= FINAL_READ_ONLY_FAILURE_CODES
            return kind, value if value in codes else None
    for module_name, names in _DIAGNOSTIC_VENDOR_KINDS.items():
        vendor_module = sys.modules.get(module_name)
        if type(vendor_module) is ModuleType:
            for name, kind in names.items():
                if vars(vendor_module).get(name) is cls:
                    return kind, None
    return "unclassified", None


def _diagnostic_number(value: Any) -> int | None:
    if type(value) is HTTPStatus:
        value = int(value)
    if type(value) is str and re.fullmatch(r"[1-5][0-9]{2}", value):
        value = int(value)
    return value if type(value) is int and 100 <= value <= 599 else None


def _failure_diagnostics(error: BaseException) -> dict[str, Any]:
    pending, indices, nodes = [error], {id(error): 0}, []
    frames_left, truncated = FAILURE_DIAGNOSTIC_FRAMES, False
    for current in pending:
        kind, code = _diagnostic_kind_code(current)
        row: dict[str, Any] = {"kind": kind, "frames": []}
        if code is not None:
            row["fixed_code"] = code
        if issubclass(type(current), OSError):
            number = OSError.__dict__["errno"].__get__(current)
            if type(number) is int and 0 <= number <= 4095:
                row["errno"] = number
        if type(current) is json.JSONDecodeError:
            state = vars(current)
            row["json_position"] = {k: state[k] for k in ("pos", "lineno", "colno")
                                    if type(state.get(k)) is int and 0 <= state[k] <= 2147483647}
        if kind in {"HTTPError", "HttpError", "GoogleAPICallError"}:
            state = vars(current)
            statuses = [_diagnostic_number(state.get("status_code")), _diagnostic_number(state.get("status")),
                        _diagnostic_number(vars(type(current)).get("code"))]
            for key in ("response", "resp"):
                response = state.get(key)
                if isinstance(response, dict):
                    statuses.append(_diagnostic_number(dict.get(response, "status")))
                elif type(response) is getattr(sys.modules.get("requests.models"), "Response", None):
                    statuses.append(_diagnostic_number(vars(response).get("status_code")))
            if any(value is not None for value in statuses):
                row["http_status"] = next(value for value in statuses if value is not None)
        tb, scanned, kept = BaseException.__dict__["__traceback__"].__get__(current), 0, 0
        while tb is not None and scanned < 128:
            scanned += 1
            frame = tb.tb_frame.f_code
            try:
                if len(frame.co_filename) > 4096:
                    raise ValueError
                path = Path(frame.co_filename)
                relative = path.resolve().relative_to(ROOT.resolve()).as_posix()
                safe = (not path.is_symlink() and path.is_file() and len(relative) <= 256
                        and re.fullmatch(r"(?:backend|infra)/[A-Za-z0-9_./-]+\.py", relative))
            except (OSError, ValueError, RuntimeError):
                safe = False
            if safe and frames_left and kept < 12:
                function = frame.co_name
                if not (re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,95}", function)
                        or function in {"<module>", "<lambda>", "<genexpr>", "<listcomp>", "<dictcomp>", "<setcomp>"}):
                    function = "unclassified"
                row["frames"].append({"file": relative, "function": function, "line": min(tb.tb_lineno, 9999999)})
                frames_left -= 1
                kept += 1
            elif safe:
                truncated = True
            tb = tb.tb_next
        truncated |= tb is not None
        row["suppress_context"] = BaseException.__dict__["__suppress_context__"].__get__(current)
        for field in ("__cause__", "__context__"):
            child = BaseException.__dict__[field].__get__(current)
            if child is None:
                row[field[2:-2]] = None
            elif id(child) in indices:
                row[field[2:-2]] = indices[id(child)]
            elif len(pending) < FAILURE_DIAGNOSTIC_NODES:
                indices[id(child)] = len(pending)
                row[field[2:-2]] = len(pending)
                pending.append(child)
            else:
                row[field[2:-2]] = None
                truncated = True
        nodes.append(row)
    return {"schema": "hirewiz_document_failure_diagnostics_v1", "nodes": nodes, "truncated": truncated}


def _record_failure_diagnostics(error: Exception, code: str) -> bool:
    """Create-only local evidence; never create a directory or change a result."""
    if _cli_option("--mode") not in {"full-source-refresh", "monitor"}:
        return False
    output = _cli_option("--output-directory")
    if output is None:
        return False
    directory = Path(output)
    try:
        if not directory.is_absolute() or directory.is_symlink() or directory.resolve() != directory:
            return False
        before = directory.stat()
    except (OSError, ValueError, RuntimeError):
        return False
    if not stat.S_ISDIR(before.st_mode) or before.st_uid != os.getuid() or stat.S_IMODE(before.st_mode) != 0o700:
        return False
    safe_code = _diagnostic_kind_code(error)[1]
    code = code if code == safe_code else "document_refresh_unavailable"
    payload = {"status": "maintenance_attention_required", "fixed_code": code,
               "state_requires_read_only_reconciliation": True, "automatic_replay": False,
               "diagnostics": _failure_diagnostics(error)}
    raw = (json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    if len(raw) > FAILURE_DIAGNOSTIC_BYTES:
        return False
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        current = os.fstat(descriptor)
        if ((current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)
                or current.st_uid != os.getuid() or stat.S_IMODE(current.st_mode) != 0o700):
            return False
        try:
            fd = os.open("failure-diagnostic.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=descriptor)
        except FileExistsError:
            return False
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return True


def _cli_option(name: str) -> str | None:
    """Read one exact CLI option in either spelling without argument parsing."""
    values = []
    for index, argument in enumerate(sys.argv):
        if argument == name and index + 1 < len(sys.argv):
            values.append(sys.argv[index + 1])
        elif argument.startswith(name + "="):
            values.append(argument[len(name) + 1:])
    return values[0] if len(values) == 1 else None


def _record_final_failure(code: str) -> None:
    """Keep a private fixed-code receipt even when no transition marker exists."""
    mode = _cli_option("--mode")
    output = _cli_option("--output-directory")
    if mode not in {"resume-final-v2-transition", "reconcile-final-v2-transition"} or output is None:
        return
    directory = Path(output)
    if (not directory.is_absolute() or directory.is_symlink() or directory.resolve() != directory
            or not directory.is_dir() or directory.stat().st_uid != os.getuid()
            or stat.S_IMODE(directory.stat().st_mode) != 0o700):
        return
    write_new(directory / "failure.json", {"status": "maintenance_attention_required",
        "fixed_code": code, "state_requires_read_only_reconciliation": True,
        "automatic_replay": False})


def run_cli() -> int:
    try:
        main()
        return 0
    except Exception as error:
        mode = _cli_option("--mode") or ""
        readback_requested = any(argument in {"reconcile-final-v2-transition", "audit-consumed-v2-history"}
                                 or argument.endswith(("=reconcile-final-v2-transition", "=audit-consumed-v2-history"))
                                 for argument in sys.argv)
        # Reporting is an independent bounded write, never a retry of the failed
        # Build/Job/PATCH. The exact read-only reconciliation mode must not
        # write even a failure heartbeat to Cloud Logging.
        if not readback_requested:
            try:
                emit_status({"mode": "monitor", "definitions": {"status": "unavailable"}})
            except Exception:
                pass
        final_mode = mode in {"resume-final-v2-transition", "reconcile-final-v2-transition"}
        code = _final_failure_code(error) if final_mode else "document_refresh_unavailable"
        if not final_mode and type(error) is RefreshDenied:
            try:
                code = _diagnostic_kind_code(error)[1] or code
            except Exception:
                pass
        if mode == "audit-consumed-v2-history":
            classes = [(sys.modules.get("document_consumed_history"), "HistoryDenied"),
                       (sys.modules.get("document_application_context"), "ApplicationDenied")]
            if (any(type(error) is getattr(module, name, None) for module, name in classes)
                    and re.fullmatch(r"document_(?:consumed_history|application)_[a-z_]{1,100}", str(error))):
                code = str(error)
        if final_mode:
            try:
                _record_final_failure(code)
            except Exception:
                pass
        try:
            _record_failure_diagnostics(error, code)
        except Exception:
            # Local diagnostic storage must never change the original outcome,
            # trigger another native call, or authorize a replay.
            pass
        print(json.dumps({"status": "maintenance_attention_required", "fixed_code": code,
                          "state_requires_read_only_reconciliation": True, "automatic_replay": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(run_cli())
