"""Read-only loader and stock journal tests using owned synthetic metadata."""

from __future__ import annotations

import copy
import hashlib
from dataclasses import asdict, replace

import pytest
from infra.gcp import schema27_continuity_bridge_plan as bridge
from test_schema26_app_successor import context as context


def load(value):
    raw = bridge.rolling.canonical(value)
    return bridge.load(raw, hashlib.sha256(raw).hexdigest())


@pytest.fixture
def current(context):
    r = bridge.rolling
    prior = context.plan
    database = bridge.scope.ContinuityDatabasePin("a" * 64)
    runtime_name, runtime_version = database.runtime_reference.split(":")
    templates = {pin.service: copy.deepcopy(context.boundary.service(pin.service)) for pin in prior.services}
    pins = []
    for pin in prior.services:
        template = templates[pin.service]["template"]
        r.environment(template)["DATABASE_URL"]["valueSource"]["secretKeyRef"] = {
            "secret": runtime_name, "version": runtime_version,
        }
        secrets = tuple(replace(s, secret=runtime_name, version=runtime_version)
                        if s.environment_name == "DATABASE_URL" else s for s in pin.secrets)
        pins.append(replace(pin, secrets=secrets, template_sha256=r.template_sha(template, pin.service),
                            candidate_template_sha256=None))
    plan = bridge.ContinuityBridgePlan(
        source=bridge.successor.SourcePin(prior.release, prior.image_digest, prior.build_id, prior.ci_run_id),
        prior_source=prior.prior_source,
        controller_release=prior.controller_release,
        controller_source_sha256=prior.controller_source_sha256,
        controller_ci_run_id=prior.controller_ci_run_id,
        nonce=prior.nonce,
        database=database,
        runtime_uid=prior.runtime_uid,
        workers_release=prior.workers_release,
        financial_sha256=prior.financial_sha256,
        policies=prior.policies,
        services=tuple(pins),
        resources=prior.resources,
        protected_liabilities=prior.protected_liabilities,
        reachable_revisions=prior.reachable_revisions,
    )
    return plan, templates


def test_continuity_plan_round_trip_and_stock_traffic_builder(current, tmp_path):
    plan, templates = current
    loaded = load(asdict(plan))
    assert loaded == plan
    assert "observer_secret" not in asdict(loaded)
    assert "retired_migration_secret" not in asdict(loaded)
    for pin in loaded.services:
        request = bridge.rolling.stage_body(loaded, pin, templates[pin.service])
        template = request["template"]
        ref = bridge.rolling.environment(template)["DATABASE_URL"]["valueSource"]["secretKeyRef"]
        assert f'{ref["secret"]}:{ref["version"]}' == loaded.database.runtime_reference
        assert template["revision"] == loaded.revision(pin.service)
    directory = tmp_path / "continuity-journal"
    directory.mkdir(mode=0o700)
    custody = loaded.custody()
    journal = bridge.rolling.Journal(directory, custody)
    journal.record(bridge.scope.JOURNAL_BINDING, custody.journal_binding())
    assert bridge.bind(loaded, directory).plan_hash == journal.plan_hash


def test_plan_swaps_refused_before_observer_or_patch(current, context, tmp_path, monkeypatch):
    plan, _ = current

    def unreachable(*_args, **_kwargs):
        pytest.fail("unverified plan reached credential or mutation code")

    monkeypatch.setattr(bridge.scope, "_observer_password", unreachable)
    monkeypatch.setattr(context.boundary, "patch", unreachable)
    with pytest.raises(bridge.rolling.RollingDenied, match="plan_kind"):
        load(asdict(context.plan))
    with pytest.raises(bridge.rolling.RollingDenied, match="plan_kind"):
        load(asdict(plan) | {"observer_secret": context.plan.observer_secret})
    old_observer = replace(plan.database, observer_source=context.plan.observer_secret)
    with pytest.raises(bridge.rolling.RollingDenied, match="database_pin_changed"):
        load(asdict(replace(plan, database=old_observer)))
    old_policy_reference = replace(plan.policies, expense_secret="hirewiz-fresh-observer-db-20261013:2")
    with pytest.raises(bridge.rolling.RollingDenied, match="policy_pins"):
        load(asdict(replace(plan, candidate_policies=old_policy_reference)))

    current_dir = tmp_path / "verified"
    current_dir.mkdir(mode=0o700)
    custody = plan.custody()
    retained = bridge.rolling.Journal(current_dir, custody)
    retained.record(bridge.scope.JOURNAL_BINDING, custody.journal_binding())
    altered_metadata = replace(plan, financial_sha256="b" * 64)
    altered_database = replace(plan, database=replace(plan.database, observer_commitment_sha256="c" * 64))
    for foreign in (altered_metadata, altered_database):
        with pytest.raises(bridge.rolling.RollingDenied):
            bridge.bind(foreign, current_dir)
    context.journal.record(bridge.scope.JOURNAL_BINDING, custody.journal_binding())
    with pytest.raises(bridge.rolling.RollingDenied):
        bridge.bind(plan, context.journal.directory)
    with pytest.raises(bridge.rolling.RollingDenied, match="private_plan_pin"):
        bridge.load(bridge.rolling.canonical(asdict(plan)), "0" * 64)
