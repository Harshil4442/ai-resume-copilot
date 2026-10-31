"""Pure constructor only; no SDK/SQL/native observation."""

from types import SimpleNamespace

from test_schema26_maintenance_guard import m, resource


def test_actual_admin_graph_constructor_has_two_bounded_registries_and_no_orm_bootstrap():
    import sys

    from app.domains.recovery.gcp_contracts import WitnessBody, WitnessPin

    before = set(sys.modules)
    resource_pin = resource()
    body = WitnessBody(
        registry=resource_pin.pin.authority.target,
        state="OPEN",
        manifest_generation="1",
        manifest_sha256="a" * 64,
        partitions_sha256="b" * 64,
    )
    pin = WitnessPin(
        registry=body.registry,
        bucket=resource_pin.bucket,
        path="synthetic/witness",
        generation="1",
        sha256=m.fingerprint(body.model_dump(mode="json")),
    )
    cfg = SimpleNamespace(
        publication_resource=resource_pin,
        execution_witness_pin=pin,
        execution_witness_body=body,
        epoch_generation=1,
    )
    publication, lifetime, _projector = m._native_graph(
        cfg,
        (
            object(),
            object(),
            SimpleNamespace(bucket=lambda name: SimpleNamespace(name=name)),
        ),
    )
    for registry in (publication.registry, lifetime.registry):
        assert registry.max_attempts == 1 and registry.deadline_seconds == 10.0
    assert not (
        {"app.database", "app.models", "backend.app.database", "backend.app.models"}
        & (set(sys.modules) - before)
    )
