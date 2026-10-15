"""Actual SQL/crypto review flow, isolated from external providers and app bootstrap."""
from __future__ import annotations

import ast
import base64
import hashlib
import importlib.util
import os
import sys
import types
import unittest
from contextlib import nullcontext
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)
from fastapi import HTTPException
from sqlalchemy import JSON, Column, Integer, String, Table, create_engine, or_
from sqlalchemy.orm import declarative_base, sessionmaker

ROOT = Path(__file__).resolve().parents[1] / "app"
PREFIX = "hirewiz_browser_assistance_isolated"


def load(name, path):
    specification = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


for suffix in ("", ".domains", ".domains.employer", ".domains.browser_assistance"):
    module = types.ModuleType(PREFIX + suffix)
    module.__path__ = []
    sys.modules[module.__name__] = module
Base = declarative_base()
database = types.ModuleType(PREFIX + ".database")
database.Base = Base
sys.modules[database.__name__] = database
core = types.ModuleType(PREFIX + ".models")
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
core.User = User
class ResumeVersion(Base):
    __tablename__ = "resume_versions"
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, nullable=False)
    resume_id = Column(Integer, nullable=False)
    approval_state = Column(String(16), nullable=False)
    structured_content = Column(JSON, nullable=False)
    evidence_ids = Column(JSON, nullable=False)
class EvidenceItem(Base):
    __tablename__ = "evidence_items"
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, nullable=False)
    approval_state = Column(String(16), nullable=False)
core.ResumeVersion, core.EvidenceItem = ResumeVersion, EvidenceItem
sys.modules[core.__name__] = core
common = load(PREFIX + ".domains.common", ROOT / "domains/common.py")
employer = load(PREFIX + ".domains.employer.models", ROOT / "domains/employer/models.py")
load(PREFIX + ".domains.employer.search_preferences", ROOT / "domains/employer/search_preferences.py")
employer_schemas = load(PREFIX + ".domains.employer.schemas", ROOT / "domains/employer/schemas.py")
employer_service = types.ModuleType(PREFIX + ".domains.employer.service")
tree = ast.parse((ROOT / "domains/employer/service.py").read_text())
function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_package_digest")
employer_service.payload_fingerprint = common.payload_fingerprint
exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), "actual_employer_package_digest", "exec"), employer_service.__dict__)
sys.modules[employer_service.__name__] = employer_service
artifact_reader = types.ModuleType(PREFIX + ".domains.employer.artifacts")
artifact_reader.hashlib, artifact_reader.HTTPException = hashlib, HTTPException
artifact_tree = ast.parse((ROOT / "domains/employer/artifacts.py").read_text())
exec(compile(ast.fix_missing_locations(ast.Module(body=[node for node in artifact_tree.body
    if isinstance(node, ast.FunctionDef) and node.name == "read"], type_ignores=[])), "actual_sealed_artifact_read", "exec"), artifact_reader.__dict__)
sys.modules[artifact_reader.__name__] = artifact_reader
models = load(PREFIX + ".domains.browser_assistance.models", ROOT / "domains/browser_assistance/models.py")
schemas = load(PREFIX + ".domains.browser_assistance.schemas", ROOT / "domains/browser_assistance/schemas.py")
attachments = load(PREFIX + ".domains.browser_assistance.attachments", ROOT / "domains/browser_assistance/attachments.py")
service = load(PREFIX + ".domains.browser_assistance.service", ROOT / "domains/browser_assistance/service.py")
manual_reporting = load(PREFIX + ".domains.employer.manual_reporting", ROOT / "domains/employer/manual_reporting.py")
# Execute the real terminal-state fences, without loading provider/bootstrap code.
fences = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in {
    "_owned", "update_package", "approve_application", "request_execution", "cancel_application"}]
fences += [node for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
           and target.id == "LOCKED_APPLICATION_STATES" for target in node.targets)]
employer_service.models, employer_service.schemas, employer_service.HTTPException = employer, employer_schemas, HTTPException
exec(compile(ast.fix_missing_locations(ast.Module(body=fences, type_ignores=[])), "actual_employer_terminal_fences", "exec"), employer_service.__dict__)
missing = {}
for table in list(Base.metadata.tables.values()):
    for foreign in table.foreign_keys:
        target, column = foreign.target_fullname.rsplit(".", 1)
        if target not in Base.metadata.tables:
            missing.setdefault(target, {})[column] = foreign.parent.type
for name, columns in missing.items():
    Table(name, Base.metadata, *(Column(name, kind, primary_key=True) for name, kind in columns.items()))


class BrowserAssistanceFlow(unittest.TestCase):
    def setUp(self):
        self.original = {key: os.environ.get(key) for key in ("BROWSER_ASSISTANCE_ENABLED", "BROWSER_ASSISTANCE_SIGNING_SECRET", "BROWSER_ASSISTANCE_TYPED_CONTROLS_ENABLED", "BROWSER_ASSISTANCE_ATTACHMENT_ENABLED")}
        os.environ.update(BROWSER_ASSISTANCE_ENABLED="true", BROWSER_ASSISTANCE_SIGNING_SECRET="synthetic-owned-test-signing-key-over-32-characters",
                          BROWSER_ASSISTANCE_TYPED_CONTROLS_ENABLED="false", BROWSER_ASSISTANCE_ATTACHMENT_ENABLED="false")
        self.engine = create_engine("sqlite://", hide_parameters=True)
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)()
        self.db.add_all([User(id=1), User(id=2)])
        self.db.flush()
        # Foreign core rows are synthetic IDs only; employer and browser models
        # and the package-digest function are the actual shipping definitions.
        self.db.execute(Base.metadata.tables["resumes"].insert().values(id=1))
        source = employer.EmployerSource(id="source_test", employer="Razorpay", platform="greenhouse", region="global",
            board_token=service.TENANT, careers_url="https://razorpay.com/careers/", allowed_hosts=["razorpay.com"],
            verification_url="https://razorpay.com/careers/", verification_note="Synthetic exact origin fixture.", enabled=True)
        posting = employer.EmployerPosting(id="posting_test", source_id=source.id, external_id="4723040005", title="Synthetic role",
            employer="Razorpay", location="Bengaluru", description="Synthetic", canonical_url=service.ORIGIN + "/" + service.TENANT + "/jobs/4723040005",
            apply_url=service.ORIGIN + "/" + service.TENANT + "/jobs/4723040005", is_open=True,
            content_sha256="a" * 64, last_checked_at=service.now())
        content = b"%PDF-1.4\nSynthetic owned bytes; not a candidate document\n%%EOF"
        self.artifact_sha = hashlib.sha256(content).hexdigest()
        artifact = employer.SealedApplicationArtifact(id="artifact_test", user_id=1, resume_id=1, sha256=self.artifact_sha,
            filename="Synthetic.pdf", media_type="application/pdf", size_bytes=len(content), content=content)
        self.app = employer.EmployerApplication(id="apply_test", user_id=1, posting_id=posting.id, idempotency_key="application_test", input_fingerprint="c" * 64,
            resume_id=1, resume_choice="original", artifact_id=artifact.id,
            form={"version": "form_test", "fields": [{"id": identity, "label": label, "type": "text", "required": True} for identity, label in service.FIELDS]},
            answers={identity: "candidate@example.test" if identity == "email" else label for identity, label in service.FIELDS}, consents={},
            package_digest="d" * 64, job_content_sha256="a" * 64, application_mode="manual", status="ready", credit_cost=0,
            admission_snapshot=None)
        self.db.add_all([source, posting, artifact])
        self.db.flush()
        self.app.package_digest = employer_service._package_digest(self.app, posting, source, artifact)
        self.db.add(self.app)
        self.db.commit()
        self.private = ec.generate_private_key(ec.SECP256R1())
        public = self.private.public_key().public_numbers()
        self.public = {"kty": "EC", "crv": "P-256", "x": service._b64(public.x.to_bytes(32, "big")), "y": service._b64(public.y.to_bytes(32, "big"))}
        self.device_id = str(uuid4())
        self.origin = "chrome-extension://" + service.EXTENSION_ID

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        for key, value in self.original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def request(self, operation, body):
        payload = {"protocol_version": 1, "device_id": self.device_id, "request_id": str(uuid4()),
            "operation": operation, "issued_at_ms": service._ms(service.now()), "body": body}
        raw = b"hirewiz.browser-assistance.request.v1\n" + service.canonical(payload)
        first, second = decode_dss_signature(self.private.sign(raw, ec.ECDSA(hashes.SHA256())))
        return schemas.DeviceRequest(**payload, signature=service._b64(first.to_bytes(32, "big") + second.to_bytes(32, "big")))

    def call(self, operation, body):
        return service.device(self.db, self.origin, self.request(operation, body))

    def paired(self):
        prepared = self.call("prepare", {"public_key": self.public, "extension_id": service.EXTENSION_ID})
        service.candidate(self.db, 1, "confirm", schemas.CandidateRequest(identity=self.device_id, confirmed=True, key_sha256=prepared["key_sha256"]))

    def inspected(self):
        self.paired()
        context = {"synthetic_structural_fixture": True}
        return self.call("inspect", {"application_id": self.app.id, "inspection": {
            "outcome": "inspected", "origin": service.ORIGIN, "url": service.ORIGIN + "/" + service.TENANT + "/jobs/4723040005",
            "frame_id": 0, "document_id": "owned_chrome_document", "context": context, "context_digest": service.digest(context),
            "assisted_values": {identity: "" for identity, _ in service.FIELDS},
            "manual_fields": [{"id": "gender", "label": "Gender", "required": True}],
        }})

    def approved(self):
        review = self.inspected()
        service.candidate(self.db, 1, "approve", schemas.CandidateRequest(identity=review["id"], confirmed=True, review_digest=review["review_digest"]))
        return review

    def begin_body(self, review, index=0):
        return {"review_id": review["id"], "review_digest": review["review_digest"], "index": index,
            "context_digest": review["payload"]["context_digest"], "document_id": "owned_chrome_document"}

    def configure_typed_application(self, *, omit_gender=False):
        fields = list(self.app.form["fields"])
        fields.append({"id": "phone", "label": "Phone", "type": "text", "required": True})
        answers = {**self.app.answers, "phone": "+919000000000"}
        for index, label in enumerate(service.CHOICE_LABELS):
            identity = f"question_{9132967005 + index * 1000}"
            fields.append({"id": identity, "label": label, "type": "single_select", "required": True,
                           "options": [{"value": f"owned-choice-{index}", "label": f"Explicit candidate {label} choice"},
                                       {"value": f"other-choice-{index}", "label": f"Other candidate {label} choice"}]})
            if not (omit_gender and label == "Gender"):
                answers[identity] = f"owned-choice-{index}"
        self.app.form = {**self.app.form, "fields": fields}
        self.app.answers = answers
        posting = self.db.get(employer.EmployerPosting, self.app.posting_id)
        source = self.db.get(employer.EmployerSource, posting.source_id)
        artifact = self.db.get(employer.SealedApplicationArtifact, self.app.artifact_id)
        self.app.package_digest = employer_service._package_digest(self.app, posting, source, artifact)
        self.db.commit()

    def typed_review(self, *, protocol=2, omit_gender=False):
        self.configure_typed_application(omit_gender=omit_gender)
        posting = self.db.get(employer.EmployerPosting, self.app.posting_id)
        self.paired()
        context = {"synthetic_typed_fixture": True}
        mapping = service.field_mapping(self.app, protocol)
        return self.call("inspect", {"application_id": self.app.id, "inspection": {
            "review_protocol_version": protocol, "outcome": "inspected", "origin": service.ORIGIN,
            "url": posting.apply_url, "frame_id": 0, "document_id": "owned_chrome_document",
            "context": context, "context_digest": service.digest(context),
            "assisted_values": {field["id"]: "" for field in mapping},
            "manual_fields": [{"id": "country", "label": "Country", "required": True}],
        }})

    def test_typed_twelve_field_sql_flow_has_explicit_signed_control_actions_and_zero_fee(self):
        with patch.dict(os.environ, {"BROWSER_ASSISTANCE_TYPED_CONTROLS_ENABLED": "true"}):
            review = self.typed_review()
            self.assertEqual(len(review["payload"]["fields"]), 12)
            service.candidate(self.db, 1, "approve", schemas.CandidateRequest(
                identity=review["id"], confirmed=True, review_digest=review["review_digest"]))
            for index, field in enumerate(review["payload"]["fields"]):
                begun = self.call("begin", self.begin_body(review, index))
                lease = begun["lease"]
                raw = base64.urlsafe_b64decode(lease["signature"] + "=" * (-len(lease["signature"]) % 4))
                key = service._public_key(service.public_signer())[1]
                key.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
                    b"hirewiz.browser-assistance.lease.v1\n" + service.canonical(lease["payload"]), ec.ECDSA(hashes.SHA256()))
                self.assertEqual(lease["payload"]["review_protocol_version"], 2)
                self.assertEqual(lease["payload"]["control_type"], field["type"])
                self.assertEqual(lease["payload"]["value"], field["value"])
                self.assertEqual(lease["payload"]["allowed_actions"], ["fill"])
                if field["type"] == "combobox":
                    self.assertEqual(lease["payload"]["option_label"], field["value_label"])
                completed = self.call("outcome", {"review_id": review["id"], "action_id": lease["payload"]["action_id"],
                                                 "outcome": "filled"})
            self.assertEqual(completed["state"], "filled")
            self.assertEqual(completed["filled_count"], 12)
            self.assertEqual(review["payload"]["fee_credits"], 0)
            self.assertIsNone(self.app.receipt)
            self.assertEqual(self.app.charged_credits, 0)

    def test_typed_sensitive_choice_without_explicit_candidate_answer_remains_manual(self):
        with patch.dict(os.environ, {"BROWSER_ASSISTANCE_TYPED_CONTROLS_ENABLED": "true"}):
            review = self.typed_review(omit_gender=True)
            self.assertNotIn("Gender", [field["label"] for field in review["payload"]["fields"]])
            self.assertIn({"id": "question_9132968005", "label": "Gender", "required": True},
                          review["payload"]["manual_fields"])

    def test_typed_feature_off_revokes_unstarted_review_without_issuing_a_lease(self):
        with patch.dict(os.environ, {"BROWSER_ASSISTANCE_TYPED_CONTROLS_ENABLED": "true"}):
            review = self.typed_review()
            service.candidate(self.db, 1, "approve", schemas.CandidateRequest(
                identity=review["id"], confirmed=True, review_digest=review["review_digest"]))
        with patch.dict(os.environ, {"BROWSER_ASSISTANCE_TYPED_CONTROLS_ENABLED": "false"}):
            with self.assertRaises(HTTPException) as error:
                self.call("begin", self.begin_body(review))
            self.assertEqual(error.exception.status_code, 503)
            self.assertEqual(self.db.query(models.BrowserAssistanceRequest).filter_by(operation="field").count(), 0)

    def test_typed_capability_does_not_expand_historical_review_approval(self):
        with patch.dict(os.environ, {"BROWSER_ASSISTANCE_TYPED_CONTROLS_ENABLED": "true"}):
            review = self.typed_review(protocol=1)
            self.assertNotIn("review_protocol_version", review["payload"])
            self.assertEqual(len(review["payload"]["fields"]), 7)
            service.candidate(self.db, 1, "approve", schemas.CandidateRequest(
                identity=review["id"], confirmed=True, review_digest=review["review_digest"]))
            lease = self.call("begin", self.begin_body(review))["lease"]["payload"]
            self.assertNotIn("control_type", lease)
            self.assertNotIn("review_protocol_version", lease)

    def test_real_signed_sql_flow_fills_only_and_never_charges_or_confirms(self):
        review = self.approved()
        for index in range(7):
            begun = self.call("begin", self.begin_body(review, index))
            lease = begun["lease"]
            raw = base64.urlsafe_b64decode(lease["signature"] + "=" * (-len(lease["signature"]) % 4))
            key = service._public_key(service.public_signer())[1]
            key.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
                b"hirewiz.browser-assistance.lease.v1\n" + service.canonical(lease["payload"]), ec.ECDSA(hashes.SHA256()))
            self.assertEqual(lease["payload"]["allowed_actions"], ["fill"])
            self.assertEqual(lease["payload"]["artifact_sha256"], self.artifact_sha)
            self.assertLessEqual(lease["payload"]["deadline"] - lease["payload"]["issued_at_ms"], 10_000)
            completed = self.call("outcome", {"review_id": review["id"], "action_id": lease["payload"]["action_id"], "outcome": "filled"})
        self.assertEqual(completed["state"], "filled")
        self.assertEqual(completed["filled_count"], 7)
        self.assertEqual(self.app.charged_credits, 0)
        self.assertEqual(self.app.status, "ready")
        self.assertEqual(self.db.query(employer.ServiceCreditReservation).count(), 0)

    def test_unapproved_begin_has_no_lease_or_field_row(self):
        review = self.inspected()
        with self.assertRaises(HTTPException):
            self.call("begin", self.begin_body(review))
        self.db.rollback()
        self.assertEqual(self.db.query(models.BrowserAssistanceRequest).filter_by(operation="field").count(), 0)

    def test_owner_and_exact_review_digest_required(self):
        review = self.inspected()
        for owner, digest in ((2, review["review_digest"]), (1, "f" * 64)):
            with self.assertRaises(HTTPException):
                service.candidate(self.db, owner, "approve", schemas.CandidateRequest(identity=review["id"], confirmed=True, review_digest=digest))
            self.db.rollback()
        self.assertEqual(self.db.query(models.BrowserAssistanceReview).one().state, "draft")

    def test_lost_begin_replay_is_status_only_and_next_field_stays_blocked(self):
        review = self.approved()
        request = self.request("begin", self.begin_body(review))
        first = service.device(self.db, self.origin, request)
        replay = service.device(self.db, self.origin, request)
        self.assertEqual(first["state"], "begun")
        self.assertEqual(replay["state"], "unknown")
        self.assertNotIn("lease", replay)
        with self.assertRaises(HTTPException):
            self.call("begin", self.begin_body(review, 1))
        self.db.rollback()
        self.assertEqual(self.db.query(models.BrowserAssistanceRequest).filter_by(operation="field").count(), 1)

    def test_revoked_device_cannot_receive_a_field_lease(self):
        review = self.approved()
        service.candidate(self.db, 1, "revoke", schemas.CandidateRequest(identity=self.device_id, confirmed=True))
        with self.assertRaises(HTTPException):
            self.call("begin", self.begin_body(review))
        self.db.rollback()
        self.assertEqual(self.db.query(models.BrowserAssistanceRequest).filter_by(operation="field").count(), 0)

    def test_changed_package_closed_job_and_expired_review_refuse(self):
        review = self.approved()
        self.app.answers = {**self.app.answers, "first_name": "Changed"}
        self.db.commit()
        with self.assertRaises(HTTPException):
            self.call("begin", self.begin_body(review))
        self.db.rollback()
        self.app.answers = {identity: "candidate@example.test" if identity == "email" else label for identity, label in service.FIELDS}
        self.db.commit()
        posting = self.db.query(employer.EmployerPosting).one()
        posting.is_open = False
        self.db.commit()
        with self.assertRaises(HTTPException):
            self.call("begin", self.begin_body(review))
        self.db.rollback()
        posting.is_open = True
        self.db.commit()
        record = self.db.query(models.BrowserAssistanceReview).one()
        record.expires_at = service.now() - timedelta(seconds=1)
        self.db.commit()
        with self.assertRaises(HTTPException):
            self.call("begin", self.begin_body(review))
        self.db.rollback()
        service.expire_reviews(self.db)
        self.assertEqual(self.db.query(models.BrowserAssistanceReview).one().payload, {})

    def test_current_job_mapping_comes_from_actual_saved_question_ids(self):
        self.paired()
        mapped = [("first_name", "First Name"), ("last_name", "Last Name"), ("email", "Email"),
                  ("question_9123507005", "LinkedIn Profile"), ("question_9123508005", "Total Years of Experience")]
        self.app.form = {"version": "current_form", "fields": [{"id": identity, "label": label, "type": "text", "required": True} for identity, label in mapped]}
        self.app.answers = {identity: "synthetic@example.test" if identity == "email" else label for identity, label in mapped}
        posting = self.db.query(employer.EmployerPosting).one()
        artifact = self.db.query(employer.SealedApplicationArtifact).one()
        source = self.db.query(employer.EmployerSource).one()
        self.app.package_digest = employer_service._package_digest(self.app, posting, source, artifact)
        self.db.commit()
        result = self.call("application", {"application_id": self.app.id})
        self.assertEqual([field["id"] for field in result["field_mapping"]], [identity for identity, _ in mapped])
        self.assertNotIn("question_8970454005", [field["id"] for field in result["field_mapping"]])

    def test_tailored_version_and_evidence_require_current_approval(self):
        version = ResumeVersion(id="version_owned", user_id=1, resume_id=1, approval_state="approved", structured_content={"source_edits": [{"original": "Owned", "replacement": "Owned improved"}]}, evidence_ids=["evidence_owned"])
        evidence = EvidenceItem(id="evidence_owned", user_id=1, approval_state="approved")
        self.db.add_all([version, evidence])
        self.app.resume_choice = "tailored"
        self.app.resume_version_id = version.id
        artifact = self.db.query(employer.SealedApplicationArtifact).one()
        artifact.resume_version_id = version.id
        self.app.package_digest = employer_service._package_digest(self.app, self.db.query(employer.EmployerPosting).one(), self.db.query(employer.EmployerSource).one(), artifact)
        self.db.commit()
        review = self.approved()
        evidence.approval_state = "rejected"
        self.db.commit()
        with self.assertRaises(HTTPException):
            self.call("begin", self.begin_body(review))
        self.db.rollback()
        evidence.approval_state = "approved"
        version.approval_state = "draft"
        self.db.commit()
        with self.assertRaises(HTTPException):
            self.call("begin", self.begin_body(review))
        self.db.rollback()
        self.assertEqual(self.db.query(models.BrowserAssistanceRequest).filter_by(operation="field").count(), 0)

    def test_cancel_erases_unknown_or_closed_review_without_new_field_authority(self):
        review = self.approved()
        begun = self.call("begin", self.begin_body(review))
        self.call("outcome", {"review_id": review["id"], "action_id": begun["lease"]["payload"]["action_id"], "outcome": "unknown"})
        self.db.query(employer.EmployerPosting).one().is_open = False
        self.db.commit()
        cancelled = self.call("cancel", {"review_id": review["id"]})
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertEqual(cancelled["payload"], {})
        with self.assertRaises(HTTPException):
            self.call("begin", self.begin_body(review))
        self.db.rollback()

    def test_device_proof_wrong_key_origin_and_future_time_refuse(self):
        self.paired()
        request = self.request("status", {})
        with self.assertRaises(HTTPException):
            service.device(self.db, "chrome-extension://" + "a" * 32, request)
        self.db.rollback()
        changed = request.model_copy(update={"issued_at_ms": request.issued_at_ms + 100_000})
        with self.assertRaises(HTTPException):
            service.device(self.db, self.origin, changed)
        self.db.rollback()
        changed = request.model_copy(update={"signature": "A" * 86})
        with self.assertRaises(HTTPException):
            service.device(self.db, self.origin, changed)

    def test_disabled_and_missing_signer_are_fast_refusals(self):
        os.environ["BROWSER_ASSISTANCE_ENABLED"] = "false"
        self.assertEqual(service.availability()["enabled"], False)
        with self.assertRaises(HTTPException):
            self.call("prepare", {"public_key": self.public, "extension_id": service.EXTENSION_ID})
        os.environ["BROWSER_ASSISTANCE_ENABLED"] = "true"
        os.environ.pop("BROWSER_ASSISTANCE_SIGNING_SECRET")
        with self.assertRaises(HTTPException):
            self.call("prepare", {"public_key": self.public, "extension_id": service.EXTENSION_ID})
        self.db.rollback()
        self.assertEqual(self.db.query(models.BrowserAssistanceDevice).count(), 0)

    def test_manual_report_is_owner_scoped_stale_protected_and_explicit(self):
        payload = employer_schemas.ManualSubmissionReport(package_digest=self.app.package_digest, confirmed=True)
        for owner, request in [(2, payload), (1, payload.model_copy(update={"package_digest": "f" * 64}))]:
            with self.assertRaises(HTTPException):
                manual_reporting.report(self.db, owner, self.app.id, request)
            self.db.rollback()
        for confirmation in (False, 1, "true", None):
            with self.assertRaises(ValueError):
                employer_schemas.ManualSubmissionReport(package_digest=self.app.package_digest, confirmed=confirmation)
        self.assertEqual(self.app.status, "ready")
        self.assertIsNone(self.app.receipt)

    def test_manual_report_keeps_original_timestamp_zero_fee_and_fences_actual_actions(self):
        review = self.approved()
        original_key = self.app.active_key
        payload = employer_schemas.ManualSubmissionReport(package_digest=self.app.package_digest, confirmed=True)
        first = manual_reporting.report(self.db, 1, self.app.id, payload)
        timestamp = first.receipt["reported_at"]
        second = manual_reporting.report(self.db, 1, self.app.id, payload)
        self.assertEqual(second.receipt["reported_at"], timestamp)
        self.assertEqual(second.status, "user_reported")
        self.assertEqual(second.receipt["source"], "candidate_report")
        self.assertFalse(second.receipt["employer_confirmed"])
        self.assertFalse(second.receipt["used_file_verified"])
        self.assertEqual(second.active_key, original_key)
        self.assertEqual(second.charged_credits, 0)
        self.assertEqual(self.db.query(employer.ServiceCreditReservation).count(), 0)
        stopped = self.db.query(models.BrowserAssistanceReview).filter_by(id=review["id"]).one()
        self.assertEqual(stopped.state, "cancelled")
        self.assertEqual(stopped.payload, {})
        actions = [
            lambda: self.call("begin", self.begin_body(review)),
            lambda: employer_service.approve_application(self.db, 1, self.app.id, employer_schemas.ApprovalCreate(package_digest=self.app.package_digest, allowed_actions=["fill"])),
            lambda: employer_service.request_execution(self.db, 1, self.app.id, employer_schemas.ExecuteCreate(package_digest=self.app.package_digest)),
            lambda: employer_service.update_package(self.db, 1, self.app.id, employer_schemas.PackageUpdate(resume_id=1, resume_choice="original", answers={}, consents={})),
            lambda: employer_service.cancel_application(self.db, 1, self.app.id),
        ]
        for action in actions:
            with self.assertRaises(HTTPException):
                action()
            self.db.rollback()
        self.assertEqual(self.db.query(employer.EmployerApplication).one().receipt["reported_at"], timestamp)

    def test_terminal_manual_reports_free_new_opening_capacity_but_keep_duplicate_fence(self):
        # Exercise the shipping creation function and real SQL query, while
        # isolating unrelated remote form/document-storage adapters.
        create_nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in {
            "_require_enabled", "_posting", "_source", "_source_mode", "_artifact_row", "create_application"}]
        admissions_tree = ast.parse((ROOT / "domains/employer/admissions.py").read_text())
        identities = types.SimpleNamespace(payload_fingerprint=common.payload_fingerprint)
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node for node in admissions_tree.body
             if isinstance(node, ast.FunctionDef) and node.name in {"employer_identity", "opening_identity", "lock_application_set"}],
             type_ignores=[])), "actual_application_opening_identity", "exec"), identities.__dict__)
        config = load(PREFIX + ".domains.employer.config", ROOT / "domains/employer/config.py")
        source = self.db.get(employer.EmployerSource, "source_test")
        original = self.db.get(employer.SealedApplicationArtifact, self.app.artifact_id)
        sealed = types.SimpleNamespace(sha256=original.sha256, filename=original.filename, media_type=original.media_type,
            content=original.content, gcs_object=None, gcs_generation=None)
        def posting(index):
            return employer.EmployerPosting(id=f"posting_capacity_{index}", source_id=source.id,
                external_id=str(5_000_000_000 + index), title="Synthetic capacity role", employer=source.employer,
                location="Bengaluru", description="Synthetic", canonical_url=service.ORIGIN + "/" + service.TENANT + f"/jobs/{5_000_000_000 + index}",
                apply_url=service.ORIGIN + "/" + service.TENANT + f"/jobs/{5_000_000_000 + index}", is_open=True,
                content_sha256="a" * 64, last_checked_at=service.now())
        reports = []
        for index in range(100):
            job = posting(index)
            self.db.add(job)
            self.db.flush()
            opening = identities.opening_identity(job, source)
            app = employer.EmployerApplication(id=f"apply_capacity_{index}", user_id=1, posting_id=job.id,
                opening_key=opening, active_key=f"1:{opening}", idempotency_key=f"capacity-original-{index}",
                input_fingerprint="c" * 64, resume_id=1, resume_choice="original", artifact_id=original.id,
                form=self.app.form, answers=self.app.answers, consents={}, package_digest="", job_content_sha256="a" * 64,
                application_mode="manual", status="ready", credit_cost=0, charged_credits=0, admission_snapshot=None)
            app.package_digest = employer_service._package_digest(app, job, source, original)
            self.db.add(app)
            self.db.commit()
            manual_reporting.report(self.db, 1, app.id, employer_schemas.ManualSubmissionReport(package_digest=app.package_digest, confirmed=True))
            reports.append(app)
        new_job = posting(100)
        self.db.add(new_job)
        self.db.commit()
        form = {"version": "synthetic-capacity-form", "fields": [], "supported": False}
        adapters = types.SimpleNamespace(source_contract=lambda value: value, load_form=lambda *_: form)
        storage = types.SimpleNamespace(materialize_for_application=lambda *_: sealed,
            guarded_storage=lambda *_args, **_kwargs: nullcontext((sealed, None)), attach_upload=lambda *_args, **_kwargs: None)
        namespace = {"core": core, "models": employer, "schemas": employer_schemas, "config": config, "or_": or_,
            "HTTPException": HTTPException, "payload_fingerprint": common.payload_fingerprint, "public_id": common.public_id,
            "connectors": adapters, "artifacts": storage, "admissions": identities,
            "_selection_snapshot": lambda *_: (types.SimpleNamespace(id=1), None), "_quote_application": lambda *_: None}
        with patch.dict(employer_service.__dict__, namespace), patch.dict(os.environ, {"EMPLOYER_DISCOVERY_ENABLED": "true", "EMPLOYER_MAX_OPEN_APPLICATIONS": "100"}):
            exec(compile(ast.fix_missing_locations(ast.Module(body=create_nodes, type_ignores=[])), "actual_application_creation", "exec"), employer_service.__dict__)
            created = employer_service.create_application(self.db, 1, employer_schemas.ApplicationCreate(
                posting_id=new_job.id, resume_id=1, resume_choice="original", idempotency_key="capacity-new-opening"))
            self.assertEqual(created.posting_id, new_job.id)
            self.assertEqual(created.credit_cost, 0)
            with self.assertRaises(HTTPException) as duplicate:
                employer_service.create_application(self.db, 1, employer_schemas.ApplicationCreate(
                    posting_id=reports[0].posting_id, resume_id=1, resume_choice="original", idempotency_key="capacity-duplicate-opening"))
            self.assertEqual(duplicate.exception.status_code, 409)
            self.assertEqual(duplicate.exception.detail["code"], "canonical_opening_conflict")
            self.db.rollback()
        self.assertEqual(self.db.query(employer.EmployerApplication).filter_by(status="user_reported").count(), 100)
        self.assertEqual(self.db.query(employer.ServiceCreditReservation).count(), 0)

    def test_manual_report_cannot_replace_api_confirmed_unknown_or_reserved_state(self):
        payload = employer_schemas.ManualSubmissionReport(package_digest=self.app.package_digest, confirmed=True)
        for field, value in [("application_mode", "api"), ("status", "confirmed"), ("status", "unknown"), ("credit_cost", 20), ("charged_credits", 20), ("reservation_id", "hold_owned")]:
            original = getattr(self.app, field)
            setattr(self.app, field, value)
            self.db.commit()
            with self.assertRaises(HTTPException):
                manual_reporting.report(self.db, 1, self.app.id, payload)
            self.db.rollback()
            self.assertEqual(getattr(self.app, field), value)
            setattr(self.app, field, original)
            self.db.commit()
        self.assertIsNone(self.app.receipt)
        self.db.add(employer.ServiceCreditReservation(id="unlinked_hold", user_id=1, operation="job_application", source_id=self.app.id,
            unit_price=20, requested_count=1, reserved_amount=20, committed_amount=0, released_amount=0, state="reserved", pricing_version="owned_test"))
        self.db.commit()
        with self.assertRaises(HTTPException):
            manual_reporting.report(self.db, 1, self.app.id, payload)
        self.db.rollback()
        self.assertEqual(self.db.query(employer.ServiceCreditReservation).one().state, "reserved")


if __name__ == "__main__":
    unittest.main(verbosity=2)
