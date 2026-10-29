"""Finite v2/source/alias fixtures. Synthetic transport never proves deployment."""
from __future__ import annotations

import copy
import json
import subprocess
from dataclasses import asdict, fields, replace
from types import SimpleNamespace

import pytest
from test_schema25_app_rolling_release import ROOT, AppFixture
from test_schema25_app_rolling_release import app_release as app

from scripts import schema25_app_release_contract_v2 as contract

rolling = app.rolling


class V2Fixture(AppFixture):
    def __init__(self):
        super().__init__()
        pins = []
        for pin in self.plan.services:
            state = self.states[pin.service]
            template = state["template"]
            template["labels"]["hirewiz-source"] = app.PRIOR_V2.release
            template["containers"][0]["image"] = self.plan.image.split("@")[0] + "@" + app.PRIOR_V2.image_digest
            rolling.environment(template)["APP_RELEASE"]["value"] = app.PRIOR_V2.release
            revision = {**copy.deepcopy(template), "name": state["latestReadyRevision"],
                "conditions": [{"type": "Ready", "state": "CONDITION_SUCCEEDED"}], "uid": "a"*8+"-"+"b"*4+"-"+"c"*4+"-"+"d"*4+"-"+"e"*12}
            self.revisions[(pin.service, pin.prior_revision)] = revision
            pins.append(replace(pin, prior_release=app.PRIOR_V2.release,
                template_sha256=rolling.template_sha(template, pin.service),
                revision_sha256=rolling.sha({key: revision.get(key) for key in pin.revision_keys})))
        values = {f.name: getattr(self.plan, f.name) for f in fields(app.App25Plan)
                  if f.name not in {"schema_contract","prior_source","services","reachable_revisions"}}
        self.plan = app.App25V2Plan(**values, services=tuple(pins),
            reachable_revisions=tuple(app.reachable_pin(p.service,p.prior_revision,self.revisions[(p.service,p.prior_revision)],
                prior_source=app.PRIOR_V2) for p in pins))
        self.targets = {p.service: app.candidate_template(self.plan,p,self.service(p.service)) for p in pins}
        self.plan.validate()


@pytest.fixture
def context(tmp_path):
    tmp_path.chmod(0o700)
    boundary = V2Fixture()
    return boundary, rolling.Journal(tmp_path, boundary.plan)


def test_v1_v2_loaders_never_adopt_each_others_plan(context):
    current, _ = context
    raw = rolling.canonical(asdict(current.plan))
    assert app.load_v2(raw) == current.plan
    with pytest.raises(rolling.RollingDenied):
        app.load(raw)
    with pytest.raises(rolling.RollingDenied):
        app.load_v2(rolling.canonical(asdict(AppFixture().plan)))
    with pytest.raises(rolling.RollingDenied):
        replace(current.plan, prior_source=app.PRIOR).validate()


def test_v2_uses_same_one_send_stage_promote_and_prior_health_rollback(context):
    current, journal = context
    assert app.execute("stage",current.plan,current,journal)["status"] == "schema25_app_staged_zero_traffic"
    assert app.execute("advance",current.plan,current,journal)["rolling_release_complete"]
    result = app.execute("rollback",current.plan,current,journal)
    assert result["status"] == "schema25_app_prior_traffic_restored"
    assert journal.read(app.ROLLBACK_INTENT)["prior_release"] == app.PRIOR_V2.release
    assert all(mask in {"template,traffic","traffic"} for _,_,mask in current.writes)


@pytest.mark.parametrize("flag", [*contract.STAGE5_FLAGS, "OPTIONAL_AI_GENERATION_ENABLED","RAZORPAY_CHECKOUT_ENABLED"])
def test_v2_cannot_implicitly_open_old_or_new_flags(context,flag):
    current, journal = context
    pin = current.plan.services[0]
    template = current.states[pin.service]["template"]
    env = rolling.environment(template)
    env[flag] = {"name":flag,"value":"true"}
    template["containers"][0]["env"] = list(env.values())
    with pytest.raises(rolling.RollingDenied):
        app.candidate_template(current.plan,pin,current.service(pin.service))
    assert not current.writes and not list(journal.directory.glob("*.started.json"))


@pytest.mark.parametrize("change", ["required","existing","unknown"])
def test_additive_response_manifest_rejects_unreviewed_schema_change(change):
    prior = app.initial.prior_app_files(ROOT,contract.PRIOR_RELEASE)["backend/app/schemas.py"]
    current = (ROOT/"backend/app/schemas.py").read_bytes()
    contract.additive_schema(prior,current)
    if change=="required":
        current=current.replace(b"evidence_quote: str | None = None",b"evidence_quote: str")
    elif change=="existing":
        current=current.replace(b"match_id:                int",b"match_id:                str")
    else:
        current+=b"\nclass UnreviewedResponse(BaseModel):\n    value: int\n"
    with pytest.raises(Exception,match="schema|additions"):
        contract.additive_schema(prior,current)


def test_old_tag_source_is_refused_by_new_release_capture(context):
    current,_=context
    pin=current.plan.services[0]
    old=copy.deepcopy(current.revisions[(pin.service,pin.prior_revision)])
    rolling.environment(old)["APP_RELEASE"]["value"]=app.PRIOR.release
    old["containers"][0]["image"]=current.plan.image.split("@")[0]+"@"+app.PRIOR.image_digest
    with pytest.raises(rolling.RollingDenied,match="source_or_admission"):
        app.reachable_pin(pin.service,pin.prior_revision,old,prior_source=app.PRIOR_V2)


@pytest.mark.parametrize("flag", contract.STAGE5_FLAGS)
def test_retained_tag_cannot_hide_stage5_activation(context, monkeypatch, flag):
    fixture, _ = context
    adapter = app.NativeApp25BoundaryV2(ROOT)
    pin = fixture.plan.services[0]
    retained_name = pin.service + "-retained-ab2"
    retained = copy.deepcopy(fixture.revision(pin.service, pin.prior_revision))
    retained["name"] = rolling.PARENT + "/services/" + pin.service + "/revisions/" + retained_name
    retained["containers"][0]["env"].append({"name": flag, "value": "true"})
    fixture.revisions[(pin.service, retained_name)] = retained
    changed = replace(pin, traffic=(*pin.traffic, {"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
        "revision": retained_name, "tag": "retained-ab2"}))
    monkeypatch.setattr(adapter, "revision", fixture.revision)
    with pytest.raises(rolling.RollingDenied, match="stage5_activation_outside_release"):
        adapter._reachable((changed, *fixture.plan.services[1:]))
    assert not fixture.writes


@pytest.mark.parametrize("change", ["continue", "optional", "environment", "retained", "history"])
def test_ci_cannot_relabel_optional_or_missing_qualification_as_success(tmp_path, change):
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    contract.verify_ci(ROOT)
    steps = workflow["jobs"]["backend"]["steps"]
    step = next(row for row in steps if row.get("name") == contract.PROTOCOL_STEP)
    if change == "continue":
        step["continue-on-error"] = True
    elif change == "optional":
        step["if"] = "false"
    elif change == "environment":
        step["env"]["HIREWIZ_AUTHORITY_EMULATOR_REQUIRED"] = "false"
    elif change == "retained":
        steps[:] = [row for row in steps if row.get("name") != "Qualify retained actual-source schema25 app v1"]
    else:
        next(row for row in steps if row.get("uses", "").startswith("actions/checkout@"))["with"]["fetch-depth"] = 1
    path = tmp_path / ".github/workflows/ci.yml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(workflow))
    with pytest.raises(Exception, match="ci_step_changed|history_required"):
        contract.verify_ci(tmp_path)


@pytest.mark.parametrize("change", ["post", "anonymous", "field", "unrelated"])
def test_additive_http_manifest_rejects_new_authority_or_other_schema_change(tmp_path, monkeypatch, change):
    prior = subprocess.run(["git", "show", contract.PRIOR_RELEASE + ":backend/openapi.json"],
        cwd=ROOT, capture_output=True, check=True).stdout
    value = json.loads((ROOT / "backend/openapi.json").read_bytes())
    contract.additive_http(ROOT)
    if change == "post":
        value["paths"]["/api/auth/bootstrap"]["post"] = value["paths"]["/api/auth/bootstrap"].pop("get")
    elif change == "anonymous":
        value["paths"]["/api/auth/bootstrap"]["get"].pop("security")
    elif change == "field":
        value["components"]["schemas"]["OwnedBootstrapView"]["properties"]["owner_id"] = {"type": "integer"}
    else:
        value["components"]["schemas"]["JobMatchResponse"]["properties"]["match_id"]["type"] = "string"
    path = tmp_path / "backend/openapi.json"
    path.parent.mkdir()
    path.write_text(json.dumps(value))
    monkeypatch.setattr(contract.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=prior))
    with pytest.raises(Exception, match="bootstrap_|unreviewed_http_change"):
        contract.additive_http(tmp_path)

