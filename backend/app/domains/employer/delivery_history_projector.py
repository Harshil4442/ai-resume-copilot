"""Pure projection of an explicitly trusted, owner-bound paid delivery.

Private Stage C unit. No database, filesystem, provider, pricing, or app imports.
Evidence wrappers must be made by an authoritative archive/record reader, never
from request JSON. This function validates concordance; it cannot authenticate
an archive or assert durable history completeness. It does not charge anything.

Known saved-item shapes come from ordinary service.py at 8c246dc (initial),
7cc49c (canonical key), and 92d1cc (preferences/quote). All emitted item charges;
only additive opening_key/preferences/quote fields may be absent here. No known
contract permits an omitted charged_credits item field.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Literal, TypeGuard, cast

PROJECTION_VERSION = "employer-delivery-history-v1"
# Descriptive version for the exact unversioned recipe first present at this commit.
IDENTITY_RECIPE_VERSION = "employer-opening-identity@7cc49c2c08a296b33cb30f96e383b959caf214c0"
DELIVERY_AUTHORITY = "verified_owner_bound_paid_database_row"
SEARCH_AUTHORITY = "verified_owner_bound_saved_search"
ARCHIVE_AUTHORITY = "verified_immutable_delivery_time_identity_archive"
LEGACY_CONTRACT = "employer-search-items@8c246dcad0cc20eacf48f266a5ec85fcb8a05074"
OPENING_CONTRACT = "employer-search-items@7cc49c2c08a296b33cb30f96e383b959caf214c0"
PREFERENCE_CONTRACT = "employer-search-items@92d1cc5a8baad7028d8e3c52e34e0c522df40caa"
_KNOWN_CONTRACTS = {
    LEGACY_CONTRACT: False,
    OPENING_CONTRACT: True,
    PREFERENCE_CONTRACT: True,
}
_HASH = re.compile(r"^[a-f0-9]{64}$")
_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]+$")
# Defensive JSON validation bounds, not a candidate or history traversal limit.
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 100_000
MAX_JSON_STRING = 1_000_000
MAX_CREDIT_AMOUNT = (1 << 31) - 1  # Existing SQL Integer authority.


@dataclass(frozen=True)
class PaidDeliveryEvidence:
    authority: str
    owner_id: int
    delivery_id: str
    search_id: str
    posting_id: str
    charged_credits: object
    opening_key: object = None


@dataclass(frozen=True)
class SavedSearchEvidence:
    authority: str
    owner_id: int
    search_id: str
    contract: str
    evidence_reference: str
    items: object


@dataclass(frozen=True)
class HistoricalIdentityEvidence:
    """Verified historical facts, not live source/posting fields or embedded JSON.

    An upstream verifier must establish the authority, immutability and binding
    to this delivery's observation time. Explicit None for requisition/employer
    key means their absence was known in that archive, not inferred today.
    """

    authority: str
    immutable: bool
    delivery_time_bound: bool
    evidence_reference: str
    recipe_version: str
    owner_id: int
    delivery_id: str
    search_id: str
    posting_id: str
    source_id: str
    platform: str
    region: str
    tenant: str
    verified_employer_key: str | None
    requisition_id: str | None
    external_id: str
    opening_key: object = None


@dataclass(frozen=True)
class IdentityPreimage:
    employer: str
    identity_kind: Literal["requisition", "external_posting"]
    identity: str
    provider: str | None

    def fingerprint(self) -> str:
        # Exactly domains/common.py payload_fingerprint for these scalar facts.
        return _fingerprint({
            "employer": self.employer,
            "identity_kind": self.identity_kind,
            "identity": self.identity,
            "provider": self.provider,
        })


State = Literal[
    "verified_existing_key",
    "verified_historical_reconstruction",
    "paid_posting_identity_pending",
    "needs_review",
    "not_paid",
]


@dataclass(frozen=True)
class HistoryProjection:
    version: str
    state: State
    reason_codes: tuple[str, ...]
    original_charged_credits: int | None = None
    same_posting_alias: tuple[int, str] | None = None
    opening_key: str | None = None
    identity_recipe_version: str | None = None
    preimage: IdentityPreimage | None = None
    matched_item_ordinals: tuple[int, ...] = ()
    item_digest: str | None = None
    proof_references: tuple[str, ...] = ()

    @property
    def canonical_entry_authorized(self) -> bool:
        return self.state in {"verified_existing_key", "verified_historical_reconstruction"}


@dataclass(frozen=True)
class _Base:
    amount: int
    alias: tuple[int, str]
    ordinals: tuple[int, ...] = ()
    item_digest: str | None = None
    proofs: tuple[str, ...] = ()


def _result(state: State, *codes: str, base: _Base | None = None,
            opening_key: str | None = None, preimage: IdentityPreimage | None = None,
            recipe_version: str | None = None) -> HistoryProjection:
    return HistoryProjection(
        version=PROJECTION_VERSION, state=state, reason_codes=tuple(codes),
        original_charged_credits=base.amount if base else None,
        same_posting_alias=base.alias if base else None,
        matched_item_ordinals=base.ordinals if base else (),
        item_digest=base.item_digest if base else None,
        proof_references=base.proofs if base else (),
        opening_key=opening_key, preimage=preimage, identity_recipe_version=recipe_version,
    )


def _positive_int(value: object) -> TypeGuard[int]:
    return type(value) is int and 0 < value <= MAX_CREDIT_AMOUNT


def _token(value: object, maximum: int = 200) -> TypeGuard[str]:
    return type(value) is str and 0 < len(value) <= maximum and _TOKEN.fullmatch(value) is not None


def _hash(value: object) -> TypeGuard[str]:
    return type(value) is str and _HASH.fullmatch(value) is not None


def _bounded_text(value: object, maximum: int) -> TypeGuard[str]:
    return type(value) is str and 0 < len(value) <= maximum


def _fingerprint(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _json_digest(value: object) -> str | None:
    """Validate literal JSON without invoking user object hooks or leaking payloads."""
    active: set[int] = set()
    nodes = 0

    def visit(item: object, depth: int) -> bool:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            return False
        kind = type(item)
        if item is None or kind is bool:
            return True
        if kind is str:
            return len(cast(str, item)) <= MAX_JSON_STRING
        if kind is int:
            # Prevent pathological conversion limits before canonical JSON encoding.
            return cast(int, item).bit_length() <= 4096
        if kind is float:
            return math.isfinite(cast(float, item))
        if kind not in {dict, list} or id(item) in active:
            return False
        active.add(id(item))
        if kind is dict:
            valid = all(type(key) is str and len(key) <= MAX_JSON_STRING
                        and visit(child, depth + 1) for key, child in cast(dict[str, object], item).items())
        else:
            valid = all(visit(child, depth + 1) for child in cast(list[object], item))
        active.remove(id(item))
        return valid

    if not visit(value, 0):
        return None
    try:
        return _fingerprint(value)
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
        return None


def _historical_preimage(archive: HistoricalIdentityEvidence) -> IdentityPreimage | None:
    if (type(archive.authority) is not str or archive.authority != ARCHIVE_AUTHORITY or archive.immutable is not True
            or archive.delivery_time_bound is not True
            or type(archive.recipe_version) is not str or archive.recipe_version != IDENTITY_RECIPE_VERSION
            or not _token(archive.evidence_reference)
            or not _token(archive.source_id, 64)
            or not _token(archive.platform, 40)
            or not _token(archive.region, 16)
            or not _token(archive.tenant, 120)
            or not _bounded_text(archive.external_id, 160)
            or (archive.verified_employer_key is not None
                and not _bounded_text(archive.verified_employer_key, 120))
            or (archive.requisition_id is not None
                and (type(archive.requisition_id) is not str or len(archive.requisition_id) > 160))):
        return None
    employer = _fingerprint(
        {"verified_employer_key": archive.verified_employer_key}
        if archive.verified_employer_key
        else {"platform": archive.platform, "region": archive.region, "tenant": archive.tenant}
    )
    requisition = (archive.requisition_id or "").strip()
    return IdentityPreimage(
        employer=employer,
        identity_kind="requisition" if requisition else "external_posting",
        identity=requisition or archive.external_id,
        provider=None if requisition else archive.platform,
    )


def project_paid_delivery(*, owner_id: object, delivery: object, search: object | None,
                          identity_archive: object | None = None) -> HistoryProjection:
    """Conserve paid evidence; authorize canonical registry entries only with proof.

    All failures return bounded static reason codes. Saved items, quote, amount,
    and order are never changed or copied to output. Same-posting aliases are
    conservative paid-replay authority, not inferred cross-source canonical keys.
    """
    if (not _positive_int(owner_id) or type(delivery) is not PaidDeliveryEvidence
            or type(delivery.authority) is not str or delivery.authority != DELIVERY_AUTHORITY
            or type(delivery.owner_id) is not int or delivery.owner_id != owner_id
            or not _token(delivery.delivery_id, 64) or not _token(delivery.search_id, 64)
            or not _token(delivery.posting_id, 64)):
        return _result("needs_review", "delivery_owner_or_authority_invalid")
    amount = delivery.charged_credits
    if type(amount) is not int or not 0 <= amount <= MAX_CREDIT_AMOUNT:
        return _result("needs_review", "delivery_charge_invalid")
    if amount == 0:
        return _result("not_paid", "zero_delivery_charge")
    base = _Base(amount=amount, alias=(owner_id, delivery.posting_id))
    if search is None:
        return _result("paid_posting_identity_pending", "saved_search_missing", base=base)
    if (type(search) is not SavedSearchEvidence
            or type(search.authority) is not str or search.authority != SEARCH_AUTHORITY
            or type(search.owner_id) is not int or search.owner_id != owner_id
            or type(search.search_id) is not str or search.search_id != delivery.search_id
            or not _token(search.evidence_reference)):
        # Conflicting owner/search authority cannot authorize even an alias here.
        return _result("needs_review", "search_owner_or_authority_invalid")
    if type(search.contract) is not str or search.contract not in _KNOWN_CONTRACTS:
        return _result("needs_review", "snapshot_contract_unknown", base=base)
    if type(search.items) is not list or _json_digest(search.items) is None:
        return _result("needs_review", "snapshot_json_invalid", base=base)
    matches: list[tuple[int, dict]] = []
    for ordinal, item in enumerate(search.items):
        if type(item) is not dict or type(item.get("posting")) is not dict:
            return _result("needs_review", "snapshot_item_invalid", base=base)
        posting = item["posting"]
        if not _token(posting.get("id"), 64):
            return _result("needs_review", "snapshot_posting_id_invalid", base=base)
        if posting["id"] == delivery.posting_id:
            matches.append((ordinal, item))
    if not matches:
        return _result("paid_posting_identity_pending", "saved_item_missing", base=base)
    digests = [_json_digest(item) for _, item in matches]
    base = _Base(amount=amount, alias=base.alias, ordinals=tuple(row[0] for row in matches),
                 item_digest=digests[0], proofs=(search.evidence_reference,))
    for _, item in matches:
        if "charged_credits" not in item:
            return _result("needs_review", "item_charge_missing_in_known_contract", base=base)
        if not _positive_int(item["charged_credits"]) or item["charged_credits"] != amount:
            return _result("needs_review", "item_charge_conflicts_with_paid_delivery", base=base)
    if len(set(digests)) > 1:
        return _result("needs_review", "duplicate_items_conflict", base=base)
    posting = matches[0][1]["posting"]
    if (not _token(posting.get("source_id"), 64)
            or not _bounded_text(posting.get("external_id"), 160)
            or not _token(posting.get("platform"), 40)):
        return _result("needs_review", "snapshot_structural_identity_invalid", base=base)
    for container in (matches[0][1], posting):
        for field in ("owner_id", "user_id"):
            if field in container and (type(container[field]) is not int or container[field] != owner_id):
                return _result("needs_review", "snapshot_owner_evidence_conflict", base=base)
        if "search_id" in container and (type(container["search_id"]) is not str
                                          or container["search_id"] != delivery.search_id):
            return _result("needs_review", "snapshot_search_evidence_conflict", base=base)
    if "opening_key" in matches[0][1]:
        return _result("needs_review", "unsupported_item_level_opening_key", base=base)
    # No archived contract defines recipe JSON in a posting. It is not authority.
    if any(name in container for name in ("identity_recipe", "identity_recipe_version",
                                         "canonical_identity_snapshot")
           for container in (matches[0][1], posting)):
        return _result("needs_review", "untrusted_embedded_identity_recipe", base=base)
    keys: list[str] = []
    if delivery.opening_key is not None:
        if not _hash(delivery.opening_key):
            return _result("needs_review", "delivery_opening_key_invalid", base=base)
        keys.append(delivery.opening_key)
    if "opening_key" in posting:
        if not _hash(posting["opening_key"]):
            return _result("needs_review", "snapshot_opening_key_invalid", base=base)
        keys.append(posting["opening_key"])
    if len(set(keys)) > 1:
        return _result("needs_review", "stored_opening_keys_conflict", base=base)
    if identity_archive is not None:
        if type(identity_archive) is not HistoricalIdentityEvidence:
            return _result("needs_review", "historical_identity_authority_invalid", base=base)
        archive = identity_archive
        if (type(archive.owner_id) is not int or archive.owner_id != owner_id
                or type(archive.delivery_id) is not str or archive.delivery_id != delivery.delivery_id
                or type(archive.search_id) is not str or archive.search_id != delivery.search_id
                or type(archive.posting_id) is not str or archive.posting_id != delivery.posting_id):
            return _result("needs_review", "historical_identity_binding_conflict", base=base)
        preimage = _historical_preimage(archive)
        if preimage is None:
            return _result("needs_review", "historical_identity_authority_invalid", base=base)
        # Bind the original saved structural fields; never use today's source row.
        if (posting.get("source_id") != archive.source_id
                or posting.get("external_id") != archive.external_id
                or posting.get("platform") != archive.platform):
            return _result("needs_review", "historical_identity_snapshot_conflict", base=base)
        for field, archived in (("region", archive.region), ("board_token", archive.tenant),
                                ("employer_key", archive.verified_employer_key),
                                ("requisition_id", archive.requisition_id)):
            if field in posting and posting[field] != archived:
                return _result("needs_review", "historical_identity_snapshot_conflict", base=base)
        calculated = preimage.fingerprint()
        if archive.opening_key is not None:
            if not _hash(archive.opening_key) or archive.opening_key != calculated:
                return _result("needs_review", "historical_key_recipe_conflict", base=base)
        if keys and keys[0] != calculated:
            return _result("needs_review", "stored_key_recipe_conflict", base=base)
        base = _Base(amount=amount, alias=base.alias, ordinals=base.ordinals,
                     item_digest=base.item_digest,
                     proofs=base.proofs + (archive.evidence_reference,))
        return _result("verified_existing_key" if keys else "verified_historical_reconstruction",
                       "immutable_historical_identity_concordant", base=base,
                       opening_key=calculated, preimage=preimage,
                       recipe_version=IDENTITY_RECIPE_VERSION)
    if any(field in posting for field in ("region", "board_token", "employer_key", "requisition_id")):
        return _result("needs_review", "untrusted_embedded_identity_components", base=base)
    # A recognized key-emitting server contract can prove a saved original key.
    # A bare key in a pre-key contract/delivery is not silently promoted.
    if keys and _KNOWN_CONTRACTS[search.contract] and "opening_key" in posting:
        return _result("verified_existing_key", "owner_bound_saved_key_concordant", base=base,
                       opening_key=keys[0], recipe_version=IDENTITY_RECIPE_VERSION)
    if keys:
        return _result("paid_posting_identity_pending", "stored_key_provenance_pending", base=base)
    return _result("paid_posting_identity_pending", "historical_identity_archive_missing", base=base)
