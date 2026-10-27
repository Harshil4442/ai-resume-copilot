"""Separate V3 candidate native probe. Frozen V2 evidence remains unchanged.

Run only as the reviewed immutable image Job's /usr/local/bin/python3 -I -c
command. It has no SDK/provider client, accepts no argv or arbitrary content,
and never changes production source, native launchers, fixtures or gates.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

IMMUTABLE_IMAGE = 'us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-document-inspector@sha256:0000000000000000000000000000000000000000000000000000000000000000'
POLICY_SHA256 = '8ed5f38142d88d88883129681953e939e79157e8b92e3efc516920d2e4409c9f'
MAX_REPORT = 16 * 1024
CASE_NAMES = ("clean_pdf", "clean_docx", "eicar_exact_file", "clean_tex", "clean_texzip",
              "eicar_tex_source", "eicar_texzip_member", "eicar_direct_detection",
              "styled_multipage_fit", "rendered_reflow_refusal", "unsupported_source_font_refusal")
REFLOW_REASONS = frozenset({"line_wrap_changed", "page_geometry_changed", "paragraph_page_changed",
                           "paragraph_overflow", "hyperlink_changed"})
RUN_OUTCOMES = frozenset({"pending", "returned", "failed", "deadline", "unexpected_exit", "reap_failed", "not_started"})
PROCESSOR_OUTCOMES = frozenset({"not_validated", "clean", "refused", "unavailable"})
VALIDATION_PHASES = frozenset({"not_started", "decode", "binding", "outcome", "complete"})
FAILURE_PHASES = frozenset({"admission", "launcher", "validation", "processor", "cleanup", "response"})
ACTIVATION_FLAGS = {"api_wiring_enabled": False, "candidate_submission_enabled": False,
                    "checkout_enabled": False, "customer_processing_enabled": False,
                    "deployment_proven": False, "checkpoint_complete": False}
EICAR = (b"X5O!P%@AP[4" + bytes([92]) + b"PZX54(P^)7CC)7}$"
         + b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*")


class ProbeRefused(ValueError):
    pass


# Name retained solely so the original fixed public generator is unchanged.
DocumentImageError = ProbeRefused


def require(condition):
    if condition is not True:
        raise ProbeRefused()


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result)
        result[key] = value
    return result


def parse(raw, limit):
    require(type(raw) is bytes and 0 < len(raw) <= limit)
    result = json.loads(raw, object_pairs_hook=pairs,
                        parse_constant=lambda _: (_ for _ in ()).throw(ProbeRefused()))
    require(type(result) is dict)
    return result


def same_binding(result, expected):
    return type(result) is dict and all(type(result.get(k)) is type(v) and result.get(k) == v
                                        for k, v in expected.items())


def scan_projection(scan):
    require(type(scan) is dict and set(scan) == {"engine", "version", "definitions"} and scan["engine"] == "ClamAV")
    require(all(type(scan[k]) is str and 0 < len(scan[k]) <= 128
                and all(32 <= ord(c) <= 126 for c in scan[k]) for k in ("version", "definitions")))
    return {k: scan[k] for k in ("engine", "version", "definitions")}


def diagnostic_projection(diagnostic):
    result = {}
    for key, allowed in (("launcher", RUN_OUTCOMES), ("cleanup", RUN_OUTCOMES | {"not_required"}),
                         ("processor", PROCESSOR_OUTCOMES), ("validation", VALIDATION_PHASES)):
        value = getattr(diagnostic, key)
        require(type(value) is str and value in allowed)
        result[key] = value
    phase = diagnostic.failure_phase
    require(phase is None or (type(phase) is str and phase in FAILURE_PHASES))
    result["failure_phase"] = phase
    for key in ("launcher_code", "cleanup_code"):
        value = getattr(diagnostic, key)
        require(value is None or (type(value) is int and -255 <= value <= 255))
        result[key] = value
    for key in ("launcher_elapsed_ms", "cleanup_elapsed_ms"):
        value = getattr(diagnostic, key)
        require(value is None or (type(value) is int and 0 <= value <= 120000))
        result[key] = value
    return result


def cleanup_success(diagnostic):
    return diagnostic.cleanup == "returned" and type(diagnostic.cleanup_code) is int and diagnostic.cleanup_code == 0


def auxiliary(service, program, diagnostic):
    """Same owned launcher/delete and bounds; no launcher replacement."""
    name = "document-" + uuid.uuid4().hex
    cleanup_required = True
    try:
        diagnostic.begin_run()
        try:
            code, raw = service._run([service.SANDBOX, "do", "--sandbox-name=" + name, "--write", "--",
                "/usr/local/bin/python3", "-I", "-c", program], timeout=55, limit=16384, allow_outcome=True)
        except BaseException as exc:
            diagnostic.end_run(error=exc)
            if isinstance(exc, service._LauncherRefused):
                cleanup_required = exc.authorized
            raise
        diagnostic.end_run(code)
        require(type(code) is int and code == 0)
        return parse(raw, 16384)
    finally:
        if cleanup_required:
            diagnostic.begin_run(cleanup=True)
            try:
                cleanup_code, _ = service._run([service.SANDBOX, "delete", name, "--force"],
                                               timeout=5, limit=65536, cleanup=True)
            except BaseException as exc:
                diagnostic.end_run(error=exc, cleanup=True)
                raise
            else:
                diagnostic.end_run(cleanup_code, cleanup=True)
                require(type(cleanup_code) is int and cleanup_code == 0)


EXPECTED_RUNTIME_HASHES = {'/opt/document/processor.py': '208eff8d08a9104af25165f6181a48b8d7939d14f2436512b430c1f77c7a75fe', '/opt/document/service.py': '5031bf49452abce33c42f825adeb789a0af7b3dfbddd7b27593ca472dd90097e', '/opt/document/docx_renderer.py': '5b22690a788ee4ed3053d4837973c1ca20c5b6cb463caab688019e24ca38b97a', '/opt/document/writer_owner.py': '2431048a9cf0867a7c016da912c54df54c683b9b3244309cfe36e5e4ee39b16e', '/opt/document/courier_prime_fonts.py': 'c81f81a53811e9f3a29129a66096fcb126af77a7fa2e310c86cb3f97c7f7b6a1', '/opt/document/app/services/parsing.py': '8d35401fe06124c259d845cd84887cfcf5f289b179af990f4716014a3cee0793', '/opt/document/app/services/market/skill_extractor.py': '765824e77d2e9e0bfc88c910134a5009dd3ded52eccc958407d8173039746f54', '/opt/document/app/services/market/skill_taxonomy.py': 'e210d0c2b97567fae690d4710ffffb1a5a93bb5bb732badcff47cc2839296366', '/opt/document/app/services/market/security.py': 'a440f4ea26a5fb5982690da8399c75983e9133a23501d12e1973a985487200f6', '/opt/document/app/services/resume_layout.py': 'd46e28c8c0a5ff0734f84ce4bda6bc42377beb9afbc6c1d90e8f1cc215261146', '/opt/document/app/services/native_project.py': '9dcbddd7ed5592ba597d1e77d82ab078e291b1bfc8439db7171145b0635f984f', '/opt/document/app/services/pdf_streams.py': '72c43333a814a309622d7465e858118b3727ee958078775727a6560a0a7c5389', '/opt/document/app/services/docx_layout_validation.py': '7844ca9decc8b9f1845e5b61b6de64f3e129f37539003e154c53e17d25719b16', '/opt/document/app/services/docx_fonts.py': '66cd6956f1b1a49c5d8c14ee909c11779f4f7f84e17069ca8f66730abf286770', '/usr/share/fonts/truetype/hirewiz-courier-prime/CourierPrime-Regular.ttf': '72f793376f8e2841656bf21d77a5de010f2929bd6956a22ee848ad0c7eb978af', '/usr/share/fonts/truetype/hirewiz-courier-prime/CourierPrime-Bold.ttf': 'ff1f38786c849d1c41fa8e447960abdb2bd75fdfb0cfcdeb524fad65a5af3638', '/usr/share/fonts/truetype/hirewiz-courier-prime/CourierPrime-Italic.ttf': 'f1b9a5829789f7e56432a9f3bc7665ef4531dbba1c112639e48bef39621a006b', '/usr/share/fonts/truetype/hirewiz-courier-prime/CourierPrime-BoldItalic.ttf': '3355273f3ea6d6362658f9564f4e83d59e156cf1a182e3c1592b5c5943627dcc', '/usr/share/doc/hirewiz-courier-prime/OFL.txt': '9a755af092b494944c99f471be6fddd19b006a448fefdc4717e4ee0aa09a97b0', '/usr/share/doc/hirewiz-courier-prime/provenance.json': '957229df76719eb4c3bef3a3226dedfd078ad38e63685250fb72283a9b3d9e6a'}
PREFLIGHT_PROGRAM = 'import hashlib,json,os,platform,re,stat,sys,time\nfrom pathlib import Path\nsys.path.insert(0,"/opt/document")\nimport docx_renderer,courier_prime_fonts\nexpected = {\'/opt/document/processor.py\': \'208eff8d08a9104af25165f6181a48b8d7939d14f2436512b430c1f77c7a75fe\', \'/opt/document/service.py\': \'5031bf49452abce33c42f825adeb789a0af7b3dfbddd7b27593ca472dd90097e\', \'/opt/document/docx_renderer.py\': \'5b22690a788ee4ed3053d4837973c1ca20c5b6cb463caab688019e24ca38b97a\', \'/opt/document/writer_owner.py\': \'2431048a9cf0867a7c016da912c54df54c683b9b3244309cfe36e5e4ee39b16e\', \'/opt/document/courier_prime_fonts.py\': \'c81f81a53811e9f3a29129a66096fcb126af77a7fa2e310c86cb3f97c7f7b6a1\', \'/opt/document/app/services/parsing.py\': \'8d35401fe06124c259d845cd84887cfcf5f289b179af990f4716014a3cee0793\', \'/opt/document/app/services/market/skill_extractor.py\': \'765824e77d2e9e0bfc88c910134a5009dd3ded52eccc958407d8173039746f54\', \'/opt/document/app/services/market/skill_taxonomy.py\': \'e210d0c2b97567fae690d4710ffffb1a5a93bb5bb732badcff47cc2839296366\', \'/opt/document/app/services/market/security.py\': \'a440f4ea26a5fb5982690da8399c75983e9133a23501d12e1973a985487200f6\', \'/opt/document/app/services/resume_layout.py\': \'d46e28c8c0a5ff0734f84ce4bda6bc42377beb9afbc6c1d90e8f1cc215261146\', \'/opt/document/app/services/native_project.py\': \'9dcbddd7ed5592ba597d1e77d82ab078e291b1bfc8439db7171145b0635f984f\', \'/opt/document/app/services/pdf_streams.py\': \'72c43333a814a309622d7465e858118b3727ee958078775727a6560a0a7c5389\', \'/opt/document/app/services/docx_layout_validation.py\': \'7844ca9decc8b9f1845e5b61b6de64f3e129f37539003e154c53e17d25719b16\', \'/opt/document/app/services/docx_fonts.py\': \'66cd6956f1b1a49c5d8c14ee909c11779f4f7f84e17069ca8f66730abf286770\', \'/usr/share/fonts/truetype/hirewiz-courier-prime/CourierPrime-Regular.ttf\': \'72f793376f8e2841656bf21d77a5de010f2929bd6956a22ee848ad0c7eb978af\', \'/usr/share/fonts/truetype/hirewiz-courier-prime/CourierPrime-Bold.ttf\': \'ff1f38786c849d1c41fa8e447960abdb2bd75fdfb0cfcdeb524fad65a5af3638\', \'/usr/share/fonts/truetype/hirewiz-courier-prime/CourierPrime-Italic.ttf\': \'f1b9a5829789f7e56432a9f3bc7665ef4531dbba1c112639e48bef39621a006b\', \'/usr/share/fonts/truetype/hirewiz-courier-prime/CourierPrime-BoldItalic.ttf\': \'3355273f3ea6d6362658f9564f4e83d59e156cf1a182e3c1592b5c5943627dcc\', \'/usr/share/doc/hirewiz-courier-prime/OFL.txt\': \'9a755af092b494944c99f471be6fddd19b006a448fefdc4717e4ee0aa09a97b0\', \'/usr/share/doc/hirewiz-courier-prime/provenance.json\': \'957229df76719eb4c3bef3a3226dedfd078ad38e63685250fb72283a9b3d9e6a\'}\nactual={name:hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in expected}\nif actual!=expected or platform.machine()!="x86_64": raise SystemExit(1)\nuid,gid=os.getuid(),os.getgid()\nif type(uid) is not int or type(gid) is not int or not 0<=uid<=4294967295 or not 0<=gid<=4294967295: raise SystemExit(1)\nmanifest=docx_renderer.MANIFEST\nreadonly=(not manifest.is_symlink() and manifest.is_file() and stat.S_IMODE(manifest.stat().st_mode)==0o444)\nif not readonly: raise SystemExit(1)\ncourier_prime_fonts.verify_bundle()\nversion,fonts=docx_renderer.font_manifest(deadline=time.monotonic()+45)\nif actual!={name:hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in expected}: raise SystemExit(1)\nprint(json.dumps({"runtime_hashes":actual,"architecture":platform.machine(),"runtime_uid":uid,"runtime_gid":gid,\n"readonly_font_manifest":readonly,"font_manifest_process_writable":os.access(manifest,os.W_OK),\n"renderer_version":version,"font_manifest_sha256":fonts,"courier_bundle_verified":True},separators=(",",":"),allow_nan=False))\n'

EICAR_DIRECT_PROGRAM = 'import hashlib,json,pathlib,sys,tempfile\nsys.path.insert(0,"/opt/document")\nimport processor\ncontent=(b"X5O!P%@AP[4"+bytes([92])+b"PZX54(P^)7CC)7}$"+b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*")\nif len(content)!=68: raise SystemExit(1)\ndetected=False\nwith tempfile.TemporaryDirectory(prefix="eicar-native-",dir="/tmp") as temporary:\n directory=pathlib.Path(temporary);directory.chmod(0o700)\n target=directory/"antivirus-test.pdf";target.write_bytes(content);target.chmod(0o600)\n try: processor.scan_original(target)\n except processor.InspectionDenied: detected=True\n unchanged=target.read_bytes()==content\n if not detected or not unchanged: raise SystemExit(1)\n print(json.dumps({"sha256":hashlib.sha256(content).hexdigest(),"size_bytes":len(content),\n "exact_file_unchanged":unchanged,"coherent_eicar_detection":detected},separators=(",",":"),allow_nan=False))\n'

def corpus() -> list[tuple[str, str, bytes, str]]:
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from pypdf import PdfWriter

    def pdf(active=False):
        writer = PdfWriter()
        writer.add_blank_page(200, 200)
        if active:
            writer.add_js("this.print();")
        stream = io.BytesIO()
        writer.write(stream)
        return stream.getvalue()
    def docx(field=None):
        document = Document()
        document.add_paragraph("Candidate Example")
        document.add_paragraph("SKILLS")
        document.add_paragraph("Python, SQL")
        if field:
            node = OxmlElement("w:fldSimple")
            node.set(qn("w:instr"), field)
            document.add_paragraph()._p.append(node)
        stream = io.BytesIO()
        document.save(stream)
        return stream.getvalue()
    clean_docx = docx()
    infected = io.BytesIO()
    with ZipFile(io.BytesIO(clean_docx)) as original, ZipFile(infected, "w", ZIP_DEFLATED) as output:
        for entry in original.infolist():
            output.writestr(entry, original.read(entry))
        output.writestr("word/standalone-antivirus-test.txt", EICAR)
    clean_tex = rb"\documentclass{article}\begin{document}Candidate Example: Python, SQL\end{document}"
    texzip = io.BytesIO()
    infected_texzip = io.BytesIO()
    project_main = rb"\documentclass{article}\usepackage{graphicx}\begin{document}\input{sections/skills}\includegraphics{asset.pdf}\end{document}"
    for target, detected in ((texzip, False), (infected_texzip, True)):
        with ZipFile(target, "w", ZIP_DEFLATED) as output:
            output.writestr("resume.tex", project_main)
            output.writestr("sections/skills.tex", b"Python, SQL")
            output.writestr("asset.pdf", pdf())
            if detected:
                output.writestr("standalone-antivirus-test.tex", EICAR)
    if len(EICAR) != 68:
        raise DocumentImageError()
    return [("clean_pdf", "pdf", pdf(), "clean"), ("clean_docx", "docx", clean_docx, "clean"),
            ("page_counter", "docx", docx("PAGE"), "clean"),
            ("active_pdf", "pdf", pdf(True), "refused"),
            ("includetext", "docx", docx('INCLUDETEXT "https://fixture.invalid/candidate.docx"'), "refused"),
            ("eicar_exact_file", "pdf", EICAR, "detected"),
            ("eicar_zip_member", "docx", infected.getvalue(), "detected"),
            ("missing_definitions", "pdf", pdf(), "missing"),
            ("clean_tex", "tex", clean_tex, "clean"),
            ("clean_texzip", "texzip", texzip.getvalue(), "clean"),
            ("eicar_tex_source", "tex", EICAR, "detected"),
            ("eicar_texzip_member", "texzip", infected_texzip.getvalue(), "detected")]

def docx_layout_corpus(image: str, *, family: str = "Cambria") -> list[tuple[str, bytes, dict]]:
    """One genuine styled two-page fit and one genuine reflow refusal.

    The fixture is synthetic; the expected candidate is the exact source-pinned
    native patch, not a template or a purported Word render produced on the host.
    """
    from docx import Document
    from docx.opc.constants import RELATIONSHIP_TYPE
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt
    spec = importlib.util.spec_from_file_location("document_native_docx_patch", Path("/opt/document/app/services/resume_layout.py"))
    if spec is None or spec.loader is None:
        raise DocumentImageError()
    layout = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = layout
    spec.loader.exec_module(layout)
    original = "Built scalable systems with Python and SQL."
    document = Document()
    if family not in {"Cambria", "Courier Prime"}:
        raise DocumentImageError()
    document.styles["Normal"].font.name = family
    document.styles["Normal"].font.size = Pt(11)
    title = document.add_paragraph("Taylor Example")
    if family == "Courier Prime":
        # The candidate exercises actual theme selection as well as Normal
        # inheritance. No used/default Latin theme face relies on Caladea.
        from lxml import etree
        theme_part = next(rel.target_part for rel in document.part.rels.values()
                          if rel.reltype == RELATIONSHIP_TYPE.THEME)
        theme = etree.fromstring(theme_part.blob)
        drawing = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
        for kind in ("majorFont", "minorFont"):
            theme.find(".//" + drawing + kind + "/" + drawing + "latin").set("typeface", family)
        theme_part._blob = etree.tostring(theme)
        fonts = title.runs[0]._r.get_or_add_rPr().get_or_add_rFonts()
        fonts.set(qn("w:asciiTheme"), "minorAscii")
        fonts.set(qn("w:hAnsiTheme"), "minorHAnsi")
    paragraph = document.add_paragraph()
    paragraph.add_run("Built scalable systems with ")
    paragraph.add_run("Python").bold = True
    paragraph.add_run(" and ")
    paragraph.add_run("SQL").italic = True
    paragraph.add_run(".")
    document.add_paragraph("Reduced API latency by 40% through focused profiling.")
    link_paragraph = document.add_paragraph()
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), document.part.relate_to("https://example.test/portfolio", RELATIONSHIP_TYPE.HYPERLINK, is_external=True))
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "Portfolio"
    run.append(text)
    link.append(run)
    link_paragraph._p.append(link)
    document.add_page_break()
    document.add_paragraph("Original education and second-page running content.")
    stream = io.BytesIO()
    document.save(stream)
    source = stream.getvalue()
    unit = next(unit for unit in layout.extract_source_units(source, "docx") if unit["text"] == original)
    policy = POLICY_SHA256
    rows = []
    for name, replacement in (("styled_multipage_fit", "Built systems with Python and SQL."),
                              ("rendered_reflow_refusal", original + " reusable systems" * 40)):
        edits = [{"unit_id": unit["unit_id"], "original_text": original, "replacement_text": replacement}]
        candidate = layout._apply_docx(source, edits)
        expected = {"version": 1, "operation": "docx_layout", "sha256": hashlib.sha256(source).hexdigest(),
            "size_bytes": len(source), "source_format": "docx", "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
            "candidate_size_bytes": len(candidate), "edits_sha256": hashlib.sha256(json.dumps(edits, sort_keys=True,
                separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()}
        request = {"version": 1, "source_format": "docx", "sha256": expected["sha256"],
            "content_base64": base64.b64encode(source).decode(), "edits": edits,
            "candidate_sha256": expected["candidate_sha256"], "candidate_size_bytes": len(candidate),
            "worker_image": image, "policy_sha256": policy}
        rows.append((name, json.dumps(request).encode(), expected))
    return rows

def candidate_docx_layout_corpus(image: str) -> list[tuple[str, bytes, dict]]:
    """Keep the historical Cambria source as an explicit fallback refusal.

    The candidate's successful layout control requests the actual bundled
    Courier Prime family. The frozen quality corpus is independent/unchanged.
    """
    rows = docx_layout_corpus(image, family="Courier Prime")
    _name, request, binding = docx_layout_corpus(image)[0]
    return rows + [("unsupported_source_font_refusal", request, binding)]

def inspection(service, payload, *, docx_layout=False):
    diagnostic = service._RequestDiagnostic()
    token = service._REQUEST_DIAGNOSTIC.set(diagnostic)
    result = None
    try:
        result = service.inspect_in_sandbox(payload, docx_layout=docx_layout)
    except Exception:
        pass
    finally:
        service._REQUEST_DIAGNOSTIC.reset(token)
    return result, diagnostic


def envelope(kind, content):
    binding = {"version": 1, "sha256": hashlib.sha256(content).hexdigest(),
               "size_bytes": len(content), "source_format": kind}
    payload = {"version": 1, "sha256": binding["sha256"], "source_format": kind,
               "content_base64": base64.b64encode(content).decode()}
    return json.dumps(payload).encode(), binding


def case_row(name, binding, result, diagnostic):
    return {"case": name, "passed": False, "attempted": True, "binding": binding,
            "binding_match": same_binding(result, binding), "diagnostic": diagnostic_projection(diagnostic),
            "cleanup_passed": cleanup_success(diagnostic)}


def main():
    started = time.monotonic()
    inventory_only = sys.argv[1:] == ["--inventory"]
    event = "hirewiz_document_candidate_inventory_v1" if inventory_only else "hirewiz_document_candidate_required_v3"
    report = {"event": event, "version": 1 if inventory_only else 3,
              "activation_flags": ACTIVATION_FLAGS.copy(), "identity_preflight": {"passed": False},
              "cases": [], "all_required_passed": False, "failure_stage": "configuration"}
    exit_code = 1
    try:
        require(sys.argv[1:] in ([], ["--inventory"]))
        execution = os.environ.get("CLOUD_RUN_EXECUTION", "")
        require(type(execution) is str and len(execution) <= 63
                and re.fullmatch(r"hirewiz-doc-six-[a-f0-9]{12}-[a-z0-9]{1,20}", execution) is not None)
        report["execution_name"] = execution
        require(re.fullmatch(r"us-central1-docker\.pkg\.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-document-inspector@sha256:[a-f0-9]{64}", IMMUTABLE_IMAGE) is not None)
        require(re.fullmatch(r"[a-f0-9]{64}", POLICY_SHA256) is not None)
        sys.path.insert(0, "/opt/document")
        import service
        require(service.configuration() == (IMMUTABLE_IMAGE, POLICY_SHA256))
        require(os.getuid() == 65534 and os.getgid() == 65534)
        report.update(worker_image=IMMUTABLE_IMAGE, policy_sha256=POLICY_SHA256, outer_runtime_user_verified=True)
        report["failure_stage"] = "identity_preflight"
        diagnostic = service._RequestDiagnostic()
        try:
            identity = auxiliary(service, PREFLIGHT_PROGRAM, diagnostic)
        finally:
            report["identity_preflight"]["diagnostic"] = diagnostic_projection(diagnostic)
            report["identity_preflight"]["cleanup_passed"] = cleanup_success(diagnostic)
        require(set(identity) == {"runtime_hashes", "architecture", "runtime_uid", "runtime_gid", "readonly_font_manifest",
                                  "font_manifest_process_writable", "renderer_version", "font_manifest_sha256", "courier_bundle_verified"})
        require(identity["runtime_hashes"] == EXPECTED_RUNTIME_HASHES)
        require(identity["architecture"] == "x86_64" and identity["readonly_font_manifest"] is True and cleanup_success(diagnostic))
        require(all(type(identity[key]) is int and 0 <= identity[key] <= 4294967295 for key in ("runtime_uid", "runtime_gid")))
        require(type(identity["font_manifest_process_writable"]) is bool)
        require(type(identity["renderer_version"]) is str and len(identity["renderer_version"]) <= 200
                and re.fullmatch(r"LibreOffice [0-9]+\.[0-9]+\.[0-9]+(?:\.[0-9]+)*(?: [ -~]+)?", identity["renderer_version"]) is not None)
        require(type(identity["font_manifest_sha256"]) is str and re.fullmatch(r"[a-f0-9]{64}", identity["font_manifest_sha256"]) is not None)
        require(identity["courier_bundle_verified"] is True and identity["font_manifest_process_writable"] is False)
        report["identity_preflight"].update(identity, passed=True)
        if inventory_only:
            report.update(failure_stage=None, elapsed_ms=min(600000, max(0, int((time.monotonic()-started)*1000))))
            raw = json.dumps(report, separators=(",", ":"), allow_nan=False).encode()
            require(len(raw)+1 <= MAX_REPORT)
            sys.stdout.buffer.write(raw+b"\n")
            return
        report["failure_stage"] = "corpus"
        fixed = {name: (kind, content, outcome) for name, kind, content, outcome in corpus()}
        require(len(fixed) == 12 and len(EICAR) == 68)
        layout_cases = candidate_docx_layout_corpus(IMMUTABLE_IMAGE)
        require(tuple(name for name, _, _ in layout_cases) == CASE_NAMES[-3:])
        for name in CASE_NAMES[:7]:
            report["failure_stage"] = name
            kind, content, outcome = fixed[name]
            require(outcome == ("detected" if name in {"eicar_exact_file", "eicar_tex_source", "eicar_texzip_member"} else "clean"))
            payload, binding = envelope(kind, content)
            result, diagnostic = inspection(service, payload)
            row = case_row(name, binding, result, diagnostic)
            report["cases"].append(row)
            if result is not None and row["binding_match"] and row["cleanup_passed"]:
                require(result.get("worker_image") == IMMUTABLE_IMAGE and result.get("policy_sha256") == POLICY_SHA256)
                if name in {"eicar_exact_file", "eicar_tex_source", "eicar_texzip_member"}:
                    row["worker_refused"] = result.get("error") == "document_inspection_refused"
                    row["passed"] = (row["worker_refused"] and diagnostic.processor == "refused"
                                     and diagnostic.launcher_code == 1 and diagnostic.validation == "complete")
                elif "error" not in result:
                    row["scan"] = scan_projection(result.get("scan"))
                    if name in {"clean_tex", "clean_texzip"}:
                        parsed = result.get("parsed", {})
                        row["native_source_empty"] = (parsed == {
                            "raw_text": "", "sections": {}, "skills": [], "experience_years": 0.0,
                            "contact_info": {key: None for key in ("name", "email", "phone", "linkedin", "github")}}
                            and type(parsed.get("experience_years")) in {int, float})
                    else:
                        row["python_skill_present"] = (name != "clean_docx" or "Python" in result.get("parsed", {}).get("skills", []))
                    row["passed"] = (diagnostic.processor == "clean" and diagnostic.launcher_code == 0
                                     and diagnostic.validation == "complete"
                                     and row.get("native_source_empty", row.get("python_skill_present")) is True)
            require(row["cleanup_passed"])
        report["failure_stage"] = "eicar_direct_detection"
        diagnostic = service._RequestDiagnostic()
        row = {"case": "eicar_direct_detection", "passed": False, "attempted": True,
               "binding": {"sha256": hashlib.sha256(EICAR).hexdigest(), "size_bytes": len(EICAR)}}
        report["cases"].append(row)
        direct = None
        try:
            direct = auxiliary(service, EICAR_DIRECT_PROGRAM, diagnostic)
        except Exception:
            pass
        finally:
            row.update(diagnostic=diagnostic_projection(diagnostic), cleanup_passed=cleanup_success(diagnostic))
        if direct is not None:
            require(set(direct) == {"sha256", "size_bytes", "exact_file_unchanged", "coherent_eicar_detection"})
            row["binding_match"] = same_binding(direct, row["binding"])
            row["exact_file_unchanged"] = direct["exact_file_unchanged"] is True
            row["coherent_eicar_detection"] = direct["coherent_eicar_detection"] is True
            row["passed"] = (row["binding_match"] and row["exact_file_unchanged"] and row["coherent_eicar_detection"]
                              and row["cleanup_passed"] and diagnostic.launcher_code == 0)
        require(row["cleanup_passed"])
        for name, payload, binding in layout_cases:
            report["failure_stage"] = name
            result, diagnostic = inspection(service, payload, docx_layout=True)
            row = case_row(name, binding, result, diagnostic)
            report["cases"].append(row)
            if result is not None and row["binding_match"] and row["cleanup_passed"]:
                require(result.get("worker_image") == IMMUTABLE_IMAGE and result.get("policy_sha256") == POLICY_SHA256)
                if name == "styled_multipage_fit" and "error" not in result:
                    receipt = result.get("layout", {})
                    renderer = receipt.get("renderer", {})
                    row["layout"] = {key: receipt.get(key) for key in ("validator", "pages", "unchanged_links",
                        "outside_changed_regions_pixel_delta", "before_pdf_sha256", "after_pdf_sha256",
                        "rendered_fonts_sha256", "changed_paragraphs", "protected_glyphs", "unchanged_graphics",
                        "max_protected_geometry_delta_points")}
                    row["renderer_binding_match"] = (renderer.get("image_digest") == IMMUTABLE_IMAGE
                        and renderer.get("policy_sha256") == POLICY_SHA256
                        and renderer.get("renderer_version") == identity["renderer_version"]
                        and renderer.get("font_manifest_sha256") == identity["font_manifest_sha256"])
                    row["scan"] = scan_projection(result.get("scan"))
                    row["candidate_scan_equal"] = result.get("candidate_scan") == result.get("scan")
                    # Original public case acceptance remains unchanged; pin join and cleanup are additional.
                    row["passed"] = (diagnostic.launcher_code == 0 and diagnostic.processor == "clean"
                        and diagnostic.validation == "complete" and receipt.get("validator") == "hirewiz-docx-layout-v1"
                        and receipt.get("pages") == 2 and receipt.get("unchanged_links") == 1
                        and receipt.get("outside_changed_regions_pixel_delta") == 0
                        and result.get("scan", {}).get("engine") == "ClamAV"
                        and row["candidate_scan_equal"] and row["renderer_binding_match"])
                elif name in {"rendered_reflow_refusal", "unsupported_source_font_refusal"}:
                    reason = result.get("reason")
                    allowed = {"unsupported_source_font"} if name == "unsupported_source_font_refusal" else REFLOW_REASONS
                    row["reason"] = reason if type(reason) is str and reason in allowed else None
                    requested = json.loads(payload)
                    unit = result.get("unit_id")
                    row["matching_unit_or_none"] = unit is None or any(unit == edit["unit_id"] for edit in requested["edits"])
                    row["passed"] = (diagnostic.launcher_code == 1 and diagnostic.processor == "refused"
                        and diagnostic.validation == "complete" and result.get("error") == "docx_layout_refused"
                        and row["reason"] is not None and row["matching_unit_or_none"])
            require(row["cleanup_passed"])
        require(tuple(row["case"] for row in report["cases"]) == CASE_NAMES)
        report["all_required_passed"] = all(row["passed"] is True for row in report["cases"])
        if report["all_required_passed"]:
            report["failure_stage"] = None
            exit_code = 0
    except BaseException:
        # No payload, native output, exception message, traceback or arbitrary path is reported.
        pass
    observed = {row["case"] for row in report["cases"]}
    report["cases"].extend({"case": name, "passed": False, "attempted": False} for name in CASE_NAMES if name not in observed)
    report["elapsed_ms"] = min(600000, max(0, int((time.monotonic() - started) * 1000)))
    raw = json.dumps(report, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) + 1 > MAX_REPORT:
        raw = json.dumps({"event": event, "version": 1 if inventory_only else 3,
                          "all_required_passed": False, "failure_stage": "report_size",
                          "activation_flags": ACTIVATION_FLAGS}, separators=(",", ":")).encode()
        exit_code = 1
    sys.stdout.buffer.write(raw + b"\n")
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
