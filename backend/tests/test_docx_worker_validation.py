"""Standalone boundary controls with synthetic scan/render adapters.

Direct execution avoids app initializers and global conftest. Real PDF comparison
runs, but these controls are not LibreOffice, antivirus or native isolation proof.
"""
from __future__ import annotations

import base64
import importlib.util
import io
import json
import sys
import tempfile
import time
import types
import unittest
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / "backend/app/services"
PREFIX = "hirewiz_docx_worker_isolated"
IMAGE = "registry.example/worker@sha256:" + "a" * 64
POLICY = "b" * 64
FONT_HASH = "c" * 64
VERSION = "Synthetic renderer fixture 1"
SCAN = {"engine": "ClamAV", "version": "1.4.3", "definitions": "1/2026-10-10T00:00:00+00:00"}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@contextmanager
def modules():
    # Each method restores existing modules; full CI cannot replace a live app.
    with patch.dict(sys.modules):
        package = types.ModuleType(PREFIX)
        package.__path__ = [str(SERVICES)]
        sys.modules[PREFIX] = package
        core = load(PREFIX + ".docx_layout_validation", SERVICES / "docx_layout_validation.py")
        layout = load(PREFIX + ".resume_layout", SERVICES / "resume_layout.py")
        ingestion = load(PREFIX + ".document_ingestion", SERVICES / "document_ingestion.py")
        artifact = load(PREFIX + ".docx_artifact", SERVICES / "docx_artifact.py")
        fixtures = load(PREFIX + ".fixtures", ROOT / "backend/tests/test_docx_layout_validation.py")
        app = types.ModuleType("app")
        app.__path__ = []
        service_package = types.ModuleType("app.services")
        service_package.__path__ = []
        sys.modules.update({"app": app, "app.services": service_package,
            "app.services.docx_layout_validation": core, "app.services.resume_layout": layout})
        processor = load("processor", ROOT / "infra/document-processing/processor.py")
        service = load("service", ROOT / "infra/document-processing/service.py")
        renderer = load("docx_renderer", ROOT / "infra/document-processing/docx_renderer.py")
        yield types.SimpleNamespace(core=core, layout=layout, ingestion=ingestion,
            artifact=artifact, fixtures=fixtures, processor=processor, service=service, renderer=renderer)


def request(m):
    source = m.fixtures.docx()
    edits = [m.fixtures.edit(source)]
    candidate = m.layout._apply_docx(source, edits)
    expected = m.artifact._binding(source, candidate, edits)
    payload = m.artifact._canonical({"version": 1, "source_format": "docx", "sha256": expected["sha256"],
        "content_base64": base64.b64encode(source).decode(), "edits": edits,
        "candidate_sha256": expected["candidate_sha256"], "candidate_size_bytes": len(candidate),
        "worker_image": IMAGE, "policy_sha256": POLICY})
    return source, candidate, edits, expected, payload


def render_adapter(m):
    def render(source, candidate, edits, *, image, policy, deadline):
        assert deadline > time.monotonic()
        return m.core.validate_docx_layout(source_docx=source, candidate_docx=candidate, edits=edits,
            before_pdf=m.fixtures.pdf(), after_pdf=m.fixtures.pdf(m.fixtures.REPLACEMENT),
            renderer=m.core.RendererIdentity(image, policy, VERSION, FONT_HASH)).to_dict()
    return render


class DocxWorkerValidationTests(unittest.TestCase):
    def test_exact_regeneration_dual_scan_and_real_pdf_comparison(self):
        with modules() as m:
            source, candidate, _, expected, payload = request(m)
            scans = []
            scan_deadlines = []
            def scan(path, *, deadline):
                self.assertGreater(deadline, time.monotonic())
                scan_deadlines.append(deadline)
                scans.append(path.read_bytes())
                return SCAN.copy()
            with patch.object(m.processor, "scan_original", scan), patch.object(m.renderer,
                    "validate_rendered_edit", render_adapter(m)):
                result = m.processor.validate_docx_edit(payload)
            self.assertEqual(scans, [source, candidate])
            self.assertEqual(len(scan_deadlines), 2)
            self.assertEqual(scan_deadlines[0], scan_deadlines[1])
            self.assertEqual({key: result[key] for key in expected}, expected)
            self.assertEqual(result["layout"]["candidate_sha256"], expected["candidate_sha256"])
            self.assertNotIn(m.fixtures.ORIGINAL, json.dumps(result))

    def test_safety_candidate_mismatch_and_mutation_stop_before_renderer(self):
        with modules() as m:
            _, _, _, _, payload = request(m)
            with patch.object(m.renderer, "validate_rendered_edit", side_effect=AssertionError("renderer reached")):
                with patch.object(m.processor, "scan_original", side_effect=m.processor.InspectionDenied()):
                    with self.assertRaises(m.processor.InspectionDenied):
                        m.processor.validate_docx_edit(payload)
                changed = json.loads(payload)
                changed["candidate_sha256"] = "d" * 64
                with patch.object(m.processor, "scan_original", return_value=SCAN):
                    with self.assertRaises(m.processor.InspectionUnavailable):
                        m.processor.validate_docx_edit(json.dumps(changed).encode())
                def mutate(path, *, deadline):
                    self.assertGreater(deadline, time.monotonic())
                    path.write_bytes(b"changed by synthetic scanner")
                    return SCAN.copy()
                with patch.object(m.processor, "scan_original", mutate):
                    with self.assertRaises(m.processor.InspectionUnavailable):
                        m.processor.validate_docx_edit(payload)

    def test_sandbox_fixed_mode_binding_and_cleanup_failure_refuses_release(self):
        with modules() as m:
            _, _, _, _, payload = request(m)
            with patch.object(m.processor, "scan_original", return_value=SCAN), patch.object(m.renderer,
                    "validate_rendered_edit", render_adapter(m)):
                result = m.processor.validate_docx_edit(payload)
            calls = []
            def run(args, **options):
                calls.append((args, options))
                return (0, b"") if args[1] == "delete" else (0, json.dumps(result).encode())
            with patch.dict("os.environ", DOCUMENT_WORKER_IMAGE_DIGEST=IMAGE, DOCUMENT_WORKER_POLICY_SHA256=POLICY), \
                 patch.object(m.service.os.path, "isfile", return_value=True), \
                 patch.object(m.service.os, "access", return_value=True), patch.object(m.service, "_run", run):
                admitted = m.service.inspect_in_sandbox(payload, docx_layout=True)
                self.assertEqual(admitted["worker_image"], IMAGE)
                self.assertEqual([row[0][1] for row in calls], ["do", "delete"])
                self.assertEqual(calls[0][0][3:], ["--write", "--", "/usr/local/bin/python3", "-I",
                                                  "/opt/document/processor.py", "--docx-layout-stdin"])
                self.assertEqual(calls[0][1], {"timeout": 55, "limit": 32768, "allow_outcome": True, "input_bytes": payload})
                self.assertEqual(calls[1][1], {"timeout": 5, "limit": 65536, "cleanup": True})
                def failed_delete(args, **options):
                    if args[1] == "delete":
                        raise m.service.InspectionDenied()
                    return 0, json.dumps(result).encode()
                with patch.object(m.service, "_run", failed_delete), self.assertRaises(m.service.InspectionDenied):
                    m.service.inspect_in_sandbox(payload, docx_layout=True)

    def test_prepare_once_seals_exact_bytes_download_is_offline_and_tamper_refuses(self):
        with modules() as m:
            source, candidate, edits, _, payload = request(m)
            with patch.object(m.processor, "scan_original", return_value=SCAN), patch.object(m.renderer,
                    "validate_rendered_edit", render_adapter(m)):
                result = m.processor.validate_docx_edit(payload)
            result.update(worker_image=IMAGE, policy_sha256=POLICY)
            calls = []
            async def http(config, data, token, expected, supplied_edits):
                calls.append(data)
                self.assertEqual(data, payload)
                self.assertEqual(supplied_edits, edits)
                self.assertEqual(config.audience, "https://worker.test.run.app")
                return json.dumps(result).encode()
            with patch.dict("os.environ", DOCUMENT_WORKER_URL="https://worker.test.run.app/inspect",
                    DOCUMENT_WORKER_IMAGE_DIGEST=IMAGE, DOCUMENT_WORKER_POLICY_SHA256=POLICY,
                    DOCUMENT_WORKER_DOCX_RENDERER_VERSION=VERSION, DOCUMENT_WORKER_DOCX_FONT_MANIFEST_SHA256=FONT_HASH), \
                 patch.object(m.ingestion, "_identity_token", return_value="synthetic-private-token"), \
                 patch.object(m.artifact, "_worker_http", http):
                seal = m.artifact.prepare_artifact(source, edits)
            self.assertEqual(len(calls), 1)
            with patch.object(m.artifact, "_worker_http", side_effect=AssertionError("download called worker")), \
                 patch.dict("os.environ", DOCUMENT_WORKER_IMAGE_DIGEST="different-current-worker"):
                self.assertEqual(m.artifact.sealed_bytes(seal, source, edits), candidate)
                altered = json.loads(json.dumps(seal))
                altered["validation"]["layout"]["pages"] = True
                with self.assertRaises(m.layout.ResumeLayoutError):
                    m.artifact.sealed_bytes(altered, source, edits)
                with self.assertRaises(m.layout.ResumeLayoutError):
                    m.artifact.sealed_bytes(seal, source, [{**edits[0], "replacement_text": "Different wording"}])

    def test_fixed_writer_paths_exact_output_source_mutation_and_size_refuse(self):
        with modules() as m, tempfile.TemporaryDirectory() as temporary:
            source = m.fixtures.docx()
            captured = []
            def run(args, **options):
                captured.append((args, options))
                output = Path(args[args.index("--outdir") + 1])
                (output / "input.pdf").write_bytes(m.fixtures.pdf())
                return b""
            with patch.object(m.renderer, "_run", run):
                pdf = m.renderer._render(source, Path(temporary) / "supported", deadline=time.monotonic() + 10)
            self.assertTrue(pdf.startswith(b"%PDF-"))
            self.assertEqual(captured[0][0][:5], ["/usr/bin/soffice", "--headless", "--nologo", "--nodefault", "--norestore"])
            export = captured[0][0][captured[0][0].index("--convert-to") + 1]
            self.assertTrue(export.startswith("pdf:writer_pdf_Export:"))
            self.assertEqual(json.loads(export.removeprefix("pdf:writer_pdf_Export:")), {
                key: {"type": "boolean", "value": value} for key, value in {
                    "ExportBookmarks": "true", "UseTaggedPDF": "false", "ExportFormFields": "false",
                    "ExportNotes": "false", "ExportNotesPages": "false", "IsAddStream": "false",
                }.items()
            })
            self.assertTrue(captured[0][0][-1].endswith("/input.docx"))
            self.assertNotIn(m.fixtures.ORIGINAL, " ".join(captured[0][0]))
            def mutation(args, **options):
                run(args, **options)
                Path(args[-1]).write_bytes(b"mutated original")
                return b""
            with patch.object(m.renderer, "_run", mutation), self.assertRaises(m.renderer.RendererUnavailable):
                m.renderer._render(source, Path(temporary) / "mutation", deadline=time.monotonic() + 10)
            with self.assertRaises(m.renderer.RendererUnavailable):
                m.renderer._render(b"x" * (5 * 1024 * 1024 + 1), Path(temporary) / "oversize", deadline=time.monotonic() + 10)

    def test_missing_native_renderer_pin_stops_before_identity_or_http(self):
        with modules() as m:
            source, _, edits, _, _ = request(m)
            with patch.dict("os.environ", DOCUMENT_WORKER_URL="https://worker.test.run.app/inspect",
                    DOCUMENT_WORKER_IMAGE_DIGEST=IMAGE, DOCUMENT_WORKER_POLICY_SHA256=POLICY,
                    DOCUMENT_WORKER_DOCX_RENDERER_VERSION="", DOCUMENT_WORKER_DOCX_FONT_MANIFEST_SHA256=""), \
                 patch.object(m.ingestion, "_identity_token", side_effect=AssertionError("identity reached")), \
                 patch.object(m.artifact, "_worker_http", side_effect=AssertionError("HTTP reached")):
                with self.assertRaises(m.artifact.DocxRendererUnavailable) as caught:
                    m.artifact.prepare_artifact(source, edits)
                self.assertIs(caught.exception.content_repair_allowed, False)
                self.assertIsInstance(caught.exception, m.layout.ResumeLayoutError)

    def test_distro_version_suffix_preserved_and_control_payload_refused(self):
        with modules() as m:
            for version in ("LibreOffice 7.4.7.2 40(Build:2)", "LibreOffice 24.8.7.2 bd155fa"):
                with patch.object(m.renderer, "_run", return_value=(version + "\n").encode()):
                    self.assertEqual(m.renderer._version(deadline=time.monotonic() + 10), version)
            for output in (b"unrecognized renderer 7.4.7", b"LibreOffice 7.4.7\nprivate data",
                           b"LibreOffice 7.4.7 " + b"x" * 200):
                with patch.object(m.renderer, "_run", return_value=output), self.assertRaises(m.renderer.RendererUnavailable):
                    m.renderer._version(deadline=time.monotonic() + 10)

    def test_valid_shape_with_wrong_receipt_identity_or_dirty_scan_is_refused(self):
        with modules() as m:
            _, _, _, expected, payload = request(m)
            with patch.object(m.processor, "scan_original", return_value=SCAN), patch.object(m.renderer,
                    "validate_rendered_edit", render_adapter(m)):
                result = m.processor.validate_docx_edit(payload)
            result.update(worker_image=IMAGE, policy_sha256=POLICY)
            for key in ("fonts", "candidate", "scan", "pixels"):
                altered = deepcopy(result)
                if key == "fonts":
                    altered["layout"]["renderer"]["font_manifest_sha256"] = "d" * 64
                elif key == "candidate":
                    altered["layout"]["candidate_sha256"] = "d" * 64
                elif key == "scan":
                    altered["candidate_scan"]["engine"] = "not-clean"
                else:
                    altered["layout"]["outside_changed_regions_pixel_delta"] = 1
                with self.subTest(key=key), self.assertRaises((ValueError, m.core.DocxLayoutError)):
                    m.artifact._check_success(altered, expected, image=IMAGE, policy=POLICY, version=VERSION, fonts=FONT_HASH)

    def test_explicit_stdin_mode_and_binding_never_fall_back_to_file_cli(self):
        with modules() as m:
            _, _, _, expected, payload = request(m)
            output = io.StringIO()
            with patch.object(m.processor.sys, "argv", ["processor.py", "--docx-layout-stdin"]), \
                 patch.object(m.processor.sys, "stdin", types.SimpleNamespace(buffer=io.BytesIO(payload))), \
                 patch.object(m.processor.resource, "setrlimit"), patch.object(m.processor.os, "umask"), \
                 patch.object(m.processor.logging, "disable"), patch.object(m.processor.warnings, "simplefilter"), \
                 patch.object(m.processor, "validate_docx_edit", return_value=expected), patch("sys.stdout", output):
                m.processor.main()
            self.assertEqual(json.loads(output.getvalue()), expected)

    def test_native_linux_fixture_factory_generates_exact_protocol_candidates(self):
        with modules() as m:
            builder = load(PREFIX + ".native_fixture", ROOT / "backend/scripts/document_test_image.py")
            with patch.object(builder, "docker_command", side_effect=AssertionError("Docker reached")):
                rows = builder.docx_layout_corpus("sha256:" + "e" * 64)
            self.assertEqual([row[0] for row in rows], ["styled_multipage_fit", "rendered_reflow_refusal"])
            for _, payload, binding in rows:
                source, decoded = m.processor.decode_docx_request(payload)
                self.assertEqual(decoded["binding"], binding)
                candidate = m.layout._apply_docx(source, decoded["request"]["edits"])
                self.assertEqual(len(m.core._native_edits(source, candidate, decoded["request"]["edits"])), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
