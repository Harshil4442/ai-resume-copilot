"""Native image-corpus shape controls; no Docker or genuine AV claim."""

from __future__ import annotations

import ast
import hashlib
import io
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from scripts import document_test_image as image

ROOT = Path(__file__).resolve().parents[2]


def test_native_corpus_has_real_supported_sources_and_exact_antivirus_test_members():
    from backend.app.services.native_project import read_project

    values = {name: (kind, content, outcome) for name, kind, content, outcome in image.corpus()}
    assert len(values) == 12
    for name, kind in (("clean_tex", "tex"), ("clean_texzip", "texzip")):
        actual_kind, content, outcome = values[name]
        assert actual_kind == kind and outcome == "clean"
        project = read_project(content, kind)
        assert project.main == "resume.tex"
        if kind == "texzip":
            assert project.files["sections/skills.tex"] == b"Python, SQL"
            assert project.files["asset.pdf"].startswith(b"%PDF-")
    assert values["eicar_tex_source"] == ("tex", image.EICAR, "detected")
    kind, content, outcome = values["eicar_texzip_member"]
    assert kind == "texzip" and outcome == "detected"
    with ZipFile(io.BytesIO(content)) as archive:
        assert archive.read("standalone-antivirus-test.tex") == image.EICAR
    assert len(image.EICAR) == 68


def test_gcp_native_corpus_uses_the_same_source_generator_as_the_local_driver():
    def corpus_node(path):
        return next(node for node in ast.parse(path.read_bytes()).body
                    if isinstance(node, ast.FunctionDef) and node.name == "corpus")

    actual = corpus_node(ROOT / "infra/gcp/document_refresh_payload.txt")
    expected = corpus_node(ROOT / "backend/scripts/document_test_image.py")
    assert ast.dump(actual, include_attributes=False) == ast.dump(expected, include_attributes=False)


@pytest.mark.parametrize("change", ["missing", "facts", "boolean"])
def test_image_checker_requires_empty_native_admission_facts(monkeypatch, change):
    content = b"synthetic native transport bytes"
    hashes = dict.fromkeys(image.SOURCES, "b" * 64)
    monkeypatch.setattr(image, "docker_command", lambda: ["synthetic-only"])
    monkeypatch.setattr(image, "source_hashes", lambda: hashes)
    monkeypatch.setattr(image, "image_metadata", lambda *a: None)
    monkeypatch.setattr(image, "corpus", lambda: [("clean_tex", "tex", content, "clean")])
    parsed = {"raw_text": "", "sections": {}, "skills": [], "experience_years": 0.0,
              "contact_info": {key: None for key in ("name", "email", "phone", "linkedin", "github")}}
    if change == "facts":
        parsed["skills"] = ["Invented skill"]
    elif change == "boolean":
        parsed["experience_years"] = False

    calls = []

    def container(*args):
        calls.append(args)
        if len(calls) == 1:
            return 0, json.dumps({path: hashes[path] for path in image.RUNTIME}).encode()
        result = {"sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content),
                  "source_format": "tex", "scan": {"engine": "ClamAV"}}
        if change != "missing":
            result["parsed"] = parsed
        return 0, json.dumps(result).encode()

    monkeypatch.setattr(image, "container", container)
    with pytest.raises(image.DocumentImageError):
        image.check("linux/amd64", image="sha256:" + "a" * 64)
    assert len(calls) == 2
