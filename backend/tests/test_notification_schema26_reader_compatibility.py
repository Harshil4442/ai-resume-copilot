"""Actual DB8 schema26 decoder over valid owned pre-/post-digest records.

Only intercepted SDK/SQLite fixtures are used. Refusal by this canonical reader
is not proof of production IAM, census durability, RTO or a permitted rollback.
The historical contract is byte-for-byte public source, not a rewritten union.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import FunctionType

import pytest
from test_candidate_production_factory import PASSWORD
from test_candidate_production_factory import configured as configured
from test_notification_digest_native import EMAIL, POLICY, account

from app.domains.recovery import gcp_partitioned_publication as publication
from app.domains.recovery.contracts import canonical
from app.domains.recovery.gcp_notification_digest import HEADS, HISTORY
from app.domains.recovery.gcp_partitioned_contracts import PartitionedRecord
from app.domains.recovery.gcp_scoped_census import validate_scoped_rows
from app.domains.recovery.store import GuardDenied

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).parent / "fixtures/schema26_notification_reader"
SOURCE = "db8a25adefaf3bc2e00a61f7543f8a1862597adf"
CONTRACT_SHA256 = "dabaddb6a8f3e889b86ae0b7b736225982001fe5036f68f15e8931fbfd5759d5"
DECODER_SHA256 = "9fcaf231b89854d936c6be82446971c9fcacae03ebc887748d31a767d0f43c49"
PROVENANCE_SHA256 = "d093c36236ee93dd0be9c7f32a3b9e9964489d4c81fcf5c211931f859f03b331"


def pinned_source(provenance):
    if provenance["source_commit"] != SOURCE:
        raise AssertionError("Historical reader source commit changed")
    contract = FIXTURE / provenance["contract"]["fixture"]
    if (
        provenance["contract"]["sha256"] != CONTRACT_SHA256
        or hashlib.sha256(contract.read_bytes()).hexdigest() != CONTRACT_SHA256
        or provenance["reused_decoder"]["sha256"] != DECODER_SHA256
        or hashlib.sha256((ROOT / provenance["reused_decoder"]["path"]).read_bytes()).hexdigest()
        != DECODER_SHA256
    ):
        raise AssertionError("Historical public model/decoder source changed")
    for name, expected in provenance["shared_source_sha256"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise AssertionError("Historical reader shared model/decoder dependency changed")
    return contract


@pytest.fixture
def historical_reader():
    raw_provenance = (FIXTURE / "PROVENANCE.json").read_bytes()
    if hashlib.sha256(raw_provenance).hexdigest() != PROVENANCE_SHA256:
        raise AssertionError("Frozen historical reader provenance changed")
    provenance = json.loads(raw_provenance)
    contract = pinned_source(provenance)
    name = "app.domains.recovery._owned_db8_notification_contracts"
    assert name not in sys.modules
    loader = SourceFileLoader(name, str(contract))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        loader.exec_module(module)
        # The decoder's complete source is still byte-identical to live DB8.
        # Copy its namespace, replacing only its historical model bindings;
        # never modify the production function, globals or implementation.
        original = publication.partitioned_intent
        globals_copy = dict(original.__globals__)
        globals_copy.update(ScopedJournalIntent=module.ScopedJournalIntent,
                            PartitionedReference=module.PartitionedReference)
        decoder = FunctionType(original.__code__, globals_copy, original.__name__,
                               original.__defaults__, original.__closure__)
        decoder.__kwdefaults__ = original.__kwdefaults__
        yield decoder, module
        assert publication.partitioned_intent is original
        assert publication.partitioned_intent.__globals__["ScopedJournalIntent"] is not module.ScopedJournalIntent
    finally:
        assert sys.modules.pop(name) is module


def test_actual_old_reader_accepts_existing_rows_then_refuses_valid_new_digest_record(
    configured, monkeypatch, historical_reader
):
    old_decode, old_contract = historical_reader
    native, context, revision = account(configured, monkeypatch)
    # Include an actual supported scoped-denial record, not only the decoder's
    # older password-lifetime fallback branch, before making a fresh session.
    configured.accounts.logout(context)
    login = configured.accounts.login(email="synthetic@example.test", password=PASSWORD)
    before = configured.native.values()
    validate_scoped_rows(before)
    resource = configured.config.publication_resource
    old_resource = old_contract.PartitionedResource.model_validate(resource.model_dump(mode="json"))
    old_scoped = 0
    for (namespace, _), raw in before.items():
        if namespace != "v3_records":
            continue
        full = PartitionedRecord.model_validate(raw)
        raw_intent = canonical(full.intent).encode()
        ref, decoded = old_decode(raw_intent, old_resource)
        assert ref.model_dump(mode="json") == full.reference.model_dump(mode="json")
        assert decoded == full.intent
        old_scoped += ref.kind == "scoped_admission"
    assert old_scoped > 0

    head = native.change(login.context, revision, expected_generation=0, enabled=True,
                         email_sha256=EMAIL, policy_version=POLICY)
    after = configured.native.values()
    assert ("v3_records", str(head.operation_id)) not in before
    assert after[HEADS, str(revision.subject_uuid)] == head.model_dump(mode="json")
    assert any(namespace == HISTORY for namespace, _ in after)
    validate_scoped_rows(after)  # Valid current provenance, not a malformed-row counterexample.
    full = PartitionedRecord.model_validate(after["v3_records", str(head.operation_id)])
    raw_intent = canonical(full.intent).encode()
    reference, decoded = publication.partitioned_intent(raw_intent, resource)
    assert reference == full.reference and decoded == full.intent
    with pytest.raises(GuardDenied, match="^Full canonical typed V3 publication intent is required$"):
        old_decode(raw_intent, old_resource)


def test_changed_shared_source_pin_refuses_before_loading_historical_reader():
    provenance = json.loads((FIXTURE / "PROVENANCE.json").read_text())
    name = next(iter(provenance["shared_source_sha256"]))
    provenance["shared_source_sha256"][name] = "0" * 64
    with pytest.raises(AssertionError, match="shared model/decoder dependency changed"):
        pinned_source(provenance)
