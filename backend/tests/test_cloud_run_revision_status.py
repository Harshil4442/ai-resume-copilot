"""Synthetic Cloud Run readbacks for retained reachable template pins."""

import pytest
from test_schema26_app_successor import context as context
from test_schema26_app_successor import no_network as no_network
from test_schema26_app_successor import r


def test_successor_reachable_pin_ignores_only_known_container_status_output(context):
    plan = context.plan
    for pin in plan.services:
        revision = context.fixture.revisions[(pin.service, pin.prior_revision)]
        revision["containerStatuses"] = [{"name": "hirewiz", "imageDigest": "sha256:" + "1" * 64}]

    assert context.boundary._reachable(plan.services, plan.prior_source) == plan.reachable_revisions
    context.boundary.verify_global(plan)

    pin = plan.services[0]
    context.fixture.revisions[(pin.service, pin.prior_revision)]["unreviewedRevisionSetting"] = True
    with pytest.raises(r.RollingDenied, match="reachable_prior_changed"):
        context.boundary.verify_global(plan)
