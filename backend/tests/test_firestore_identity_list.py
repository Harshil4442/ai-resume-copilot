"""Installed GAPIC metadata transport and request-local brackets; no cloud IAM proof."""
from __future__ import annotations

from contextvars import copy_context
from unittest.mock import Mock
from uuid import uuid4

import pytest
from google.api_core.exceptions import ServiceUnavailable
from google.cloud.firestore_admin_v1.types import Database, ListDatabasesResponse
from google.protobuf.timestamp_pb2 import Timestamp

from app.domains.recovery.gcp_contracts import RegistryPin
from app.domains.recovery.gcp_rpc import FirestoreIdentityList, FirestoreRpc
from app.domains.recovery.native_identity_observation import (
    identity_observation_scope,
    invalidate_identity_observations,
)
from app.domains.recovery.store import GuardUnavailable
from tests.test_gcp_rpc import DATABASE, UID, sdk  # noqa: F401

SECOND = "projects/local-proof/databases/second-proof"
SECOND_UID = "17538789-d17f-4d02-bdcb-8a04aff617fa"


def response():
    return ListDatabasesResponse(databases=[Database(name=DATABASE, uid=UID, type_=1),
        Database(name=SECOND, uid=SECOND_UID, type_=1),
        Database(name="projects/local-proof/databases/(default)", uid=str(uuid4()), type_=2)])


def setup(sdk):  # noqa: F811
    transport = sdk.admin.transport
    transport.list_databases.side_effect = sdk.endpoint("list_databases")
    sdk.admin.list_databases = Mock(wraps=sdk.admin.list_databases)
    second = FirestoreRpc(sdk.client, sdk.admin, database_resource=SECOND)
    rpcs = (sdk.rpc, second)
    pins = tuple(RegistryPin(database=rpc.database_resource, database_uid=uid,
        authority_id=uuid4(), incarnation=1, epoch_id=uuid4())
        for rpc, uid in zip(rpcs, (UID, SECOND_UID), strict=True))
    return rpcs, tuple(zip(rpcs, pins, strict=True)), FirestoreIdentityList(rpcs)


def test_installed_sdk_one_complete_project_list_has_no_retry_or_pagination(sdk):  # noqa: F811
    _, _, batch = setup(sdk)
    sdk.queue("list_databases", response())
    values = batch.identities()
    assert len(values) == 2
    assert sdk.admin.list_databases.call_args.kwargs["retry"] is None
    assert sdk.admin.list_databases.call_args.kwargs["timeout"] == 2.0
    calls = sdk.calls["list_databases"]
    assert len(calls) == 1
    request, _ = calls[0]
    assert request.parent == "projects/local-proof" and request.show_deleted is False
    assert not sdk.calls["get_database"]


@pytest.mark.parametrize("changed", ["unreachable", "missing", "duplicate", "foreign", "deleted",
    "datastore", "uuid", "uppercase_uid", "wrong_uid", "oversized", "not_protobuf"])
def test_partial_ambiguous_or_changed_identity_never_enters_dependent_reads(sdk, changed):  # noqa: F811
    _, bindings, batch = setup(sdk)
    value = response()
    if changed == "unreachable":
        value.unreachable.append("projects/local-proof/locations/us-central1")
    elif changed == "missing":
        del value.databases[1]
    elif changed == "duplicate":
        value.databases.append(value.databases[0])
    elif changed == "foreign":
        value.databases[-1].name = "projects/other-proof/databases/other-proof"
    elif changed == "deleted":
        value.databases[0].delete_time = Timestamp(seconds=1)
    elif changed == "datastore":
        value.databases[0].type_ = 2
    elif changed == "uuid":
        value.databases[0].uid = "00000000-0000-1000-8000-000000000001"
    elif changed == "uppercase_uid":
        value.databases[0].uid = UID.upper()
    elif changed == "wrong_uid":
        value.databases[0].uid = str(uuid4())
    elif changed == "oversized":
        value.databases[-1].tags["synthetic"] = "x" * (512 * 1024)
    else:
        value = {"databases": []}
    sdk.queue("list_databases", value)
    with pytest.raises(GuardUnavailable), identity_observation_scope(bindings, batch=batch):
        pytest.fail("Incomplete identity metadata authorized a dependent read")
    assert len(sdk.calls["list_databases"]) == 1
    assert not sdk.calls["get_database"]


def test_list_denial_does_not_retry_or_fall_back_to_get(sdk):  # noqa: F811
    _, _, batch = setup(sdk)
    sdk.queue("list_databases", ServiceUnavailable("synthetic-unavailable"), response())
    with pytest.raises(GuardUnavailable):
        batch.identities()
    assert len(sdk.calls["list_databases"]) == 1
    assert len(sdk.scripts["list_databases"]) == 1
    assert not sdk.calls["get_database"]


def test_each_request_is_fresh_and_exit_list_starts_after_all_dependent_reads(sdk):  # noqa: F811
    rpcs, bindings, batch = setup(sdk)
    for _ in range(2):
        sdk.queue("list_databases", response(), response())
        before = len(sdk.calls["list_databases"])
        with identity_observation_scope(bindings, batch=batch):
            assert len(sdk.calls["list_databases"]) == before + 1
            first = rpcs[0].identity()
            first["uid"] = "an-untrusted-mutable-copy"
            assert rpcs[0].identity()["uid"] == UID
            assert rpcs[1].identity()["uid"] == SECOND_UID
            assert len(sdk.calls["list_databases"]) == before + 1
        assert len(sdk.calls["list_databases"]) == before + 2
    assert not sdk.calls["get_database"]


def test_exit_replacement_denies_success_and_abort_invalidation_refreshes_before_reuse(sdk):  # noqa: F811
    rpcs, bindings, batch = setup(sdk)
    changed = response()
    changed.databases[1].uid = str(uuid4())
    sdk.queue("list_databases", response(), changed)
    with pytest.raises(GuardUnavailable):
        with identity_observation_scope(bindings, batch=batch):
            assert rpcs[1].identity()["uid"] == SECOND_UID
    sdk.queue("list_databases", response(), response(), response())
    with identity_observation_scope(bindings, batch=batch):
        before = len(sdk.calls["list_databases"])
        invalidate_identity_observations()
        assert rpcs[0].identity()["uid"] == UID
        assert len(sdk.calls["list_databases"]) == before + 1
    assert len(sdk.calls["list_databases"]) == 5


def test_copied_context_cannot_borrow_batched_observation_and_poisons_scope(sdk):  # noqa: F811
    rpcs, bindings, batch = setup(sdk)
    sdk.queue("list_databases", response(), response())
    with pytest.raises(GuardUnavailable):
        with identity_observation_scope(bindings, batch=batch):
            with pytest.raises(GuardUnavailable):
                copy_context().run(rpcs[0].identity)
            with pytest.raises(GuardUnavailable):
                rpcs[1].identity()
    assert len(sdk.calls["list_databases"]) == 2


def test_changed_transport_or_resource_cannot_reuse_a_batch(sdk):  # noqa: F811
    rpcs, _, batch = setup(sdk)
    rpcs[1].database_resource = "projects/local-proof/databases/other-proof"
    with pytest.raises(GuardUnavailable):
        batch.identities()
    assert not sdk.calls["list_databases"]


def test_caught_failed_batch_never_revives_prior_identities(sdk):  # noqa: F811
    rpcs, bindings, batch = setup(sdk)
    sdk.queue("list_databases", response(), ServiceUnavailable("synthetic-unavailable"), response())
    with pytest.raises(GuardUnavailable):
        with identity_observation_scope(bindings, batch=batch):
            invalidate_identity_observations()
            with pytest.raises(GuardUnavailable):
                rpcs[0].identity()
            with pytest.raises(GuardUnavailable):
                rpcs[1].identity()
    assert len(sdk.calls["list_databases"]) == 3
    assert not sdk.calls["get_database"]
