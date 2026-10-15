"""Actual current Native RunQuery pagination, explicit bounds and flat orphan census."""

from __future__ import annotations

from uuid import uuid4

import pytest
from test_gcp_pairing_emulator import case as case

from app.domains.recovery.gcp_publication_scan import NativeAuthorityScanner
from app.domains.recovery.store import GuardUnavailable


def populated(c, count=75):
    registry = c.publication.registry
    keys = tuple(str(uuid4()) for _ in range(count))
    for start in range(0, len(keys), 32):
        def add(tx, batch=keys[start:start + 32]):
            for key in batch:
                tx.put("v3_full_records", key, {"complete": key}, immutable=True)
        registry.run(add)
    return keys


def test_actual_native_flat_census_paginates_beyond_old_global_bound(case, monkeypatch):
    keys = populated(case)
    rpc = case.publication.registry.rpc
    original, requests = rpc.client.run_query, []
    def observe(**kwargs):
        request = kwargs["request"]
        requests.append(request)
        assert kwargs["retry"] is None and kwargs["timeout"] == 5.0
        assert "read_time" not in request and "transaction" not in request
        assert "where" not in request["structured_query"]
        return original(**kwargs)
    monkeypatch.setattr(rpc.client, "run_query", observe)
    found = NativeAuthorityScanner(rpc).scan(page_size=8, max_documents=128, max_pages=32)
    records = [v.envelope for v in found if v.envelope["namespace"] == "v3_full_records"]
    assert {r["key"] for r in records} == set(keys)
    assert len(found) == 76 and len(requests) == 10
    assert all(r["structured_query"]["start_at"]["before"] is False for r in requests[1:])
    assert not case.active.any()


@pytest.mark.parametrize("bound", ["documents", "pages"])
def test_actual_native_census_refuses_bound_exhaustion_instead_of_partial_complete(case, bound):
    populated(case)
    with pytest.raises(GuardUnavailable):
        NativeAuthorityScanner(case.publication.registry.rpc).scan(page_size=8,
            max_documents=64 if bound == "documents" else 128,
            max_pages=8 if bound == "pages" else 32)


def test_full_row_surviving_an_index_rollback_remains_visible_to_actual_flat_census(case):
    registry = case.publication.registry
    key = str(uuid4())
    registry.run(lambda tx: tx.put("v3_full_records", key, {"complete": key}, immutable=True))
    # The negative independent probe regressed only its mutable cut index. This
    # actual flat query does not filter by that index, so the full orphan survives.
    registry.run(lambda tx: tx._stage("control", "publication", {"entries": []}, immutable=False))
    found = NativeAuthorityScanner(registry.rpc).scan(page_size=1, max_documents=8, max_pages=8)
    assert any(row.envelope["namespace"] == "v3_full_records" and row.envelope["key"] == key for row in found)
