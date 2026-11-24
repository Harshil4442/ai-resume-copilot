"""Restore-only safety over owned ports; no GCP, credentials, or SQL calls."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, replace
from types import SimpleNamespace
from uuid import UUID

import pytest
from infra.gcp import schema27_staged_restore as op
from test_schema27_mixed_app_successor import application as application
from test_schema27_mixed_app_successor import context as context
from test_schema27_mixed_app_successor import controlled as controlled
from test_schema27_mixed_app_successor import current as current
from test_schema27_mixed_app_successor import handoff as handoff
from test_schema27_mixed_app_successor import lane as lane
from test_schema27_mixed_app_successor import mixed_app as mixed_app
from test_schema27_mixed_app_successor import no_network as no_network
from test_schema27_mixed_app_successor import owned_app_roots as owned_app_roots
from test_schema27_mixed_app_successor import profile as profile


@pytest.fixture
def staged(mixed_app, monkeypatch, tmp_path):
    c = mixed_app
    # Synthetic exact-interruption pins, never copies of production evidence.
    monkeypatch.setattr(op, "SOURCE70", c.plan.prior_source.release)
    monkeypatch.setattr(op, "SOURCE3F7", c.plan.release)
    monkeypatch.setattr(op, "GENERATIONS", {pin.service: pin.generation + 1 for pin in c.plan.services})
    op.mixed.execute("stage", c.plan, c.boundary, c.journal)
    assert len(c.fixture.writes) == 3
    for index, value in enumerate(c.fixture.revisions.values(), 1):
        value["uid"] = str(UUID(int=index))
    monkeypatch.setattr(op, "STAGED_UIDS", {pin.service: c.fixture.revisions[(pin.service,
        c.plan.revision(pin.service))]["uid"] for pin in c.plan.services})
    c.fixture.writes.clear()
    c.boundary.identity = lambda email: copy.deepcopy(c.identities[email])
    c.boundary.verify_sources = lambda app: op.need(app == c.plan, "synthetic_source_changed")
    original_patch = c.boundary.patch

    def patch(service, body, mask):
        result = original_patch(service, body, mask)
        if "template" in body:
            name = body["template"]["revision"]
            c.fixture.revisions[(service, name)]["uid"] = str(UUID(int=100 + len(c.fixture.writes)))
        return result

    c.boundary.patch = patch
    boundary = object.__new__(op.NativeBoundary)
    for name in ("service", "revision", "patch", "operation", "iam", "identity", "health", "verify_sources"):
        setattr(boundary, name, getattr(c.boundary, name))
    raw = op.rolling.canonical(asdict(c.plan))
    original = tmp_path / "original-plan.json"
    original.write_bytes(raw)
    original.chmod(0o600)
    digest = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(op, "B_FILE_SHA256", digest)
    stamp = {"release": "9" * 40, "tree": "8" * 40,
             "files": {"infra/gcp/schema27_staged_restore.py": "7" * 64}}
    monkeypatch.setattr(op, "source_state", lambda: copy.deepcopy(stamp))
    directory = tmp_path / "restore"
    directory.mkdir(mode=0o700)
    return SimpleNamespace(original=original, original_sha256=digest, c=c, stamp=stamp,
                           directory=directory, boundary=boundary)


def capture(c):
    return op.capture(c.original, c.original_sha256, c.boundary,
                      nonce="d" * 32, journal_directory=c.directory)


@pytest.fixture
def prepared(staged):
    plan = capture(staged)
    journal = op.owned.Journal(staged.directory, plan, create=True)
    return staged, plan, journal


def test_capture_and_roundtrip_have_no_literal_values_or_new_native_writes(prepared):
    c, plan, journal = prepared
    encoded = op.rolling.canonical(asdict(plan))
    assert op.load(encoded, hashlib.sha256(encoded).hexdigest()) == plan
    assert not c.c.fixture.writes
    assert b"synthetic-sensitive-literal" not in encoded
    assert "request_sha256" not in json.dumps({key: value for key, value in asdict(plan).items()
                                               if key != "application"})
    assert b"<inline-redacted>" not in encoded  # only a redacted projection hash is stored
    assert journal.plan_hash != c.c.journal.plan_hash
    assert not journal.claimed("restore-stage." + op.SERVICES[0] + ".started")


@pytest.mark.parametrize("field,accepted", [("containerStatuses", True), ("unexpectedRuntimeConfig", False)])
def test_native_output_status_does_not_mask_unknown_template_changes(staged, field, accepted):
    for pin in staged.c.plan.services:
        staged.c.fixture.revisions[(pin.service, staged.c.plan.revision(pin.service))][field] = [{"name": "app"}]
    if not accepted:
        with pytest.raises(op.rolling.RollingDenied, match="staged_revision_changed"):
            capture(staged)
        assert not staged.c.fixture.writes
        return
    original_patch = staged.boundary.patch

    def patch(service, body, mask):
        result = original_patch(service, body, mask)
        if "template" in body:
            staged.c.fixture.revisions[(service, body["template"]["revision"])][field] = [{"name": "app"}]
        return result

    staged.boundary.patch = patch
    plan = capture(staged)
    journal = op.owned.Journal(staged.directory, plan, create=True)
    result = op.execute("restore", plan, staged.boundary, journal)
    assert result["all_homogeneous_source70"] is True and result["native_mutations"] == 6


def test_all_services_restore_source70_only_after_zero_traffic_health(prepared):
    c, plan, journal = prepared
    prior = copy.deepcopy(c.c.fixture.revisions)
    calls = []
    original_patch, original_health = c.boundary.patch, c.boundary.health

    def patch(service, body, mask):
        calls.append(("patch", service, mask))
        return original_patch(service, body, mask)

    def health(*args):
        calls.append(("health", args[1]))
        return original_health(*args)

    c.boundary.patch, c.boundary.health = patch, health
    result = op.execute("restore", plan, c.boundary, journal)
    assert result["all_homogeneous_source70"] is True
    assert result["native_mutations"] == 6
    assert result["forward_release_authorized"] is False
    assert result["acceptance_freshness_authorized"] is False
    for service in op.SERVICES:
        positions = [row[0] for row in calls if row[1] == service]
        assert positions == ["patch", "health", "patch", "health"]
        value = c.c.fixture.states[service]
        assert all(row["revision"] == plan.pin(service).revision for row in value["trafficStatuses"])
        assert op.rolling.environment(value["template"])["APP_RELEASE"]["value"] == op.SOURCE70
        assert value["template"]["serviceAccount"] == plan.application.identities.identity(service).email
        assert int(value["generation"]) == plan.pin(service).before["generation"] + 2
        assert all(op.rolling.environment(value["template"])[flag]["value"] == "false"
                   for flag in plan.application.disabled_flags())
    assert all(c.c.fixture.revisions[key] == value for key, value in prior.items())
    assert all("synthetic-sensitive-literal" not in path.read_text() for path in journal.directory.glob("*.json"))
    before = len(c.c.fixture.writes)
    assert op.execute("observe", plan, c.boundary, journal)["native_mutations"] == 0
    assert op.execute("restore", plan, c.boundary, journal)["native_mutations"] == 0
    assert len(c.c.fixture.writes) == before
    receipt = op.VerifiedRestore(plan, journal)
    assert receipt.application_plan == c.c.plan and receipt.profile == c.c.plan.identities
    assert set(receipt.verify_current(c.boundary)) == set(op.SERVICES)


@pytest.mark.parametrize("fault", ["uid", "generation", "etag", "identity", "traffic", "static", "prior_uid", "staged_uid",
                                  "prior_literal", "current_literal", "source", "iam", "promotion", "missing_checked"])
def test_unowned_or_changed_preimage_refuses_before_native_write(prepared, fault):
    c, plan, journal = prepared
    api = op.SERVICES[0]
    value = c.c.fixture.states[api]
    if fault == "uid":
        value["uid"] = str(UUID(int=777))
    elif fault == "generation":
        value["generation"] = value["observedGeneration"] = "999"
    elif fault == "etag":
        value["etag"] = "unowned"
    elif fault == "identity":
        c.c.identities[plan.application.identities.transition.new_api.email]["uid"] = "9" * 21
    elif fault == "traffic":
        value["trafficStatuses"][0]["revision"] = plan.application.revision(api)
    elif fault == "static":
        value["ingress"] = "INGRESS_TRAFFIC_INTERNAL_ONLY"
    elif fault in {"prior_uid", "staged_uid"}:
        revision = plan.application.pin(api).prior_revision if fault == "prior_uid" else plan.application.revision(api)
        c.c.fixture.revisions[(api, revision)]["uid"] = str(UUID(int=888))
    elif fault in {"prior_literal", "current_literal"}:
        template = value["template"] if fault == "current_literal" else c.c.fixture.revisions[(api,
            plan.application.pin(api).prior_revision)]
        op.rolling.environment(template)["RAZORPAY_KEY_SECRET"]["value"] = "unowned-secret-change"
    elif fault == "source":
        c.stamp["tree"] = "6" * 40
    elif fault == "iam":
        c.c.fixture.policies[api]["etag"] = "unowned"
    elif fault == "promotion":
        c.c.journal.record("promote." + api + ".started", {"synthetic": True})
    elif fault == "missing_checked":
        (c.c.journal.directory / ("stage." + api + ".checked.json")).unlink()
    with pytest.raises(op.rolling.RollingDenied):
        op.execute("restore", plan, c.boundary, journal)
    assert not c.c.fixture.writes


@pytest.mark.parametrize("mode", ["restore", "reconcile", "observe"])
def test_unknown_ack_never_replays_or_manufactures_ack(prepared, mode):
    c, plan, journal = prepared
    c.c.fixture.lose_ack = True
    with pytest.raises(RuntimeError):
        op.execute("restore", plan, c.boundary, journal)
    assert len(c.c.fixture.writes) == 1
    with pytest.raises(op.rolling.RollingDenied, match="missing_native_ack"):
        op.execute(mode, plan, c.boundary, journal)
    assert len(c.c.fixture.writes) == 1
    assert journal.read("restore-stage." + op.SERVICES[0] + ".ack") is None
    assert journal.read("complete") is None


def test_pending_operation_and_reconciliation_do_not_start_route(prepared):
    c, plan, journal = prepared
    c.c.fixture.pause = True
    result = op.execute("restore", plan, c.boundary, journal)
    assert result["status"] == "same_native_operation_pending"
    assert len(c.c.fixture.writes) == 1
    assert op.execute("reconcile", plan, c.boundary, journal)["native_mutations"] == 0
    assert len(c.c.fixture.writes) == 1
    for value in c.c.fixture.operations.values():
        value["done"] = True
    c.c.fixture.pause = False
    assert not op.execute("reconcile", plan, c.boundary, journal)["all_homogeneous_source70"]
    assert journal.read("restore-stage." + op.SERVICES[0] + ".checked")["health_verified"]
    assert len(c.c.fixture.writes) == 1
    assert op.execute("restore", plan, c.boundary, journal)["all_homogeneous_source70"]
    assert len(c.c.fixture.writes) == 6


def test_failed_zero_traffic_health_preserves_old_source70_allocation(prepared):
    c, plan, journal = prepared
    before = copy.deepcopy(c.c.fixture.states[op.SERVICES[0]]["trafficStatuses"])

    def health(*_):
        raise op.rolling.RollingDenied("synthetic_health_failed")

    c.boundary.health = health
    with pytest.raises(op.rolling.RollingDenied, match="health_failed"):
        op.execute("restore", plan, c.boundary, journal)
    assert len(c.c.fixture.writes) == 1
    after = c.c.fixture.states[op.SERVICES[0]]["trafficStatuses"]
    assert [row for row in after if row.get("percent")] == [row for row in before if row.get("percent")]
    assert journal.read("restore-route." + op.SERVICES[0] + ".started") is None
    assert journal.read("complete") is None


def test_exclusive_claim_without_record_forbids_new_send(prepared):
    c, plan, journal = prepared
    key = "restore-stage." + op.SERVICES[0] + ".started"
    (journal.directory / (key + ".claim")).touch(mode=0o600)
    with pytest.raises(op.rolling.RollingDenied, match="incomplete_intent"):
        op.execute("restore", plan, c.boundary, journal)
    assert not c.c.fixture.writes


def test_verified_loader_rejects_incomplete_proof_and_current_drift(prepared, tmp_path):
    c, plan, journal = prepared
    path = tmp_path / "restore-plan.json"
    raw = op.rolling.canonical(asdict(plan))
    path.write_bytes(raw)
    path.chmod(0o600)
    args = {"plan_path": path, "plan_sha256": hashlib.sha256(raw).hexdigest(),
            "journal_path": journal.directory, "profile": plan.application.identities}
    with pytest.raises(op.rolling.RollingDenied, match="restore_incomplete"):
        op.load_verified_restore(**args)
    op.execute("restore", plan, c.boundary, journal)
    proof = op.load_verified_restore(**args)
    assert proof.verify_current(c.boundary)
    c.c.fixture.states[op.SERVICES[0]]["etag"] = "maintenance-later-state"
    with pytest.raises(op.rolling.RollingDenied, match="checked_state_changed"):
        proof.verify_current(c.boundary)
    # Later maintenance may still authenticate historical receipt custody.
    assert proof.verify_custody()["all_homogeneous_source70"]


def test_no_new_spend_or_sql_or_credential_authority_is_exposed(prepared):
    c, plan, journal = prepared
    with pytest.raises(op.rolling.RollingDenied, match="exact_owner"):
        op.execute("advance", plan, c.boundary, journal)
    with pytest.raises(op.rolling.RollingDenied):
        replace(plan, original_plan_file_sha256="0" * 64).validate()
    assert not hasattr(op.NativeBoundary, "_database")
    assert not hasattr(op.NativeBoundary, "update_secret")
    assert "provider_writes" not in json.dumps(asdict(plan))


@pytest.mark.parametrize("fail_second", [False, True])
def test_native_source_pair_reused_only_after_both_proofs_succeed(prepared, monkeypatch, fail_second):
    _, plan, _ = prepared
    app = plan.application
    calls, failure = [], {"active": fail_second}
    boundary = object.__new__(op.NativeBoundary)
    boundary._verified_source_pair = None

    def ci(observer, release, run_id):
        assert observer is boundary.observer
        calls.append(("ci", release, run_id))

    def provenance(**values):
        calls.append(("provenance", values["release"], values["build_id"]))
        if failure["active"] and values["release"] == app.source.release:
            raise op.rolling.RollingDenied("synthetic_provenance_failure")

    boundary.observer = SimpleNamespace(_verify_source_image_provenance=provenance)
    monkeypatch.setattr(op.mixed.app.successor, "_native_ci", ci)
    if fail_second:
        with pytest.raises(op.rolling.RollingDenied, match="provenance_failure"):
            boundary.verify_sources(app)
        assert boundary._verified_source_pair is None
        assert len(calls) == 4
        failure["active"] = False
    boundary.verify_sources(app)
    expected = 8 if fail_second else 4
    assert len(calls) == expected
    assert boundary._verified_source_pair == (app.prior_source, app.source)
    boundary.verify_sources(app)
    assert len(calls) == expected
    changed = replace(app.application, source=replace(app.source, image_digest="sha256:" + "0" * 64))
    with pytest.raises(op.rolling.RollingDenied, match="source_pair_changed"):
        boundary.verify_sources(replace(app, application=changed))
    assert len(calls) == expected
    assert boundary._verified_source_pair == (app.prior_source, app.source)
