"""Trusted SQL projection callbacks from the original protected pairing bridge.

This is not an HTTP enrollment API. Only the production pairing composition
injects these callbacks, after original native/protected acknowledgement checks.
SQL projection cannot substitute for the fresh protected submit-device checks.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import Engine
from sqlalchemy.engine.base import OptionEngine
from sqlalchemy.orm import sessionmaker

from ...models import CandidatePasswordAccount, User
from ..candidate_accounts.service import revision_from_row
from ..recovery.gcp_submit_contracts import PairedSubmitDeviceRegistration, SubmitDeviceGrant
from ..recovery.pairing_auth import key_fingerprint, public_jwk
from ..recovery.password_reauth import CandidateWebSession
from ..recovery.store import GuardDenied, GuardUnavailable
from .models import BrowserAssistanceDevice as Device
from .models import BrowserAssistanceReview as Review


class PairingProjection:
    def __init__(self, engine: Engine):
        # Accept the stock private view without admitting arbitrary Engine
        # subclasses or relaxing the candidate SQL parameter-redaction boundary.
        if type(engine) not in {Engine, OptionEngine} or engine.hide_parameters is not True:
            raise GuardUnavailable("Private pairing projection is unavailable")
        self.sessions = sessionmaker(bind=engine, expire_on_commit=False)

    def activated(self, grant: SubmitDeviceGrant, registration: PairedSubmitDeviceRegistration,
                  extension_id: str) -> None:
        from .service import EXTENSION_ID, digest
        if (type(grant) is not SubmitDeviceGrant or type(registration) is not PairedSubmitDeviceRegistration
                or registration.grant != grant or extension_id != EXTENSION_ID
                or key_fingerprint(public_jwk(grant.public_key)) != grant.key_sha256):
            raise GuardDenied("Exact original paired activation is required")
        context = grant.context
        with self.sessions() as db:
            user = db.query(User).filter_by(id=context.candidate_id).with_for_update().one_or_none()
            row = db.query(CandidatePasswordAccount).filter_by(user_id=context.candidate_id).with_for_update().one_or_none()
            if user is None or row is None:
                raise GuardDenied("Paired candidate projection is absent")
            current = revision_from_row(row, user.password_hash)
            if current.model_dump(mode="json") != context.model_dump(mode="json"):
                raise GuardDenied("Paired candidate credential ownership changed")
            identity = str(grant.device_id)
            record = db.query(Device).filter_by(id=identity).with_for_update().one_or_none()
            key_sha256 = digest(grant.public_key)  # Legacy device transport uses plain canonical SHA.
            other = db.query(Device.id).filter(Device.key_sha256 == key_sha256, Device.id != identity).first()
            if other is not None:
                raise GuardDenied("This device key already has a different SQL identity")
            if record is not None:
                # A read/claim/status must never reactivate an existing device.
                # Only the original callback can finish its exact pending row.
                if (record.state != "pending" or record.user_id not in {None, context.candidate_id}
                        or record.extension_id != extension_id or record.public_key != grant.public_key
                        or record.key_sha256 != key_sha256):
                    raise GuardDenied("Paired device projection cannot be adopted or replaced")
            else:
                record = Device(id=identity, extension_id=extension_id, public_key=grant.public_key,
                    key_sha256=key_sha256, state="pending", pairing_expires_at=datetime.now(UTC) + timedelta(minutes=5))
                db.add(record)
            record.user_id, record.state, record.confirmed_at = context.candidate_id, "active", datetime.now(UTC)
            db.commit()

    def deny_sql(self, context: CandidateWebSession, device_id: UUID) -> None:
        """Explicit signed revoke denies SQL first, including unknown native outcomes."""
        from . import submissions
        with self.sessions() as db:
            user = db.query(User).filter_by(id=context.candidate_id).with_for_update().one_or_none()
            row = db.query(CandidatePasswordAccount).filter_by(user_id=context.candidate_id).with_for_update().one_or_none()
            record = db.query(Device).filter_by(id=str(device_id), user_id=context.candidate_id).with_for_update().one_or_none()
            if (user is None or row is None or record is None
                    or row.account_binding_id != str(context.account_binding_id)):
                raise GuardDenied("Exact paired candidate/device is required")
            revision_from_row(row, user.password_hash)
            record.state, record.revoked_at = "revoked", datetime.now(UTC)
            for review in db.query(Review).filter_by(device_id=record.id, user_id=context.candidate_id).filter(Review.state.in_(["draft", "approved", "running"])):
                if review.review_protocol_version == 4:
                    submissions.cancel(db, review)
                else:
                    review.state, review.payload = "cancelled", {}
            db.commit()


def activation_port(engine: Engine) -> PairingProjection:
    return PairingProjection(engine)
