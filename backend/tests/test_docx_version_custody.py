"""Real function AST with synthetic custody dependencies; no SQL or renderer proof."""
from __future__ import annotations
import __future__

import ast
import builtins
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


class HTTPFailure(Exception):
    def __init__(self, status_code, detail):
        self.status_code, self.detail = status_code, detail


class LayoutFailure(Exception):
    pass


class FactFailure(Exception):
    pass


class Column:
    def __init__(self, dataset, name):
        self.dataset, self.name = dataset, name

    def __eq__(self, value):
        return lambda row: getattr(row, self.name) == value

    def in_(self, values):
        return lambda row: getattr(row, self.name) in values


class Version:
    resume_id = Column("versions", "resume_id")
    version_number = Column("versions", "version_number")

    def __init__(self, **values):
        self.__dict__.update(values)


class Query:
    def __init__(self, rows, kind="rows", column=None):
        self.rows, self.kind, self.column = list(rows), kind, column

    def filter(self, *predicates):
        self.rows = [row for row in self.rows if all(predicate(row) for predicate in predicates)]
        return self

    def scalar(self):
        if self.kind == "count":
            return len(self.rows)
        assert self.kind == "max"
        return max((getattr(row, self.column.name) for row in self.rows), default=None)

    def all(self):
        return self.rows


class Database:
    def __init__(self, harness):
        self.harness = harness
        self.added, self.commits = [], 0

    def query(self, entity):
        if entity is self.harness.models.EvidenceItem:
            return Query(self.harness.evidence)
        rows = self.harness.evidence if entity.column.dataset == "evidence" else self.harness.versions
        return Query(rows, entity.kind, entity.column)

    def add(self, value):
        self.harness.events.append("add")
        self.added.append(value)

    def commit(self):
        self.harness.events.append("commit")
        self.commits += 1

    def refresh(self, value):
        assert value in self.added
        self.harness.events.append("refresh")


class Harness:
    def __init__(self):
        self.events, self.prepared, self.validated = [], [], []
        self.native_failure = None
        self.resume = SimpleNamespace(id=7, source_available=True, source_format="docx",
                                      source_document=b"synthetic immutable uploaded source")
        self.evidence = [SimpleNamespace(id="ev-approved", user_id=22, resume_id=7,
                                        approval_state="approved", evidence_text="Verified Python skill",
                                        skills=["Python"])]
        self.versions = [Version(resume_id=7, version_number=2), Version(resume_id=99, version_number=20)]
        evidence_type = type("EvidenceItem", (), {
            name: Column("evidence", name) for name in ("id", "user_id", "resume_id", "approval_state")
        })
        self.models = SimpleNamespace(EvidenceItem=evidence_type, ResumeVersion=Version)
        self.db = Database(self)
        self.seal = {"synthetic_server_seal": "sealed-source", "source_sha256": "a" * 64}
        self.imports = {
            "services.llm_client": SimpleNamespace(TailoringOutputError=FactFailure,
                                                   _validate_tailoring_edit=self.validate),
            "services.resume_layout": SimpleNamespace(ResumeLayoutError=LayoutFailure,
                                                       extract_source_units=self.units),
            "services.docx_artifact": SimpleNamespace(prepare_artifact=self.prepare_docx),
            "services.native_tex": SimpleNamespace(prepare_artifact=self.prepare_tex),
        }
        tree = ast.parse((ROOT / "backend/app/domains/career/service.py").read_bytes())
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name == "create_resume_version"]
        assert len(nodes) == 1
        namespace = {
            "__name__": "private_docx_custody", "__package__": "private_docx_custody.domains.career",
            "__builtins__": {**vars(builtins), "__import__": self.synthetic_import},
            "HTTPException": HTTPFailure, "models": self.models,
            "func": SimpleNamespace(count=lambda column: SimpleNamespace(kind="count", column=column),
                                    max=lambda column: SimpleNamespace(kind="max", column=column)),
            "_owned_resume": self.owned_resume,
            "public_id": lambda prefix: prefix + "-synthetic", "utcnow": lambda: "synthetic-time",
        }
        module = ast.Module(body=nodes, type_ignores=[])
        exec(compile(module, "<actual-create-resume-version-AST>", "exec",
                     flags=__future__.annotations.compiler_flag, dont_inherit=True), namespace)
        self.create = namespace["create_resume_version"]

    def synthetic_import(self, name, globals=None, locals=None, fromlist=(), level=0):
        assert level == 3 and name in self.imports, "Only private synthetic service imports are allowed"
        return self.imports[name]

    def owned_resume(self, db, user_id, resume_id):
        assert db is self.db and user_id == 22 and resume_id == self.resume.id
        return self.resume

    def units(self, source, kind):
        assert source == self.resume.source_document and kind == self.resume.source_format
        return [{"unit_id": "paragraph-1", "text": "Original paragraph"}]

    def validate(self, edit, units, allowed, seen):
        self.events.append("validate")
        self.validated.append((copy.deepcopy(edit), copy.deepcopy(allowed)))
        if edit["unit_id"] not in units or any(identity not in allowed for identity in edit["evidence_ids"]):
            raise FactFailure("synthetic unsupported evidence")
        assert edit["unit_id"] not in seen
        return {**edit, "replacement_text": edit["replacement_text"].strip()}

    def prepare_docx(self, source, edits):
        self.events.append("prepare_docx")
        self.prepared.append(("docx", source, copy.deepcopy(edits)))
        if self.native_failure is not None:
            raise self.native_failure
        return copy.deepcopy(self.seal)

    def prepare_tex(self, source, kind, edits, evidence):
        self.events.append("prepare_tex")
        self.prepared.append((kind, source, copy.deepcopy(edits), copy.deepcopy(evidence)))
        return copy.deepcopy(self.seal)

    def payload(self, content=None, evidence_ids=None):
        return SimpleNamespace(resume_id=7, opportunity_id=None, generation_run_id=None,
                               evidence_ids=[] if evidence_ids is None else evidence_ids,
                               structured_content={} if content is None else content, label="synthetic draft")

    def edited_content(self):
        return {"format_preservation": "source", "source_format": self.resume.source_format,
                "source_edits": [{"unit_id": "paragraph-1", "replacement_text": "  Verified Python skill  ",
                                  "evidence_ids": ["ev-approved"]}]}


@pytest.fixture
def custody():
    return Harness()


def test_edited_docx_seals_validated_edits_once_before_persistence(custody):
    content = custody.edited_content()
    content["sealed_native_artifact"] = {"synthetic_client_seal": "untrusted"}
    payload = custody.payload(content, ["ev-approved"])
    before = copy.deepcopy(payload.structured_content)
    version = custody.create(custody.db, 22, payload)
    validated = [{**before["source_edits"][0], "replacement_text": "Verified Python skill"}]
    assert custody.prepared == [("docx", custody.resume.source_document, validated)]
    assert custody.validated[0][1] == {"ev-approved": {"text": "Verified Python skill", "skills": ["Python"]}}
    assert version.structured_content["source_edits"] == validated
    assert version.structured_content["sealed_native_artifact"] == custody.seal
    assert payload.structured_content == before
    assert custody.db.added == [version] and custody.db.commits == 1
    assert version.version_number == 3 and version.approval_state == "draft"
    assert custody.events == ["validate", "prepare_docx", "add", "commit", "refresh"]


def test_unchanged_docx_preserves_snapshot_and_replaces_client_seal(custody):
    custody.seal = None  # The real unchanged-source preparation needs no render seal.
    content = {"format_preservation": "source", "source_format": "docx", "source_edits": [],
               "snapshot": {"note": "unchanged"}, "sealed_native_artifact": {"synthetic_client_seal": "untrusted"}}
    payload = custody.payload(content)
    before, source = copy.deepcopy(content), custody.resume.source_document
    version = custody.create(custody.db, 22, payload)
    assert custody.prepared == [("docx", source, [])] and custody.validated == []
    assert version.structured_content == {key: value for key, value in before.items()
                                         if key != "sealed_native_artifact"}
    assert payload.structured_content == before and custody.resume.source_document == source
    assert custody.events == ["prepare_docx", "add", "commit", "refresh"]


def test_docx_source_kind_mismatch_refuses_before_prepare_or_persistence(custody):
    content = custody.edited_content()
    content["source_format"] = "tex"
    with pytest.raises(HTTPFailure) as caught:
        custody.create(custody.db, 22, custody.payload(content, ["ev-approved"]))
    assert caught.value.status_code == 422
    assert custody.prepared == custody.db.added == [] and custody.db.commits == 0


def test_unsupported_fact_refuses_before_native_prepare_or_persistence(custody):
    content = custody.edited_content()
    content["source_edits"][0]["evidence_ids"] = ["ev-not-approved"]
    with pytest.raises(HTTPFailure) as caught:
        custody.create(custody.db, 22, custody.payload(content, ["ev-approved"]))
    assert caught.value.status_code == 422 and caught.value.detail == "synthetic unsupported evidence"
    assert custody.events == ["validate"]
    assert custody.prepared == custody.db.added == [] and custody.db.commits == 0


def test_native_docx_refusal_cannot_commit_an_unsealed_version(custody):
    custody.native_failure = LayoutFailure("synthetic native artifact refusal")
    content = {"format_preservation": "source", "source_format": "docx", "source_edits": []}
    with pytest.raises(HTTPFailure) as caught:
        custody.create(custody.db, 22, custody.payload(content))
    assert caught.value.status_code == 422 and caught.value.detail == "synthetic native artifact refusal"
    assert len(custody.prepared) == 1 and custody.events == ["prepare_docx"]
    assert custody.db.added == [] and custody.db.commits == 0


def test_tex_keeps_source_kind_edits_and_approved_facts_prepare_signature(custody):
    custody.resume.source_format = "tex"
    payload = custody.payload(custody.edited_content(), ["ev-approved"])
    version = custody.create(custody.db, 22, payload)
    edits = [{**payload.structured_content["source_edits"][0], "replacement_text": "Verified Python skill"}]
    facts = [{"id": "ev-approved", "text": "Verified Python skill", "skills": ["Python"]}]
    assert custody.prepared == [("tex", custody.resume.source_document, edits, facts)]
    assert version.structured_content["sealed_native_artifact"] == custody.seal
    assert custody.events == ["validate", "prepare_tex", "add", "commit", "refresh"]
