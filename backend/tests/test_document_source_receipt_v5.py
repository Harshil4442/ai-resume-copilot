"""Synthetic receipt controls, with historical V4 checked independently."""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
from infra.gcp import document_candidate_probe as candidate
from infra.gcp import document_source_payload as v4_payload
from infra.gcp import document_source_receipt as v4
from infra.gcp import document_source_receipt_v5 as v5
from test_document_candidate_probe import IMAGE
from test_document_candidate_probe import inventory as old_inventory
from test_document_source_revision import source_receipt

REFUSED = {
    "eicar_exact_file", "eicar_tex_source", "eicar_texzip_member",
    "rendered_reflow_refusal", "unsupported_source_font_refusal",
    "pdf_spanning_columns_refused", "pdf_unaligned_gutter_refused",
}
POLICY = "d" * 64
RUNTIME = {"/opt/document/service.py": "f" * 64, "/opt/document/processor.py": "e" * 64}


def _provider_diagnostic(code):
    return {"launcher": "returned", "launcher_code": 0, "launcher_elapsed_ms": 42,
        "cleanup": "provider_teardown", "cleanup_code": None, "cleanup_elapsed_ms": None,
        "processor_code": code, "processor": {None: "not_validated", 0: "clean", 1: "refused"}[code],
        "validation": "not_started" if code is None else "complete",
        "failure_phase": "processor" if code == 1 else None}


def _corpus():
    historical = source_receipt(SimpleNamespace(policy=POLICY, runtime=RUNTIME))
    value = copy.deepcopy(historical)
    value.update(event=v5.CORPUS_EVENT, version=v5.VERSION)
    value["identity_preflight"]["diagnostic"] = _provider_diagnostic(None)
    for row in value["cases"]:
        name = row["case"]
        code = None if name == "eicar_direct_detection" else int(name in REFUSED)
        row["diagnostic"] = _provider_diagnostic(code)
    return historical, value


def _inventory():
    historical = old_inventory()
    historical.update(event=v4_payload.INVENTORY_EVENT, policy_sha256=POLICY)
    historical["identity_preflight"]["runtime_hashes"] = RUNTIME
    value = copy.deepcopy(historical)
    value.update(event=v5.INVENTORY_EVENT, version=v5.VERSION)
    value["identity_preflight"]["diagnostic"] = _provider_diagnostic(None)
    return historical, value


def test_v5_corpus_preserves_all_v4_case_checks_without_relabelling_originals():
    historical, value = _corpus()
    before = copy.deepcopy(value)
    assert len(value["cases"]) == 17 and sum("scan" in row for row in value["cases"]) == 9
    assert v4.validate(historical, IMAGE, POLICY, RUNTIME)
    assert v5.validate(value, IMAGE, POLICY, RUNTIME) and value == before
    with pytest.raises(ValueError, match="finite_probe_unverified"):
        v5.validate(historical, IMAGE, POLICY, RUNTIME)
    with pytest.raises(ValueError, match="finite_probe_unverified"):
        v4.validate(value, IMAGE, POLICY, RUNTIME)


def test_v5_inventory_requires_auxiliary_transport_and_preserves_stock_identity():
    historical, value = _inventory()
    expected = candidate.validate_inventory(historical, IMAGE, RUNTIME, policy=POLICY,
        event=v4_payload.INVENTORY_EVENT)
    before = copy.deepcopy(value)
    assert v5.validate_inventory(value, IMAGE, RUNTIME, policy=POLICY) == expected
    assert value == before
    with pytest.raises(ValueError, match="finite_probe_unverified"):
        v5.validate_inventory(historical, IMAGE, RUNTIME, policy=POLICY)
    value["identity_preflight"]["diagnostic"] = _provider_diagnostic(0)
    with pytest.raises(ValueError, match="finite_probe_unverified"):
        v5.validate_inventory(value, IMAGE, RUNTIME, policy=POLICY)


@pytest.mark.parametrize(("case", "changes"), [
    ("clean_pdf", {"launcher_code": False}),
    ("clean_pdf", {"launcher_code": 1}),
    ("clean_pdf", {"launcher": "reap_failed"}),
    ("clean_pdf", {"launcher_elapsed_ms": None}),
    ("clean_pdf", {"cleanup": "returned", "cleanup_code": 0, "cleanup_elapsed_ms": 2}),
    ("clean_pdf", {"cleanup_code": 0}),
    ("clean_pdf", {"cleanup_elapsed_ms": 0}),
    ("clean_pdf", {"processor_code": False}),
    ("clean_pdf", {"processor_code": None}),
    ("clean_pdf", {"processor_code": 2, "processor": "unavailable", "failure_phase": "processor"}),
    ("eicar_exact_file", {"processor_code": 0, "processor": "clean", "failure_phase": None}),
    ("eicar_exact_file", {"processor_code": 2, "processor": "unavailable"}),
    ("eicar_exact_file", {"failure_phase": "cleanup"}),
    ("eicar_direct_detection", {"processor_code": 0, "processor": "clean", "validation": "complete"}),
    ("pdf_independent_columns", {"validation": "binding"}),
])
def test_v5_refuses_ambiguous_transport_or_unavailable_processor(case, changes):
    _, value = _corpus()
    row = next(row for row in value["cases"] if row["case"] == case)
    row["diagnostic"].update(changes)
    with pytest.raises(ValueError, match="finite_probe_unverified"):
        v5.validate(value, IMAGE, POLICY, RUNTIME)


@pytest.mark.parametrize("change", ["reported_cleanup", "missing_case", "false_scan", "bad_binding", "bad_parser"])
def test_v5_teardown_does_not_substitute_for_original_case_evidence(change):
    _, value = _corpus()
    if change == "reported_cleanup":
        value["cases"][0]["cleanup_passed"] = False
    if change == "missing_case":
        value["cases"].pop()
    if change == "false_scan":
        value["cases"][0]["scan"]["engine"] = "other"
    if change == "bad_binding":
        value["cases"][0]["binding"]["sha256"] = "not-a-hash"
    if change == "bad_parser":
        next(row for row in value["cases"] if row["case"] == "pdf_independent_columns")["reading_order_pass"] = False
    with pytest.raises(ValueError, match="finite_probe_unverified"):
        v5.validate(value, IMAGE, POLICY, RUNTIME)
