"""Actual V3 native full-record registration, pagination and rollback refusal."""

from __future__ import annotations

from uuid import uuid4

import pytest
from backend.tests.fixtures.gcp_partitioned_publication import partitioned_for
from test_gcp_pairing_emulator import case as case

from app.domains.recovery.contracts import canonical
from app.domains.recovery.gcp_contracts import AmbiguousCommit, JournalIntent, OpeningHold
from app.domains.recovery.store import GuardUnavailable


def intent(c):
    return JournalIntent(operation_id=uuid4(), pin=c.pin,
        effect=OpeningHold(subject_uuid=c.subject, employer_key="a" * 64,
            tenant_id="synthetic", opening_key="b" * 64, binding_sha256="c" * 64),
        created_at_ms=c.now, deadline_ms=c.now + 60_000)


def retain(publisher, value):
    publisher.retain(canonical(value.model_dump(mode="json")).encode(), publisher.resource.bucket)


def test_partitioned_native_cut_enumerates_more_than_63_records_across_all_registered_segments(case):
    pub = partitioned_for(case)
    inputs = tuple(intent(case) for _ in range(80))
    for value in inputs:
        retain(pub, value)
    closed = pub.close()
    assert closed is not None
    cut, records = pub.seal_native(closed, max_documents=512)
    assert cut.records_count == 80 and len(cut.segments) > 16
    assert {r.reference.operation_id for r in records} == {v.operation_id for v in inputs}
    assert all(r.intent == v.model_dump(mode="json") for r in records for v in inputs if r.reference.operation_id == v.operation_id)
    assert not case.active.any()


@pytest.mark.parametrize("persisted", [False, True])
def test_unknown_v3_registration_publication_commit_includes_only_actual_full_orphan(case, monkeypatch, persisted):
    pub = partitioned_for(case)
    value = intent(case)
    original = pub.registry.rpc.commit
    def lost(tx, writes):
        if persisted:
            original(tx, writes)
        else:
            pub.registry.rpc.rollback(tx)
        raise AmbiguousCommit("Synthetic V3 native publication acknowledgement loss")
    monkeypatch.setattr(pub.registry.rpc, "commit", lost)
    with pytest.raises(GuardUnavailable, match="unknown"):
        retain(pub, value)
    monkeypatch.setattr(pub.registry.rpc, "commit", original)
    closed = pub.close()
    assert closed is not None
    cut, records = pub.seal_native(closed)
    assert cut.records_count == int(persisted)
    assert (value.operation_id in {r.reference.operation_id for r in records}) is persisted


def test_same_uid_only_mutable_gate_rollback_cannot_omit_surviving_full_native_record(case):
    pub = partitioned_for(case)
    value = intent(case)
    before = pub.registry.read_many(tuple(("v3_lanes", str(i)) for i in range(2))
        + tuple(("v3_directory", str(i)) for i in range(2)))
    retain(pub, value)
    def restore(tx):
        for (namespace, key), state in before.items():
            assert state is not None
            tx.put(namespace, key, state)
    pub.registry.run(restore)
    closed = pub.close()
    assert closed is not None
    with pytest.raises(GuardUnavailable, match="cardinality|registration|history"):
        pub.seal_native(closed)
    assert pub.registry.read("v3_records", str(value.operation_id)) is not None


def test_v3_complete_native_cut_refuses_census_overflow(case):
    pub = partitioned_for(case)
    for _ in range(20):
        retain(pub, intent(case))
    closed = pub.close()
    assert closed is not None
    with pytest.raises(GuardUnavailable):
        pub.seal_native(closed, max_documents=32)
