"""Owned journal plus native-shaped traffic fixtures, not cloud acceptance."""
from __future__ import annotations

import copy
import importlib
import json
from dataclasses import asdict, fields, replace

import pytest
from test_schema25_app_rolling_release import ROOT
from test_schema25_app_rolling_v2 import V2Fixture, app, rolling

aliases = importlib.import_module("schema25_app_alias_retirement")


class AliasFixture(V2Fixture):
    def __init__(self):
        super().__init__()
        pins, revisions = [], []
        for pin in self.plan.services:
            state = self.states[pin.service]
            old_name = pin.service + "-00000-retained8cb"
            old = copy.deepcopy(self.revisions[(pin.service,pin.prior_revision)])
            old["name"] = rolling.PARENT+"/services/"+pin.service+"/revisions/"+old_name
            old["uid"] = "12345678-1234-1234-1234-123456789abc"
            old["labels"]["hirewiz-source"] = app.PRIOR.release
            old["containers"][0]["image"] = self.plan.image.split("@")[0]+"@"+app.PRIOR.image_digest
            rolling.environment(old)["APP_RELEASE"]["value"] = app.PRIOR.release
            self.revisions[(pin.service,old_name)] = old
            state["trafficStatuses"] += [
                {"revision":pin.prior_revision,"percent":0,"tag":"retained-ab2"},
                {"revision":old_name,"percent":0,"tag":"retained-8cb"}]
            state["traffic"] = rolling.pinned_traffic(pin.service,state)
            pins.append(replace(pin,traffic=tuple(state["traffic"])))
            for name in sorted({r["revision"] for r in state["traffic"]}):
                revisions.append(aliases.revision_pin(self,pin.service,name,self.revisions[(pin.service,pin.prior_revision)]))
        base={f.name:getattr(self.plan,f.name) for f in fields(rolling.RollingPlan)}
        base.update(release=app.PRIOR_V2.release,image_digest=app.PRIOR_V2.image_digest,build_id=app.PRIOR_V2.build_id,
                    ci_run_id=app.PRIOR_V2.ci_run_id,services=tuple(pins))
        self.plan=aliases.AliasPlan(**base,operator_release="c"*40,operator_ci_run_id=1,operator_sha256="a"*64,
            resources=self.plan.resources,protected_liabilities=(),reachable_revisions=tuple(revisions))
        self.plan.validate()

    def verify_aliases(self,plan):
        plan.validate()
        if self.finance_drift:
            raise rolling.RollingDenied("synthetic-alias-financial-drift")
        for pin in plan.services:
            aliases.phase(plan,pin,self.service(pin.service))
            app.observed_flags(self.states[pin.service]["template"],pin.service)
            app.v2_flags(self.states[pin.service]["template"])
            rows=tuple(aliases.revision_pin(self,pin.service,r.revision,self.revision(pin.service,pin.prior_revision))
                for r in plan.reachable_revisions if r.service==pin.service)
            assert rows==tuple(r for r in plan.reachable_revisions if r.service==pin.service)


@pytest.fixture
def context(tmp_path):
    tmp_path.chmod(0o700)
    boundary=AliasFixture()
    return boundary,rolling.Journal(tmp_path,boundary.plan)


def test_only_zero_percent8cb_tags_retired_and_old_source_work_retained(context):
    boundary,journal=context
    original=copy.deepcopy(boundary.revisions)
    assert aliases.load(rolling.canonical(asdict(boundary.plan)))==boundary.plan
    result=aliases.execute(boundary.plan,boundary,journal)
    assert result["status"]=="alias_retirement_observed" and result["native_mutations"]==3
    assert boundary.revisions==original
    for service,body,mask in boundary.writes:
        assert mask=="traffic" and set(body)=={"name","etag","traffic"}
        assert all(r.get("tag")!="retained-8cb" for r in body["traffic"])
        assert any(r.get("tag")=="retained-ab2" for r in body["traffic"])
        assert [r for r in body["traffic"] if r.get("percent")]==list(boundary.plan.pin(service).traffic)[:1]
    assert aliases.execute(boundary.plan,boundary,journal)["native_mutations"]==0


def test_lost_ack_never_sends_again_and_readonly_reconcile_cannot_start_next(context):
    boundary,journal=context
    boundary.lose_ack=True
    with pytest.raises(RuntimeError):
        aliases.execute(boundary.plan,boundary,journal)
    assert len(boundary.writes)==1
    with pytest.raises(rolling.RollingDenied,match="reconcile_only"):
        aliases.execute(boundary.plan,boundary,journal)
    read=aliases.execute(boundary.plan,boundary,journal,reconcile=True)
    assert read["status"]=="existing_alias_actions_reconciled_only" and len(boundary.writes)==1
    assert read["prior_app_plan_historical"] is True
    assert aliases.execute(boundary.plan,boundary,journal)["native_mutations"]==2


def test_pending_exact_operation_observed_without_repeated_send(context):
    boundary,journal=context
    boundary.pause=True
    assert aliases.execute(boundary.plan,boundary,journal)["status"]=="alias_retirement_pending"
    assert len(boundary.writes)==1
    assert aliases.execute(boundary.plan,boundary,journal,reconcile=True)["status"]=="alias_retirement_pending"
    assert len(boundary.writes)==1
    next(iter(boundary.operations.values()))["done"]=True
    aliases.execute(boundary.plan,boundary,journal,reconcile=True)
    assert len(boundary.writes)==1


@pytest.mark.parametrize("change",["positive_old_traffic","template","generation","uid","financial","stage5"])
def test_unreviewed_source_config_or_funding_never_permits_a_traffic_write(context,change):
    boundary,journal=context
    pin=boundary.plan.services[0]
    state=boundary.states[pin.service]
    if change=="positive_old_traffic":
        state["trafficStatuses"][-1]["percent"]=1
        state["trafficStatuses"][0]["percent"]=99
    elif change=="template":
        state["template"]["containers"][0]["resources"]["limits"]["cpu"]="2"
    elif change=="generation":
        state["generation"]=state["observedGeneration"]="43"
    elif change=="uid":
        state["uid"]="11111111-1111-4111-8111-111111111111"
    elif change=="financial":
        boundary.finance_drift=True
    else:
        state["template"]["containers"][0]["env"].append({"name":"CANDIDATE_METADATA_ADMISSION_ENABLED","value":"true"})
    with pytest.raises(rolling.RollingDenied):
        aliases.execute(boundary.plan,boundary,journal)
    assert not boundary.writes


def test_different_plan_never_adopts_existing_journal(context):
    boundary,journal=context
    other=replace(boundary.plan,nonce="d"*32)
    with pytest.raises(rolling.RollingDenied,match="journal_changed"):
        aliases.execute(other,boundary,journal)


def test_native_revision_identity_cannot_be_an_unstructured_hex_string(context):
    boundary, journal = context
    row = boundary.plan.reachable_revisions[0]
    malformed = replace(row, uid="a" * 36)
    with pytest.raises(rolling.RollingDenied, match="reachable_source_invalid"):
        replace(boundary.plan, reachable_revisions=(malformed, *boundary.plan.reachable_revisions[1:])).validate()
    assert not boundary.writes and not list(journal.directory.glob("*.started.json"))


@pytest.mark.parametrize("change", ["none", "duplicate", "skipped", "source", "dirty"])
def test_real_alias_adapter_requires_exact_operator_source_and_both_qualification_steps(context, monkeypatch, change):
    boundary, _ = context
    adapter = aliases.NativeAliases(ROOT)
    plan = replace(boundary.plan, operator_sha256=aliases.code_sha(ROOT))
    steps = [{"name": name, "conclusion": "success", "status": "completed"}
             for name in (app.contract_v2.COMPATIBILITY_STEP, app.contract_v2.PROTOCOL_STEP)]
    if change == "duplicate":
        steps[1]["name"] = steps[0]["name"]
    elif change == "skipped":
        steps[1]["conclusion"] = "skipped"
    run = {"headSha": plan.operator_release, "status": "completed", "conclusion": "success", "jobs": [
        {"name": name, "conclusion": "success", "steps": steps if name == "backend" else []}
        for name in rolling.release.CI_JOBS]}
    if change == "source":
        run["headSha"] = "e" * 40
    monkeypatch.setattr(adapter.observer, "_command", lambda *args: json.dumps(run).encode())
    def git(args, **kwargs):
        return (plan.operator_release + "\n") if args[1] == "rev-parse" else (b" M source\n" if change == "dirty" else b"")
    monkeypatch.setattr(aliases.subprocess, "check_output", git)
    if change == "none":
        adapter._operator(plan)
    else:
        with pytest.raises(rolling.RollingDenied, match="operator_"):
            adapter._operator(plan)
    assert not boundary.writes

