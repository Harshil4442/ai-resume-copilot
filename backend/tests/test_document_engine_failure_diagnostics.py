"""Standalone finite CI diagnostics; no app imports or native engine execution."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def load_helper():
    path = Path(__file__).resolve().parents[1] / "scripts" / "document_test_image.py"
    spec = importlib.util.spec_from_file_location("document_engine_diagnostics", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m = load_helper()
BASE_KEYS = {"error", "stage", "case", "exit_code", "completed_cases"}


class Untrusted:
    def __eq__(self, other):
        raise AssertionError("untrusted diagnostic value compared")

    def __str__(self):
        raise AssertionError("untrusted diagnostic value serialized")

    def __repr__(self):
        raise AssertionError("untrusted diagnostic value represented")


class FailureDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.token = m._FAILURE.set({"stage": "setup", "completed_cases": 8})
        self.binding = {
            "version": 1, "operation": "docx_layout", "sha256": "a" * 64,
            "size_bytes": 42, "source_format": "docx", "candidate_sha256": "b" * 64,
            "candidate_size_bytes": 43, "edits_sha256": "c" * 64,
        }
        self.refusal = {
            **self.binding, "error": "docx_layout_refused", "reason": "nontext_changed",
            "unit_id": "PRIVATE_UNIT_AND_NATIVE_BODY_MARKER",
        }
        m._progress("case_validation", case="styled_multipage_fit", exit_code=1)

    def tearDown(self):
        m._FAILURE.reset(self.token)

    def note(self, result=None, *, binding=None, code=1, case="styled_multipage_fit"):
        m._note_docx_refusal(self.refusal if result is None else result,
                             self.binding if binding is None else binding, code, case=case)

    def assert_redacted(self, report):
        encoded = json.dumps(report)
        self.assertNotIn("PRIVATE", encoded)
        self.assertNotIn("unit_id", encoded)
        self.assertNotIn("a" * 64, encoded)
        self.assertNotIn("candidate_sha256", encoded)

    def test_exact_bound_refusal_keeps_only_finite_reason(self):
        self.note()
        diagnostic = m._FAILURE.get()
        self.assertIs(type(diagnostic["docx_refusal"]), m._DocxRefusal)
        self.assertEqual(diagnostic["docx_refusal"],
                         ("styled_multipage_fit", "docx_layout_refused", "nontext_changed"))
        self.assertNotIn("PRIVATE", repr(diagnostic))
        # Diagnostic storage owns no reference to either source-bound body.
        self.refusal.update(reason="PRIVATE_MUTATED_REASON", unit_id=Untrusted())
        self.binding["sha256"] = "PRIVATE_MUTATED_HASH"
        report = m._failure_report()
        self.assertEqual(set(report), BASE_KEYS | {"processor_error", "processor_reason"})
        self.assertEqual(report["processor_reason"], "nontext_changed")
        self.assertEqual(report["completed_cases"], 8)
        self.assert_redacted(report)

    def test_mismatched_or_nonprotocol_refusal_never_exports_reason(self):
        invalid = [
            ({**self.refusal, "sha256": "d" * 64}, self.binding, 1),
            ({**self.refusal, "size_bytes": True}, self.binding, 1),
            ({**self.refusal, "version": True}, self.binding, 1),
            ({**self.refusal, "raw_body": "PRIVATE_NATIVE_BODY"}, self.binding, 1),
            ({key: value for key, value in self.refusal.items() if key != "unit_id"}, self.binding, 1),
            ({**self.refusal, "error": "docx_layout_unavailable"}, self.binding, 1),
            ({**self.refusal, "reason": "PRIVATE_UNKNOWN_REASON"}, self.binding, 1),
            ({**self.refusal, "reason": "malformed_input"}, self.binding, 1),
            (self.refusal, {**self.binding, "size_bytes": 0}, 1),
            (self.refusal, self.binding, True),
            (self.refusal, self.binding, 2),
        ]
        for index, (result, binding, code) in enumerate(invalid):
            with self.subTest(control=index):
                # A rejected update must also clear a previous valid projection.
                self.note()
                self.note(result, binding=binding, code=code)
                report = m._failure_report()
                self.assertEqual(set(report), BASE_KEYS)
                self.assert_redacted(report)

    def test_progress_and_wrong_phase_clear_stale_attribution(self):
        self.note()
        m._progress("source_recheck")
        self.assertNotIn("docx_refusal", m._FAILURE.get())
        self.note()
        self.assertEqual(set(m._failure_report()), BASE_KEYS)
        m._progress("case_validation", case="rendered_reflow_refusal", exit_code=1)
        self.note(case="rendered_reflow_refusal")
        self.assertEqual(set(m._failure_report()), BASE_KEYS)
        m._progress("case_validation", case="styled_multipage_fit", exit_code=2)
        self.note()
        self.assertEqual(set(m._failure_report()), BASE_KEYS)

    def test_reporter_distrusts_arbitrary_context_and_forged_projection(self):
        hostile = Untrusted()
        for stored in (hostile, "PRIVATE_CONTEXT", None,
                       {"stage": hostile, "case": hostile, "exit_code": True, "completed_cases": hostile},
                       {"stage": "case_validation", "case": "styled_multipage_fit", "exit_code": 1,
                        "docx_refusal": {"error": "docx_layout_refused", "reason": "PRIVATE_RAW_REASON"}},
                       {"stage": "case_validation", "case": "styled_multipage_fit", "exit_code": 1,
                        "docx_refusal": m._DocxRefusal(hostile, hostile, hostile)}):
            token = m._FAILURE.set(stored)
            try:
                self.assertEqual(set(m._failure_report()), BASE_KEYS)
                self.assert_redacted(m._failure_report())
                self.note()
                m._progress("setup")
                self.assertEqual(set(m._failure_report()), BASE_KEYS)
            finally:
                m._FAILURE.reset(token)

    def fit_result(self):
        scan = {"engine": "ClamAV", "version": "PRIVATE_SCANNER_VERSION", "definitions": "PRIVATE_DEFINITIONS"}
        return {**self.binding, "scan": scan, "candidate_scan": scan.copy(), "layout": {
            "validator": "hirewiz-docx-layout-v1", "pages": 3, "unchanged_links": 2,
            "outside_changed_regions_pixel_delta": 0, "renderer": "PRIVATE_FONT_AND_VERSION",
        }}

    def test_bound_exit_zero_fit_failure_exports_only_check_names_and_counts(self):
        result = self.fit_result()
        m._progress("case_validation", case="styled_multipage_fit", exit_code=0)
        m._note_docx_fit_mismatch(result, self.binding, 0, case="styled_multipage_fit")
        self.assertNotIn("PRIVATE", repr(m._FAILURE.get()))
        result["layout"].update(pages=20, unchanged_links=100)
        report = m._failure_report()
        self.assertEqual(set(report), BASE_KEYS | {"fit_failed_checks", "fit_pages", "fit_links"})
        self.assertEqual(report["fit_failed_checks"], ["pages", "unchanged_links"])
        self.assertEqual((report["fit_pages"], report["fit_links"]), (3, 2))
        self.assert_redacted(report)
        m._progress("source_recheck")
        self.assertNotIn("docx_fit_mismatch", m._FAILURE.get())
        m._progress("case_validation", case="styled_multipage_fit", exit_code=0)
        result["layout"].update(pages=2, unchanged_links=1)
        m._note_docx_fit_mismatch(result, self.binding, 0, case="styled_multipage_fit")
        self.assertEqual(set(m._failure_report()), BASE_KEYS)

    def test_fit_failure_names_and_counts_are_independently_redacted(self):
        result = self.fit_result()
        result["layout"].update(validator="PRIVATE_VALIDATOR", pages=True, unchanged_links=101,
                                outside_changed_regions_pixel_delta=1)
        result["scan"]["engine"] = "PRIVATE_ENGINE"
        m._progress("case_validation", case="styled_multipage_fit", exit_code=0)
        m._note_docx_fit_mismatch(result, self.binding, 0, case="styled_multipage_fit")
        report = m._failure_report()
        self.assertEqual(report["fit_failed_checks"], list(m._DOCX_FIT_CHECKS))
        self.assertEqual(set(report), BASE_KEYS | {"fit_failed_checks"})
        self.assert_redacted(report)
        for bad_result, code in (({**result, "sha256": "d" * 64}, 0),
                                 ({**result, "raw_body": "PRIVATE_BODY"}, 0), (result, False), (result, 1)):
            m._note_docx_fit_mismatch(bad_result, self.binding, code, case="styled_multipage_fit")
            self.assertEqual(set(m._failure_report()), BASE_KEYS)
        hostile = Untrusted()
        for fit in (m._DocxFitMismatch(hostile, ("pages",), 1, 1),
                    m._DocxFitMismatch("styled_multipage_fit", (hostile,), 1, 1),
                    m._DocxFitMismatch("styled_multipage_fit", ("PRIVATE_UNKNOWN",), 1, 1),
                    m._DocxFitMismatch("styled_multipage_fit", ("pages", "pages"), 1, 1)):
            m._FAILURE.get()["docx_fit_mismatch"] = fit
            self.assertEqual(set(m._failure_report()), BASE_KEYS)
        m._FAILURE.get()["docx_fit_mismatch"] = m._DocxFitMismatch("styled_multipage_fit", ("pages",), False, hostile)
        self.assertEqual(set(m._failure_report()), BASE_KEYS | {"fit_failed_checks"})
        self.assert_redacted(m._failure_report())

    def test_link_probe_is_failure_only_bounded_and_value_free(self):
        compile(m.DOCX_LINK_PROGRAM, "fixed_link_diagnostic", "exec")
        result = self.fit_result()
        result["layout"].update(pages=2, unchanged_links=0)
        observed = dict(zip(m._LINK_FIELDS, (1, 1, 0, True, True, False), strict=True))
        for extra in ({}, {"URI": "PRIVATE_URI_AND_BODY"}):
            with tempfile.TemporaryDirectory() as temporary:
                request = Path(temporary) / "request.json"
                request.write_text("synthetic; container mocked")
                m._progress("case_validation", case="styled_multipage_fit", exit_code=0)
                with patch.object(m, "container", return_value=(0, json.dumps({**observed, **extra}).encode())) as child:
                    m._diagnose_docx_links(["docker"], "synthetic-image", "linux/amd64", request,
                                          result, self.binding, 0, case="styled_multipage_fit")
                self.assertEqual(child.call_count, 1)
                self.assertEqual(child.call_args.args[3], ["-c", m.DOCX_LINK_PROGRAM])
                mounts = child.call_args.args[4]
                self.assertEqual(json.loads(mounts[1][0].read_bytes()), self.binding)
                report = m._failure_report()
                self.assertEqual(report["link_diagnostic_container_calls"], 1)
                self.assertGreaterEqual(report["link_diagnostic_seconds"], 0)
                self.assertLessEqual(report["link_diagnostic_seconds"], 150)
                self.assertEqual(report["link_diagnostic_status"], "unavailable" if extra else "observed")
                self.assertEqual(report.get("link_diagnostic"), None if extra else observed)
                self.assert_redacted(report)
                m._progress("source_recheck")
                self.assertNotIn("docx_link_observation", m._FAILURE.get())
        for pages, links in ((2, 1), (3, 0)):
            result["layout"].update(pages=pages, unchanged_links=links)
            with patch.object(m, "container", side_effect=AssertionError("outside scope reached container")) as child:
                m._progress("case_validation", case="styled_multipage_fit", exit_code=0)
                m._diagnose_docx_links([], "unused", "unused", Path("unused"), result,
                                      self.binding, 0, case="styled_multipage_fit")
                child.assert_not_called()
                m._FAILURE.get()["docx_link_observation"] = m._DocxLinkObservation(1, 0.1, tuple(observed.values()))
                self.assertNotIn("link_diagnostic", m._failure_report())


if __name__ == "__main__":
    unittest.main()
