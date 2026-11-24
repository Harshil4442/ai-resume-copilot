"""Owned app-family fixtures; no provider, retained runner or native QA authority."""
from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from test_schema27_app_release_contract import owned as legacy_owned
from test_schema27_app_release_contract import workflow, write

from scripts import qualify_native_quality_acquisition as ports
from scripts import qualify_schema27 as gate
from scripts import schema27_app_r02_release_contract as r02
from scripts import schema27_app_release_contract as old

assert legacy_owned


@pytest.fixture
def owned(legacy_owned, monkeypatch):
    state = legacy_owned
    # Old exact synthetic inventory remains valid only for its old family.
    state.old_app = old.current_app(state.root)
    state.old_workflow = workflow()
    write(state.root, "backend/app/owned.py", "VALUE=2\n")
    monkeypatch.setattr(r02, "CANDIDATE_APP_FILES", len(r02.current_app(state.root)))
    monkeypatch.setattr(r02, "CANDIDATE_APP_SHA256", r02.app_digest(r02.current_app(state.root)))
    monkeypatch.setattr(r02, "PRIOR_APP_FILES", len(state.prior))
    monkeypatch.setattr(r02, "PRIOR_APP_SHA256", r02.app_digest(state.prior))
    monkeypatch.setattr(r02, "preserved_sources", lambda root: state.visits.append(("preserved-r02", root)) or {"owned": "retained"})
    for name in r02.R02_PATHS:
        write(state.root, name, "# owned current qualification input\n")
    current = copy.deepcopy(state.old_workflow)
    for step in current["jobs"]["backend"]["steps"]:
        if step.get("name") == old.COMPATIBILITY_STEP:
            step.update(name=r02.COMPATIBILITY_STEP, run=r02.COMPATIBILITY_COMMAND)
        elif step.get("name") == old.PROTOCOL_STEP:
            step.update(name=r02.PROTOCOL_STEP, run=r02.PROTOCOL_COMMAND)
    state.current_workflow = current
    write(state.root, ".github/workflows/ci.yml", yaml.safe_dump(current))
    return state


def test_exact_current_inventory_and_phase_semantics_have_a_distinct_non_native_family():
    root = Path(__file__).resolve().parents[2]
    app = r02.current_app(root)
    assert len(app) == r02.CANDIDATE_APP_FILES == 316
    assert r02.app_digest(app) == r02.CANDIDATE_APP_SHA256 == ports.R02_APPLICATION_APP_SHA256
    assert r02.CONTRACT == gate.R02_FAMILY == ports.R02_APPLICATION_FAMILY
    assert r02.CONTRACT != old.CONTRACT and r02.CANDIDATE_APP_SHA256 != old.CANDIDATE_APP_SHA256
    assert r02.CREDIT_PHASE_SEMANTICS == {
        "legacy_default": "widening", "widening_minimum_successes": 200,
        "initial_phase": "initial_canary", "initial_minimum_successes": 1,
        "initial_server_audience": "canary", "shadow_days": 7,
        "public_requires_widening": True, "existing_consent_and_five_percent_admission": True,
        "runtime_config_and_prices_unchanged": True,
    }
    assert r02.runtime_contract is old.runtime_contract and r02.additive_http is old.additive_http
    assert r02.preserved_sources is old.preserved_sources
    assert (r02.PRIOR_RELEASE, r02.PRIOR_APP_SHA256) == (old.PRIOR_RELEASE, old.PRIOR_APP_SHA256)
    declarations = old.assignments(ast.parse(Path(r02.__file__).read_bytes()))
    for name in ("CONTRACT", "CANDIDATE_APP_SHA256", "CANDIDATE_APP_FILES",
                 "COMPATIBILITY_STEP", "PROTOCOL_STEP", "RETAINED_STEP"):
        assert ast.literal_eval(declarations[name]) == getattr(r02, name)


def test_old_exact_source_still_uses_old_reader_and_new_inventory_only_uses_new_reader(owned):
    state = owned
    assert len(r02.source_fingerprint(state.root, state.prior)) == 64
    with pytest.raises(old.Source27Denied, match="current_app_unqualified"):
        old.source_fingerprint(state.root, state.prior)
    for name, raw in state.old_app.items():
        write(state.root, name, raw)
    write(state.root, ".github/workflows/ci.yml", yaml.safe_dump(state.old_workflow))
    assert len(old.source_fingerprint(state.root, state.prior)) == 64
    with pytest.raises(old.Source27Denied, match="current_app_r02_unqualified"):
        r02.source_fingerprint(state.root, state.prior)


@pytest.mark.parametrize("change", ["mixed", "extra", "missing", "linked"])
def test_new_family_refuses_unknown_partial_or_linked_inventory(owned, change):
    path = owned.root / "backend/app/owned.py"
    if change == "mixed":
        path.write_text("VALUE=3\n")
    elif change == "extra":
        write(owned.root, "backend/app/unreviewed.py", "VALUE=1\n")
    elif change == "missing":
        path.unlink()
    else:
        target = owned.root / "elsewhere.py"
        path.replace(target)
        path.symlink_to(target)
    with pytest.raises(old.Source27Denied):
        r02.source_fingerprint(owned.root, owned.prior)


@pytest.mark.parametrize("change", ["phase_selector", "retained_protocol", "checkout", "http", "runtime"])
def test_new_family_preserves_ci_http_and_runtime_guards(owned, change):
    state = owned
    if change == "http":
        value = copy.deepcopy(state.current)
        value["paths"]["/unreviewed"] = {"get": {}}
        write(state.root, "backend/openapi.json", json.dumps(value))
    elif change == "runtime":
        write(state.root, "Dockerfile", "FROM different\n")
    else:
        value = copy.deepcopy(state.current_workflow)
        steps = value["jobs"]["backend"]["steps"]
        if change == "phase_selector":
            next(s for s in steps if s.get("name") == r02.COMPATIBILITY_STEP)["run"] = old.COMPATIBILITY_COMMAND
        elif change == "retained_protocol":
            steps[:] = [s for s in steps if s.get("name") != old.RETAINED_PROTOCOL_STEP]
        else:
            next(s for s in steps if "uses" in s)["with"]["ref"] = "main"
        write(state.root, ".github/workflows/ci.yml", yaml.safe_dump(value))
    with pytest.raises(old.Source27Denied):
        r02.source_fingerprint(state.root, state.prior)


@pytest.mark.parametrize("name", r02.R02_PATHS)
def test_new_profile_keeps_prior_and_full_qualification_input_binding(owned, name):
    first = r02.source_fingerprint(owned.root, owned.prior)
    with pytest.raises(old.Source27Denied, match="qualified_prior"):
        r02.source_fingerprint(owned.root, {"other": b"not the qualified predecessor"})
    path = owned.root / name
    path.write_text(path.read_text() + "# changed owned test input\n")
    assert r02.source_fingerprint(owned.root, owned.prior) != first
    assert ("migration", owned.root) in owned.visits and ("preserved-r02", owned.root) in owned.visits


def test_real_module_selector_requires_explicit_new_family_and_exact_origin():
    _, contract, _ = ports.current_application_modules(ports.R02_APPLICATION_FAMILY)
    assert contract is r02
    _, contract, _ = ports.current_application_modules(ports.CURRENT_APPLICATION_FAMILY)
    assert contract is old
    with pytest.raises(ValueError, match="application_family"):
        ports.current_application_modules("hirewiz_schema27_app_r02_guess")
    with pytest.raises(ValueError, match="profiled_native_observer_required"):
        ports.current_application_observer(Path(__file__).resolve().parents[2], family=r02.CONTRACT)
    # Runtime/producer literals stay separate from the new app-only family.
    assert ports.APP_SHA256 == "4c86409a4eae9bd941e2e89a3b000b972fd891f3d1c67a9ed0fe68b3effb27af"
    assert ports.ENTRY_SHA256 == "8eef532ef4d4978e9dda000e47ee694b43b55e16a168f93f9c7da724ea9b8740"


@pytest.mark.parametrize("name,value", [
    ("__file__", "/foreign/schema27_app_r02_release_contract.py"),
    ("CONTRACT", old.CONTRACT), ("CANDIDATE_APP_SHA256", old.CANDIDATE_APP_SHA256),
    ("CANDIDATE_APP_FILES", 315),
])
def test_r02_selector_refuses_foreign_or_mixed_descriptor(name, value, monkeypatch):
    monkeypatch.setattr(r02, name, value)
    with pytest.raises(ValueError):
        ports.current_application_modules(ports.R02_APPLICATION_FAMILY)


@pytest.mark.parametrize("change", [None, "old_family", "failed", "duplicate"])
def test_native_ci_reader_requires_new_family_steps_not_historical_success(change):
    names = [r02.COMPATIBILITY_STEP, r02.PROTOCOL_STEP, r02.RETAINED_STEP,
             r02.RETAINED_PROTOCOL_STEP, old.v3.RETAINED_STEP]
    steps = [{"name": name, "status": "completed", "conclusion": "success"} for name in names]
    if change == "old_family":
        steps[0]["name"], steps[1]["name"] = old.COMPATIBILITY_STEP, old.PROTOCOL_STEP
    elif change == "failed":
        steps[0]["conclusion"] = "failure"
    elif change == "duplicate":
        steps.append(copy.deepcopy(steps[0]))
    calls = []
    def command(*args):
        calls.append(args)
        return json.dumps({"jobs": [{"name": "backend", "steps": steps}]})
    observer = SimpleNamespace(_command=command)
    if change is None:
        r02.verify_native_ci(observer, 1)
    else:
        with pytest.raises(old.Source27Denied, match="family_ci_not_successful"):
            r02.verify_native_ci(observer, 1)
    assert calls == [("gh", "run", "view", "1", "--repo", "Harshil4442/ai-resume-copilot", "--json", "jobs")]
