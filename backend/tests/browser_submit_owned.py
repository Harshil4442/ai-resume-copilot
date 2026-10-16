"""Owned synthetic full ORM/credit/crypto fixture, never production authority."""

import hashlib
import json
from uuid import uuid4

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from backend.app import models as core
from backend.app.domains.browser_assistance import models, schemas, service, submissions
from backend.app.domains.employer import models as employer
from backend.app.domains.employer.service import _package_digest
from backend.tests.expense_policy_fixtures import synthetic_expense_policy
from backend.tests.test_employer_services import context


class OwnedAuthority:
    def __init__(self, owned):
        self.owned, self.calls, self.acks = owned, [], set()
        self.fail = self.on_reserve = None

    def reserve(self, context, binding):
        assert not self.owned.db.in_transaction(), "Native IO ran under SQL locks"
        self.calls.append((context, binding))
        if self.on_reserve:
            self.on_reserve()
        if self.fail == "unknown":
            return None
        if self.fail == "exception":
            raise RuntimeError("Owned lost native acknowledgement")
        ack = object()
        self.acks.add(ack)
        return ack

    def consume_ack(self, ack, binding):
        assert ack in self.acks
        self.acks.remove(ack)
        return {"owned_test_only": True, "binding_sha256": binding.digest, "epoch_generation": 1}


class OwnedSubmit:
    def __init__(self, monkeypatch):
        self.factory, _, self.content = context.__wrapped__(monkeypatch)
        self.db = self.factory()
        for key, value in {
            "BROWSER_ASSISTANCE_ENABLED": "true",
            "BROWSER_ASSISTANCE_TYPED_CONTROLS_ENABLED": "true",
            "BROWSER_ASSISTANCE_ATTACHMENT_ENABLED": "true",
            "BROWSER_ASSISTANCE_SUBMIT_ENABLED": "true",
            "BROWSER_ASSISTANCE_SIGNING_SECRET": "owned-synthetic-final-submit-key-over-32-characters",
            "BROWSER_ASSISTANCE_SUBMIT_EXECUTOR_REVISION": "companion_0_6_0",
            "EMPLOYER_APPLY_CREDITS_PER_JOB": "20",
        }.items():
            monkeypatch.setenv(key, value)
        policy = synthetic_expense_policy()
        policy["service_apply_credits"] = 20
        monkeypatch.setenv("HIREWIZ_EXPENSE_POLICY_JSON", json.dumps(policy))
        self.target = service.ORIGIN + "/" + service.TENANT + "/jobs/4740333005"
        self.artifact_sha = hashlib.sha256(self.content).hexdigest()
        self.mapping = [
            {"id": identity, "label": label, "type": "text", "required": True}
            for identity, label in service.FIELDS[:3]
        ]
        self.values = {
            "first_name": "Owned",
            "last_name": "Synthetic",
            "email": "owned@example.test",
        }
        user = self.db.get(core.User, 1)
        user.password_hash = "synthetic-nonusable-credential-hash"
        user.job_service_credits = 100
        self.db.add(
            core.CandidatePasswordAccount(
                user_id=1,
                subject_uuid=str(uuid4()),
                account_binding_id=str(uuid4()),
                principal_sha256="c" * 64,
                credential_sha256=hashlib.sha256(user.password_hash.encode()).hexdigest(),
                auth_generation=1,
                state="ACTIVE",
            )
        )
        source = self.db.get(employer.EmployerSource, "source_test")
        source.employer, source.board_token = "Razorpay", service.TENANT
        posting = self.db.get(employer.EmployerPosting, "job_1")
        posting.external_id = "4740333005"
        posting.apply_url = posting.canonical_url = self.target
        posting.employer = "Razorpay"
        artifact = employer.SealedApplicationArtifact(
            id="artifact_owned",
            user_id=1,
            resume_id=10,
            sha256=self.artifact_sha,
            filename="Owned.pdf",
            media_type="application/pdf",
            size_bytes=len(self.content),
            content=self.content,
            gcs_generation="1",
        )
        self.db.add(artifact)
        self.db.flush()
        self.app = employer.EmployerApplication(
            id="apply_owned",
            user_id=1,
            posting_id=posting.id,
            idempotency_key="owned_application",
            input_fingerprint="f" * 64,
            resume_id=10,
            resume_choice="original",
            artifact_id=artifact.id,
            form={"version": "owned_form", "fields": self.mapping},
            answers=self.values,
            consents={},
            package_digest="d" * 64,
            job_content_sha256=posting.content_sha256,
            application_mode="manual",
            status="ready",
            credit_cost=0,
        )
        self.app.package_digest = _package_digest(self.app, posting, source, artifact)
        self.db.add(self.app)
        self.db.commit()
        self.key = ec.generate_private_key(ec.SECP256R1())
        numbers = self.key.public_key().public_numbers()
        self.public = {
            "kty": "EC",
            "crv": "P-256",
            "x": service._b64(numbers.x.to_bytes(32, "big")),
            "y": service._b64(numbers.y.to_bytes(32, "big")),
        }
        self.device_id = str(uuid4())
        self.origin = "chrome-extension://" + service.EXTENSION_ID
        self.native = OwnedAuthority(self)
        monkeypatch.setattr(submissions, "authority", lambda: self.native)

    def request(self, operation, body, request_id=None):
        unsigned = {
            "protocol_version": 1,
            "device_id": self.device_id,
            "request_id": request_id or str(uuid4()),
            "operation": operation,
            "issued_at_ms": service._ms(service.now()),
            "body": body,
        }
        r, s = decode_dss_signature(
            self.key.sign(
                b"hirewiz.browser-assistance.request.v1\n" + service.canonical(unsigned),
                ec.ECDSA(hashes.SHA256()),
            )
        )
        return schemas.DeviceRequest(
            **unsigned, signature=service._b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        )

    def device(self, operation, body):
        return service.device(self.db, self.origin, self.request(operation, body))

    def candidate(self, operation, identity, **kwargs):
        return service.candidate(
            self.db, 1, operation, schemas.CandidateRequest(identity=identity, **kwargs)
        )

    def connect(self):
        result = self.device(
            "prepare", {"public_key": self.public, "extension_id": service.EXTENSION_ID}
        )
        return self.candidate(
            "confirm", self.device_id, confirmed=True, key_sha256=result["key_sha256"]
        )

    def seed_selection(self):
        review = models.BrowserAssistanceReview(
            id=str(uuid4()),
            user_id=1,
            device_id=self.device_id,
            application_id=self.app.id,
            package_digest=self.app.package_digest,
            artifact_sha256=self.artifact_sha,
            context_digest="b" * 64,
            review_digest="d" * 64,
            payload={},
            state="filled",
            next_index=1,
            review_protocol_version=3,
            expires_at=service.now() + service.timedelta(minutes=5),
        )
        self.db.add(review)
        self.db.flush()
        action = models.BrowserAssistanceRequest(
            id=str(uuid4()),
            device_id=self.device_id,
            request_id=str(uuid4()),
            operation="attachment",
            request_sha256="a" * 64,
            review_id=review.id,
            field_index=0,
            state="filled",
            response={
                "delivery_claimed": True,
                "selection_reported": True,
                "selection_document_id": "owned_document",
                "selection_target_url": self.target,
                "selection_artifact_sha256": self.artifact_sha,
            },
        )
        self.db.add(action)
        self.db.commit()
        return {
            "review_id": review.id,
            "action_id": action.id,
            "filename": "Owned.pdf",
            "status": "portal_reports_uploaded",
        }

    def review(self):
        self.connect()
        upload = self.seed_selection()
        self.device("application", {"application_id": self.app.id, "review_protocol_version": 4})
        return self.device(
            "inspect",
            {
                "application_id": self.app.id,
                "inspection": {
                    "review_protocol_version": 4,
                    "outcome": "inspected",
                    "origin": service.ORIGIN,
                    "url": self.target,
                    "frame_id": 0,
                    "document_id": "owned_document",
                    "context_digest": "e" * 64,
                    "assisted_values": self.values,
                    "manual_fields": [],
                    "ready": True,
                    "upload": upload,
                    "upload_continuity": {
                        "review_id": upload["review_id"],
                        "action_id": upload["action_id"],
                        "artifact_sha256": self.artifact_sha,
                        "document_id": "owned_document",
                        "target_url": self.target,
                    },
                },
            },
        )

    def approved(self):
        review = self.review()
        return self.candidate(
            "approve", review["id"], confirmed=True, review_digest=review["review_digest"]
        )

    def begin_request(self, review):
        return self.request(
            "begin",
            {
                "review_id": review["id"],
                "review_digest": review["review_digest"],
                "index": 0,
                "context_digest": review["payload"]["context_digest"],
                "document_id": review["payload"]["document_id"],
            },
        )

    def audit(self):
        self.db.expire_all()
        app = self.db.get(employer.EmployerApplication, self.app.id)
        return {
            "credits": self.db.get(core.User, 1).job_service_credits,
            "application_status": app.status,
            "charged_credits": app.charged_credits,
            "receipt": app.receipt,
            "attempts": self.db.query(employer.EmployerApplicationAttempt).count(),
            "submit_actions": self.db.query(models.BrowserAssistanceRequest)
            .filter_by(operation="submit")
            .count(),
            "reservations": [
                {
                    "state": row.state,
                    "reserved": row.reserved_amount,
                    "committed": row.committed_amount,
                    "released": row.released_amount,
                }
                for row in self.db.query(employer.ServiceCreditReservation)
            ],
            "native_calls": len(self.native.calls),
        }

    def close(self):
        self.db.close()
        self.factory.kw["bind"].dispose()
