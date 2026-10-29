"""Finite source-revision probe built from the unchanged V3 safety corpus.

Only source identity literals and the final report sink are adapted. The original
sandbox/scanner/renderer checks execute unchanged before five new parser cases.
No candidate resume, provider or arbitrary execution input is accepted.
"""
from __future__ import annotations

import ast
import re

from infra.gcp import document_source_candidate as profile

EXTRA_CASES = ("pdf_independent_columns", "pdf_late_date_rows", "pdf_spanning_columns_refused",
               "pdf_earlier_unaligned_sections_refused", "pdf_font_relative_spacing")
INVENTORY_EVENT = "hirewiz_document_source_inventory_v1"
CORPUS_EVENT = "hirewiz_document_source_required_v4"

EXTRA_PROGRAM = r'''
def reading_pdf(mode):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, NumberObject, DecodedStreamObject
    writer=PdfWriter()
    page=writer.add_blank_page(width=612,height=792)
    fonts=DictionaryObject()
    for key,face in (("F1","Helvetica"),("F2","Helvetica-Bold"),("F3","Helvetica-Oblique"),("F4","Courier")):
        fonts[NameObject("/"+key)]=writer._add_object(DictionaryObject({NameObject("/Type"):NameObject("/Font"),
            NameObject("/Subtype"):NameObject("/Type1"),NameObject("/BaseFont"):NameObject("/"+face)}))
    page[NameObject("/Resources")]=DictionaryObject({NameObject("/Font"):fonts})
    rows=[(48,752,"Casey Sample","F2",20),(48,732,"casey@example.test | https://example.test/casey","F1",10)]
    if mode=="pdf_font_relative_spacing":
        rows.append((48,710,"EXPERIENCE","F2",14))
        # Standard Courier advances exactly0.6em. Draw glyphs separately so
        # literal space characters cannot substitute for measured gaps.
        for text,size,y in (("Delivered 14 API checks.",6,680),("API checks",40,620)):
            x=48.0
            for character in text:
                if character==" ":
                    x+=size*.24
                else:
                    rows.append((x,y,character,"F4",size))
                    x+=size*.64
    elif mode=="pdf_late_date_rows":
        rows += [(48,710,"EXPERIENCE","F2",14),(48,686,"Harbor Labs","F2",11),
                 (48,666,"Delivered 4 Python APIs.","F1",11),(48,626,"EDUCATION","F2",14),
                 (48,606,"North College 2015 - 2019","F1",11),(480,686,"2020 - 2022","F3",10)]
    elif mode=="pdf_earlier_unaligned_sections_refused":
        rows += [(48,710,"EDUCATION","F2",14),(330,700,"EXPERIENCE","F2",14),
                 (48,680,"North College 2015 - 2019","F1",11),(330,680,"Harbor Labs 2020 - 2022","F1",11),
                 (48,660,"Degree in Computing","F1",11),(330,660,"Built 3 Python APIs","F1",11),
                 (48,620,"PROJECTS","F2",14),(330,620,"SKILLS","F2",14),
                 (48,600,"Built 2 billing checks","F1",11),(330,600,"Python, SQL, Docker","F1",11)]
    else:
        # Paint by rows, so authored flow cannot substitute for column logic.
        rows += [(48,690,"SUMMARY","F2",14),(330,690,"PROJECTS","F2",14),
                 (48,670,"Python engineer with documented work.","F1",10),(330,670,"Built 3 billing API checks.","F1",11),
                 (48,630,"EXPERIENCE","F2",14),(330,630,"EDUCATION","F2",14),
                 (48,610,"Harbor Labs","F2",11),(185,610,"2020 - 2022","F3",10),
                 (330,610,"North College 2015 - 2019","F1",11),(48,590,"Reduced queue delays by 17%.","F1",11),
                 (330,570,"SKILLS","F2",14),(330,550,"Python, SQL, Docker","F1",11)]
        if mode=="pdf_spanning_columns_refused":
            rows.append((48,520,"A spanning statement crosses both independent sections and needs manual review.","F1",11))
    def escaped(text):
        return text.replace("\\","\\\\").replace("(","\\(").replace(")","\\)")
    commands=[("BT /%s %s Tf 1 0 0 1 %s %s Tm (%s) Tj ET" % (font,size,x,y,escaped(text))).encode("ascii")
              for x,y,text,font,size in rows]
    stream=DecodedStreamObject();stream.set_data(b"\n".join(commands))
    page[NameObject("/Contents")]=writer._add_object(stream)
    output=io.BytesIO();writer.write(output)
    return output.getvalue()


def source_main():
    report,exit_code,started=_base_main()
    inventory_only=sys.argv[1:]==["--inventory"]
    report["event"]=SOURCE_INVENTORY_EVENT if inventory_only else SOURCE_CORPUS_EVENT
    report["version"]=1 if inventory_only else 4
    if not inventory_only:
        if exit_code==0:
            try:
                import service
                for name in SOURCE_EXTRA_CASES:
                    report["failure_stage"]=name
                    require(time.monotonic()-started<510)
                    content=reading_pdf(name)
                    payload,binding=envelope("pdf",content)
                    result,diagnostic=inspection(service,payload)
                    row=case_row(name,binding,result,diagnostic)
                    report["cases"].append(row)
                    if result is not None and row["binding_match"] and row["cleanup_passed"]:
                        require(result.get("worker_image")==IMMUTABLE_IMAGE and result.get("policy_sha256")==POLICY_SHA256)
                        if name.endswith("_refused"):
                            row["worker_refused"]=(result.get("error")=="document_inspection_refused")
                            row["passed"]=(row["worker_refused"] and diagnostic.processor=="refused"
                                and diagnostic.launcher_code==1 and diagnostic.validation=="complete")
                        elif "error" not in result:
                            parsed=result.get("parsed",{});sections=parsed.get("sections",{});text=parsed.get("raw_text","")
                            row["scan"]=scan_projection(result.get("scan"))
                            row["reading_order_pass"]=(parsed.get("experience_years")==2
                                and "Harbor Labs" in sections.get("experience","")
                                and "2020 - 2022" in sections.get("experience","")
                                and "North College" in sections.get("education","")
                                and "North College" not in sections.get("experience","")
                                and parsed.get("contact_info",{}).get("email")=="casey@example.test")
                            if name=="pdf_independent_columns":
                                row["reading_order_pass"]=(row["reading_order_pass"]
                                    and "billing API" in sections.get("projects","")
                                    and "billing API" not in sections.get("experience","")
                                    and text.index("EXPERIENCE")<text.index("PROJECTS"))
                            elif name=="pdf_font_relative_spacing":
                                row["reading_order_pass"]=(sections.get("experience","").splitlines()
                                    ==["Delivered 14 API checks.","API checks"]
                                    and parsed.get("contact_info",{}).get("email")=="casey@example.test")
                            row["passed"]=(row["reading_order_pass"] and diagnostic.processor=="clean"
                                and diagnostic.launcher_code==0 and diagnostic.validation=="complete")
                    require(row["cleanup_passed"] and row["passed"])
                report["failure_stage"]=None
            except BaseException:
                exit_code=1
        observed={row["case"] for row in report["cases"]}
        report["cases"].extend({"case":name,"passed":False,"attempted":False}
            for name in SOURCE_EXTRA_CASES if name not in observed)
        report["all_required_passed"]=(exit_code==0 and len(report["cases"])==16
            and all(row["passed"] is True for row in report["cases"]))
        exit_code=0 if report["all_required_passed"] else 1
    report["elapsed_ms"]=min(600000,max(0,int((time.monotonic()-started)*1000)))
    raw=json.dumps(report,separators=(",",":"),allow_nan=False).encode()
    if len(raw)+1>MAX_REPORT:
        raw=json.dumps({"event":report["event"],"version":report["version"],"all_required_passed":False,
            "failure_stage":"report_size","activation_flags":ACTIVATION_FLAGS},separators=(",",":")).encode()
        exit_code=1
    sys.stdout.buffer.write(raw+b"\n")
    raise SystemExit(exit_code)

if __name__=="__main__":
    source_main()
'''


def rendered(image, source, *, root=profile.ROOT, retained_template=None):
    profile.need(type(image) is str and re.fullmatch(
        r"us-central1-docker\.pkg\.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-document-inspector@sha256:[a-f0-9]{64}", image),
        "payload_image_invalid")
    extra_program = EXTRA_PROGRAM
    if retained_template is not None:
        retained = ast.parse(retained_template)
        literals = {node.targets[0].id: ast.literal_eval(node.value) for node in retained.body
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"EXTRA_CASES", "EXTRA_PROGRAM", "INVENTORY_EVENT", "CORPUS_EVENT"}}
        profile.need(literals.get("EXTRA_CASES") == EXTRA_CASES and literals.get("INVENTORY_EVENT") == INVENTORY_EVENT
                     and literals.get("CORPUS_EVENT") == CORPUS_EVENT and type(literals.get("EXTRA_PROGRAM")) is str,
                     "retained_payload_version_changed")
        extra_program = literals["EXTRA_PROGRAM"]
    raw = (root / "infra/gcp/document_candidate_payload.py").read_bytes()
    profile.need(profile.digest(raw) == profile.BASE_PAYLOAD_SHA256, "base_payload_changed")
    tree = ast.parse(raw)
    assignments = {node.targets[0].id: node for node in tree.body if isinstance(node, ast.Assign)
                   and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)}
    for name, value in (("IMMUTABLE_IMAGE", image), ("POLICY_SHA256", source.policy),
                        ("EXPECTED_RUNTIME_HASHES", source.runtime)):
        profile.need(name in assignments, "base_payload_shape_changed")
        assignments[name].value = ast.parse(repr(value), mode="eval").body
    program = ast.parse(ast.literal_eval(assignments["PREFLIGHT_PROGRAM"].value))
    expected = [node for node in program.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "expected" for target in node.targets)]
    profile.need(len(expected) == 1, "base_preflight_changed")
    expected[0].value = ast.parse(repr(source.runtime), mode="eval").body
    assignments["PREFLIGHT_PROGRAM"].value = ast.Constant(ast.unparse(program))
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    profile.need(isinstance(main.body[-1], ast.Raise) and isinstance(main.body[-2], ast.Expr)
                 and ast.unparse(main.body[-1]) == "raise SystemExit(exit_code)"
                 and ast.unparse(main.body[-2]) == "sys.stdout.buffer.write(raw + b'\\n')", "base_report_changed")
    main.name = "_base_main"
    inventory = [node for node in ast.walk(main) if isinstance(node, ast.If)
                 and ast.unparse(node.test) == "inventory_only"]
    profile.need(len(inventory) == 1 and isinstance(inventory[0].body[-1], ast.Return)
                 and inventory[0].body[-1].value is None
                 and ast.unparse(inventory[0].body[-2]) == "sys.stdout.buffer.write(raw + b'\\n')",
                 "base_inventory_report_changed")
    inventory[0].body[-2:] = [ast.Return(ast.Tuple(elts=[ast.Name(id="report", ctx=ast.Load()),
        ast.Constant(0), ast.Name(id="started", ctx=ast.Load())], ctx=ast.Load()))]
    main.body[-2:] = [ast.Return(ast.Tuple(elts=[ast.Name(id=name, ctx=ast.Load())
        for name in ("report", "exit_code", "started")], ctx=ast.Load()))]
    profile.need(isinstance(tree.body[-1], ast.If)
                 and ast.unparse(tree.body[-1]) == "if __name__ == '__main__':\n    main()", "base_entry_changed")
    tree.body.pop()
    ast.fix_missing_locations(tree)
    return ast.unparse(tree) + "\nSOURCE_EXTRA_CASES=" + repr(EXTRA_CASES) + "\nSOURCE_INVENTORY_EVENT=" + repr(INVENTORY_EVENT) + "\nSOURCE_CORPUS_EVENT=" + repr(CORPUS_EVENT) + "\n" + extra_program
