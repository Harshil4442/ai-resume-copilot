"""Local protocol proof only; native source, PostgreSQL and Cloud Run still gate release."""

from __future__ import annotations

import copy
import importlib
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from backend.tests.test_rolling_release import FixtureBoundary

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/gcp"))
hotfix = importlib.import_module("document_api_admission_hotfix")
rolling = hotfix.rolling


class Observer:
    def __init__(self, uid):
        self.uid = uid
        self.source_calls = []
        self.document_calls = []

    def verify_source_image(self, **kwargs):
        self.source_calls.append(kwargs)

    def verify_policy_configuration(self, _policies):
        pass

    def verify_document_configuration(self, source):
        self.document_calls.append(source.release)

    def _cloud(self, *args):
        assert args == ("iam", "service-accounts", "describe", rolling.RUNTIME)
        return {"uniqueId": self.uid}

    def _secret(self, reference):
        assert reference in {"synthetic-expense:1", "synthetic-model-policy:1"}
        return "synthetic-policy"


class Boundary(FixtureBoundary):
    def __init__(self):
        super().__init__()
        original = self.plan
        self.observer = Observer(original.runtime_uid)
        images = tuple(
            self.revision(service, original.pin(service).prior_revision)["containers"][0]["image"]
            for service in rolling.release.SERVICES
        )
        self.plan = hotfix.HotfixPlan(
            original.release, original.image_digest, original.build_id, original.ci_run_id,
            original.nonce, original.runtime_uid, original.runtime_secret,
            original.observer_secret, original.retired_migration_secret,
            original.workers_release, original.financial_sha256,
            original.policies, original.services, prior_images=images,
            api_target_template_sha256="0" * 64,
        )
        template = hotfix._candidate_template(self.plan, self.service(hotfix.API))
        self.plan = replace(
            self.plan, api_target_template_sha256=rolling.template_sha(template, hotfix.API)
        )
        self.plan.validate()

    def _reference(self, _secret, version):
        return version

    def _identity(self, uid):
        assert uid == self.plan.runtime_uid

    def _target(self, runtime, observer, retired):
        assert (runtime, observer, retired) == (
            self.plan.runtime_secret, self.plan.observer_secret,
            self.plan.retired_migration_secret,
        )
        return "0" * 64 if self.finance_drift else self.plan.financial_sha256

    def verify_global(self, plan):
        hotfix.NativeHotfixBoundary.verify_global(self, plan)


@pytest.fixture
def owned(tmp_path):
    tmp_path.chmod(0o700)
    boundary = Boundary()
    return boundary, rolling.Journal(tmp_path, boundary.plan)


def test_capture_requires_exact_source_and_pins_all_three_unchanged_images(owned):
    boundary, _journal = owned
    plan = boundary.plan
    captured = hotfix.NativeHotfixBoundary.capture_hotfix(
        boundary, release=plan.release, image_digest=plan.image_digest,
        build_id=plan.build_id, ci_run_id=plan.ci_run_id, nonce=plan.nonce,
        observer_secret=plan.observer_secret,
        retired_migration_secret=plan.retired_migration_secret,
        workers_release=plan.workers_release,
    )
    assert captured.prior_images == plan.prior_images
    assert captured.api_target_template_sha256 == plan.api_target_template_sha256
    assert boundary.observer.source_calls == [
        dict(release=plan.release, image_digest=plan.image_digest,
             build_id=plan.build_id, ci_run_id=plan.ci_run_id),
        dict(release=plan.release, image_digest=plan.image_digest,
             build_id=plan.build_id, ci_run_id=plan.ci_run_id),
    ]
    assert boundary.writes == []


def test_api_only_stage_then_promote_preserves_workers_and_every_other_api_env(owned):
    boundary, journal = owned
    original = copy.deepcopy(boundary.states)
    staged = hotfix.stage(boundary.plan, boundary, journal)
    assert staged["status"] == "api_hotfix_staged_zero_traffic"
    assert len(boundary.writes) == 1
    result = hotfix.promote(boundary.plan, boundary, journal)
    assert result["complete"] is True
    assert [name for name, _, _ in boundary.writes] == [hotfix.API, hotfix.API]
    assert [mask for _, _, mask in boundary.writes] == ["template,traffic", "traffic"]
    assert all(boundary.states[name] == original[name] for name in hotfix.WORKERS)
    stage = boundary.writes[0][1]
    env_before = rolling.environment(original[hotfix.API]["template"])
    env_after = rolling.environment(stage["template"])
    for name, row in env_before.items():
        if name != "APP_RELEASE":
            assert env_after[name] == row
    assert env_after["APP_RELEASE"]["value"] == boundary.plan.release
    assert env_after["RESUME_DIRECT_UPLOAD_ENABLED"]["value"] == "false"
    assert stage["template"]["containers"][0]["image"] == boundary.plan.image
    assert [row for row in stage["traffic"] if row.get("percent")] == list(boundary.plan.pin(hotfix.API).traffic)
    assert [row for row in boundary.writes[1][1]["traffic"] if row.get("percent")][0]["revision"] == boundary.plan.revision(hotfix.API)
    assert boundary.observer.source_calls


def test_pending_lro_reuses_same_operation_then_promotes_once(owned):
    boundary, journal = owned
    boundary.pause = True
    result = hotfix.stage(boundary.plan, boundary, journal)
    assert result["phase"] == "stage" and len(boundary.writes) == 1
    assert hotfix.stage(boundary.plan, boundary, journal) == result
    assert len(boundary.writes) == 1
    for operation in boundary.operations.values():
        operation["done"] = True
    boundary.pause = False
    assert hotfix.stage(boundary.plan, boundary, journal)["status"] == "api_hotfix_staged_zero_traffic"
    assert len(boundary.writes) == 1
    assert hotfix.promote(boundary.plan, boundary, journal)["complete"] is True
    assert len(boundary.writes) == 2


def test_unknown_ack_reconciles_observed_stage_without_replay(owned):
    boundary, journal = owned
    boundary.lose_ack = True
    with pytest.raises(RuntimeError, match="synthetic-sensitive-provider-error"):
        hotfix.stage(boundary.plan, boundary, journal)
    assert len(boundary.writes) == 1
    with pytest.raises(rolling.RollingDenied, match="unknown_mutation"):
        hotfix.stage(boundary.plan, boundary, journal)
    assert len(boundary.writes) == 1
    assert hotfix.reconcile(boundary.plan, boundary, journal)["native_mutations"] == 0
    assert len(boundary.writes) == 1
    assert hotfix.stage(boundary.plan, boundary, journal)["status"] == "api_hotfix_staged_zero_traffic"
    assert hotfix.promote(boundary.plan, boundary, journal)["complete"] is True
    assert len(boundary.writes) == 2
    assert all("synthetic-sensitive-provider-error" not in path.read_text()
               for path in journal.directory.glob("*.json"))


def test_failed_operation_or_worker_drift_stops_before_next_patch(owned):
    boundary, journal = owned
    boundary.states[hotfix.WORKERS[0]]["template"]["containers"][0]["image"] = "different"
    with pytest.raises(rolling.RollingDenied, match="worker_changed"):
        hotfix.stage(boundary.plan, boundary, journal)
    assert boundary.writes == []
    boundary.states[hotfix.WORKERS[0]]["template"]["containers"][0]["image"] = boundary.plan.prior_images[1]
    boundary.pause = True
    hotfix.stage(boundary.plan, boundary, journal)
    next(iter(boundary.operations.values())).update(done=True, error={"native_failure": True})
    with pytest.raises(rolling.RollingDenied, match="operation_failed"):
        hotfix.stage(boundary.plan, boundary, journal)
    assert len(boundary.writes) == 1


def test_worker_drift_after_zero_traffic_stage_blocks_promotion(owned):
    boundary, journal = owned
    hotfix.stage(boundary.plan, boundary, journal)
    boundary.states[hotfix.WORKERS[1]]["template"]["containers"][0]["image"] = "different"
    with pytest.raises(rolling.RollingDenied, match="worker_changed"):
        hotfix.promote(boundary.plan, boundary, journal)
    assert len(boundary.writes) == 1


def test_unknown_promotion_ack_reconciles_without_second_traffic_patch(owned):
    boundary, journal = owned
    hotfix.stage(boundary.plan, boundary, journal)
    boundary.lose_ack = True
    with pytest.raises(RuntimeError, match="synthetic-sensitive-provider-error"):
        hotfix.promote(boundary.plan, boundary, journal)
    assert len(boundary.writes) == 2
    with pytest.raises(rolling.RollingDenied, match="unknown_mutation"):
        hotfix.promote(boundary.plan, boundary, journal)
    assert hotfix.reconcile(boundary.plan, boundary, journal)["native_mutations"] == 0
    assert hotfix.promote(boundary.plan, boundary, journal)["complete"] is True
    assert len(boundary.writes) == 2


def test_rollback_is_one_traffic_patch_and_never_repromotes(owned):
    boundary, journal = owned
    hotfix.stage(boundary.plan, boundary, journal)
    hotfix.promote(boundary.plan, boundary, journal)
    assert hotfix.rollback(boundary.plan, boundary, journal)["status"] == "prior_api_traffic_restored_schema21"
    assert len(boundary.writes) == 3
    assert boundary.writes[-1][0] == hotfix.API
    assert boundary.writes[-1][2] == "traffic" and "template" not in boundary.writes[-1][1]
    assert [row for row in boundary.states[hotfix.API]["traffic"] if row.get("percent")][0]["revision"] == boundary.plan.pin(hotfix.API).prior_revision
    with pytest.raises(rolling.RollingDenied, match="rollback_in_progress"):
        hotfix.promote(boundary.plan, boundary, journal)


def test_plan_roundtrip_and_foreign_plan_refusal(owned):
    boundary, _journal = owned
    raw = rolling.canonical(asdict(boundary.plan))
    assert hotfix.load_plan(raw) == boundary.plan
    with pytest.raises(rolling.RollingDenied, match="distinct_plan"):
        hotfix.load_plan(rolling.canonical(asdict(FixtureBoundary().plan)))
    with pytest.raises(rolling.RollingDenied, match="plan_invalid"):
        hotfix.load_plan(rolling.canonical({**asdict(boundary.plan), "prior_images": ["tag:mutable"] * 3}))


def test_candidate_flag_is_closed_even_when_prior_flag_is_enabled(owned):
    boundary, _journal = owned
    current = boundary.states[hotfix.API]
    current["template"]["containers"][0]["env"].append({"name": "RESUME_DIRECT_UPLOAD_ENABLED", "value": "true"})
    pin = boundary.plan.pin(hotfix.API)
    changed = replace(pin, template_sha256=rolling.template_sha(current["template"], hotfix.API))
    services = (changed, *boundary.plan.services[1:])
    plan = replace(boundary.plan, services=services)
    template = hotfix._candidate_template(plan, current)
    assert rolling.environment(template)["RESUME_DIRECT_UPLOAD_ENABLED"]["value"] == "false"
