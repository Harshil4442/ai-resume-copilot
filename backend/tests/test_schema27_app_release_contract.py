"""Owned synthetic Source27 roots only; no actual-root checker or qualification."""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from evaluation.native_acquisition_v2 import qualification
from scripts import schema27_app_release_contract as c


@pytest.fixture(autouse=True)
def forbid_actual_ports(monkeypatch):
    def deny(*_args, **_kwargs):
        pytest.fail("Owned Source27 controls forbid actual Git, Root qualification, network or providers")
    monkeypatch.setattr(c, "git_bytes", deny)
    monkeypatch.setattr(c.v3, "current_document_helpers", deny)
    monkeypatch.setattr(qualification, "_ports", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    monkeypatch.setattr(socket.socket, "connect", deny)


def write(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else value.encode())


def workflow():
    checkout = {"uses": "actions/checkout@owned-fixture", "with": {
        "ref": "${{ github.event.pull_request.head.sha || github.sha }}", "fetch-depth": 0}}
    names = ("backend", "frontend", "security-and-container", "browser-companion", "cold-browser-journey")
    jobs = {name: {"runs-on": "ubuntu-24.04", "steps": [copy.deepcopy(checkout)]} for name in names}
    steps = jobs["backend"]["steps"]
    for name, command in ((c.COMPATIBILITY_STEP, c.COMPATIBILITY_COMMAND), (c.PROTOCOL_STEP, c.PROTOCOL_COMMAND),
                          (c.RETAINED_STEP, c.RETAINED_COMMAND), (c.RETAINED_PROTOCOL_STEP, c.RETAINED_PROTOCOL_COMMAND),
                          (c.v3.RETAINED_STEP, c.v3.RETAINED_COMMAND)):
        steps.append({"name": name, "working-directory": "backend", "run": command, "env": copy.deepcopy(c.NATIVE_ENV)})
    steps.append({"name": "Qualify retained actual-source schema25 app v1", "working-directory": "backend",
                  "run": ".venv/bin/python scripts/qualify_schema25_retained_v1.py",
                  "env": {"HIREWIZ_TEST_POSTGRES_URL": c.PG_URL, "HIREWIZ_APP_COMPATIBILITY_REQUIRED": "true"}})
    steps.append({"name": "Run backend tests", "env": copy.deepcopy(c.NATIVE_ENV),
        "run": ".venv/bin/pytest -q -p scripts.ci_postgres_lock_diagnostics "
               + " ".join("--ignore=" + name for name in c.HISTORICAL_CURRENT_IGNORES) + "\n"})
    return {"jobs": jobs}


@pytest.fixture
def owned(tmp_path, monkeypatch):
    root = tmp_path / "source27-owned"
    root.mkdir()
    docker = b"FROM owned\nENV NOTIFICATION_DIGESTS_ENABLED=false LIFECYCLE_EMAILS_ENABLED=false\n"
    write(root, "Dockerfile", docker)
    monkeypatch.setattr(c, "DOCKERFILE_SHA256", hashlib.sha256(docker).hexdigest())
    write(root, "backend/app/domains/recovery/gcp_notification_digest.py", "\n".join(
        f"{key}={value!r}" for key, value in c.DIGEST_NAMESPACES.items()))
    write(root, "backend/app/domains/recovery/gcp_digest_census.py",
          "DIGEST_NAMESPACES={HEADS,HISTORY,BEGINS,OUTCOMES}\nDIGEST_COMMANDS=(" + ",".join(c.DIGEST_COMMANDS) + ")\n")
    write(root, "backend/app/domains/recovery/gcp_partitioned_contracts.py", "\n".join(
        f"class {name}:\n kind={kind!r}".replace(" kind=", " kind: str=")
        for name, kind in zip(c.DIGEST_COMMANDS, c.DIGEST_KINDS, strict=True))
        + "\nclass ScopedJournalIntent:\n command: Annotated[" + "|".join((*c.OLD_COMMANDS, *c.DIGEST_COMMANDS))
        + ",Field(discriminator='kind')]\n")
    write(root, "backend/app/domains/notification_consent/unsubscribe.py",
          "PURPOSE=b'hirewiz:weekly-digest-disable:v1'\nMAX_TOKEN_BYTES=4096\n")
    write(root, "backend/scripts/neon_direct_identity.py",
          "SCHEMA27_RELEASE_SCHEMA='20261105_0027'\n"
          "def collect_schema27_release_target(*args,**kwargs):\n"
          " return _collect_fresh_release_target(*args,purpose='schema27_fresh_release_capture',**kwargs)\n")
    write(root, "backend/app/owned.py", "VALUE=1\n")
    app = c.current_app(root)
    monkeypatch.setattr(c, "CANDIDATE_APP_FILES", len(app))
    monkeypatch.setattr(c, "CANDIDATE_APP_SHA256", c.app_digest(app))
    prior = {"backend/app/prior.py": b"OWNED_PRIOR=1\n"}
    monkeypatch.setattr(c, "PRIOR_APP_FILES", 1)
    monkeypatch.setattr(c, "PRIOR_APP_SHA256", c.app_digest(prior))
    before = {"info": {"version": "owned"}, "paths": {"/old": {"get": {"description": "unchanged"}}},
              "components": {"schemas": {"Kept": {"type": "integer"}}}}
    current = copy.deepcopy(before)
    for name in c.NEW_HTTP_PATHS:
        current["paths"][name] = {"post" if name.endswith("unsubscribe") else "get": {"description": "owned"}}
    for name in c.NEW_HTTP_SCHEMAS:
        current["components"]["schemas"][name] = {"type": "object", "title": name}
    monkeypatch.setattr(c, "HTTP_PATHS", {n: c.digest(v) for n, v in current["paths"].items() if n != "/old"})
    monkeypatch.setattr(c, "HTTP_SCHEMAS", {n: c.digest(v) for n, v in current["components"]["schemas"].items() if n != "Kept"})
    write(root, "backend/openapi.json", json.dumps(current))
    monkeypatch.setattr(c, "git_bytes", lambda r, name: json.dumps(before).encode()
                        if r == root and name == "backend/openapi.json" else pytest.fail("Unowned retained read"))
    write(root, ".github/workflows/ci.yml", yaml.safe_dump(workflow()))
    visits = []
    monkeypatch.setattr(c.schema27, "verify_source", lambda r: visits.append(("migration", r)))
    monkeypatch.setattr(c, "preserved_sources", lambda r: visits.append(("preserved", r)) or {"retained": "owned"})
    return SimpleNamespace(root=root, prior=prior, current=current, before=before, visits=visits)


def test_new_family_is_not_the_neverqualified294_or_retained_producer():
    assert c.PRIOR_RELEASE == "45b3e808c88d78c1d7d182cd4af4482df25c13f0"
    assert c.PRIOR_APP_FILES == 290 and c.PRIOR_APP_SHA256.startswith("f2775c3b")
    assert c.CANDIDATE_APP_FILES == 311 and c.CANDIDATE_APP_SHA256.startswith('77cae81f')
    assert c.HTTP_PATHS == c.v3.HTTP_PATHS | c.NEW_HTTP_PATHS
    assert c.HTTP_SCHEMAS == c.v3.HTTP_SCHEMAS | c.NEW_HTTP_SCHEMAS
    assert len(c.NEW_HTTP_PATHS) == 2 and len(c.NEW_HTTP_SCHEMAS) == 3
    assert c.HISTORICAL_CURRENT_IGNORES[-3:] == ("tests/test_schema25_app_compatibility_v3_postgres.py",
        "tests/test_schema25_app_protocol_v3.py", "tests/test_schema25_app_rolling_v3.py")


def test_owned_complete_family_fingerprint_is_stable_and_never_invokes_a_native_role(owned):
    p = owned
    assert c.source_fingerprint(p.root, p.prior) == c.source_fingerprint(p.root, p.prior)
    assert p.visits == [("migration", p.root), ("preserved", p.root)] * 2


@pytest.mark.parametrize("drift", ["candidate", "prior", "extra", "symlink"])
def test_unreviewed_app_inventory_never_qualifies(owned, drift):
    p = owned
    if drift == "candidate":
        write(p.root, "backend/app/owned.py", "VALUE=2\n")
    elif drift == "prior":
        p.prior["backend/app/prior.py"] += b"changed"
    elif drift == "extra":
        write(p.root, "backend/app/extra.py", "EXTRA=True\n")
    else:
        path = p.root / "backend/app/owned.py"
        path.unlink()
        path.symlink_to(p.root / "Dockerfile")
    with pytest.raises(c.Source27Denied):
        c.source_fingerprint(p.root, p.prior)


@pytest.mark.parametrize("drift", ["reviewed_route", "extra_route", "unrelated_schema", "top_level", "public_auth"])
def test_only_exact_reviewed_http_changes_are_allowed(owned, drift):
    p = owned
    value = copy.deepcopy(p.current)
    if drift == "reviewed_route":
        value["paths"][next(iter(c.NEW_HTTP_PATHS))]["delete"] = {}
    elif drift == "extra_route":
        value["paths"]["/unreviewed"] = {"post": {}}
    elif drift == "unrelated_schema":
        value["components"]["schemas"]["Kept"]["type"] = "string"
    elif drift == "top_level":
        value["info"]["version"] = "different"
    else:
        value["paths"]["/api/public/notifications/unsubscribe"]["post"]["security"] = [{"signIn": []}]
    write(p.root, "backend/openapi.json", json.dumps(value))
    with pytest.raises(c.Source27Denied):
        c.additive_http(p.root)


@pytest.mark.parametrize("drift", ["docker", "namespace", "command", "union", "purpose", "neon_purpose"])
def test_defaultoff_closed_native_and_purpose_guards(owned, drift):
    p = owned
    names = {"docker": "Dockerfile", "namespace": "backend/app/domains/recovery/gcp_notification_digest.py",
             "command": "backend/app/domains/recovery/gcp_digest_census.py",
             "union": "backend/app/domains/recovery/gcp_partitioned_contracts.py",
             "purpose": "backend/app/domains/notification_consent/unsubscribe.py",
             "neon_purpose": "backend/scripts/neon_direct_identity.py"}
    path = p.root / names[drift]
    text = path.read_text()
    text = text.replace("false", "true") if drift == "docker" else text.replace(
        "v3_digest_send_begins", "unknown_namespace") if drift == "namespace" else text.replace(
        "ScopedDigestEmailDisable", "UnreviewedCommand") if drift in {"command", "union"} else text.replace(
        "schema27_fresh_release_capture", "schema26_fresh_release_capture") if drift == "neon_purpose" else text.replace(
        "weekly-digest-disable", "account-login")
    path.write_text(text)
    with pytest.raises((c.Source27Denied, KeyError)):
        c.runtime_contract(p.root)


@pytest.mark.parametrize("drift", ["missing_new", "old_command", "environment", "wrong_image", "ignore", "history"])
def test_exact_ci_family_preserves_retained_runs_and_current_boundary(owned, drift):
    value = workflow()
    steps = value["jobs"]["backend"]["steps"]
    if drift == "missing_new":
        steps[:] = [s for s in steps if s.get("name") != c.COMPATIBILITY_STEP]
    elif drift == "old_command":
        next(s for s in steps if s.get("name") == c.RETAINED_STEP)["run"] = c.v3.COMPATIBILITY_COMMAND
    elif drift == "environment":
        next(s for s in steps if s.get("name") == c.PROTOCOL_STEP)["env"]["HIREWIZ_AUTHORITY_EMULATOR_REQUIRED"] = "false"
    elif drift == "wrong_image":
        value["jobs"]["frontend"]["runs-on"] = "ubuntu-latest"
    elif drift == "ignore":
        full = next(s for s in steps if s.get("name") == "Run backend tests")
        full["run"] = full["run"].replace("\n", " --ignore=unreviewed.py\n")
        # A matching substring alone must not admit an extra exclusion.
    else:
        steps[0]["with"]["fetch-depth"] = 1
    write(owned.root, ".github/workflows/ci.yml", yaml.safe_dump(value))
    with pytest.raises(c.Source27Denied):
        c.verify_ci(owned.root)


def successful_steps():
    return [{"name": name, "status": "completed", "conclusion": "success"} for name in
            (c.COMPATIBILITY_STEP, c.PROTOCOL_STEP, c.RETAINED_STEP, c.RETAINED_PROTOCOL_STEP, c.v3.RETAINED_STEP)]


@pytest.mark.parametrize("bad", ["missing", "duplicate", "pending"])
def test_native_ci_requires_each_actual_new_and_retained_step(bad):
    steps = successful_steps()
    if bad == "missing":
        steps.pop(0)
    elif bad == "duplicate":
        steps.append(copy.deepcopy(steps[0]))
    else:
        steps[0]["conclusion"] = None
    observer = SimpleNamespace(_command=lambda *_: json.dumps({"jobs": [{"name": "backend", "steps": steps}]}).encode())
    with pytest.raises(c.Source27Denied, match="family_ci_not_successful"):
        c.verify_native_ci(observer, 1)


@pytest.fixture
def observer(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "infra/gcp"))
    m = importlib.import_module("schema27_native_migration")
    source = m.Source27BridgeBoundary(tmp_path)
    monkeypatch.setattr(m.rolling.release.source_guard, "checked_files", lambda *_: {})
    monkeypatch.setattr(m.rolling.release.source_guard, "migration_chain",
                        lambda *_a, **_k: (*m.app26.initial.CHAIN, c.schema27.BEFORE, c.schema27.SCHEMA))
    monkeypatch.setattr(m.contract, "verify_source", lambda *_: "owned-migration")
    docker = b"owned-baked-false-source"
    write(tmp_path, "Dockerfile", docker)
    monkeypatch.setattr(m, "BRIDGE_DOCKERFILE_SHA256", hashlib.sha256(docker).hexdigest())
    current = {"role": "current_application_operator_source", "family": c.CONTRACT, "release": "a" * 40,
               "app_sha256": c.CANDIDATE_APP_SHA256, "app_files": c.CANDIDATE_APP_FILES,
               "qualification_sha256": "b" * 64, "source_only": True, "native_runner_eligible": False,
               "retained_producer_proof": False, "processing_authorized": False, "native_approval": False}
    identity = {**current, "application_source": current, "authorizes_retail": False}
    calls = []
    monkeypatch.setattr(qualification, "current_application_identity",
        lambda root, *, family: calls.append(("application", root, family)) or identity)
    source._command = lambda *_: json.dumps({"jobs": [{"name": "backend", "steps": successful_steps()}]}).encode()
    source._verify_source_image_provenance = lambda **kwargs: calls.append(("stock_provenance", kwargs))
    return SimpleNamespace(source=source, identity=identity, calls=calls, root=tmp_path)


def test_source27_observer_actually_dispatches_current_role_new_ci_and_stock_image_proof(observer):
    p = observer
    p.source.verify_source_image(release="a" * 40, image_digest="sha256:" + "c" * 64, build_id="owned", ci_run_id=1)
    assert p.calls[0] == ("application", p.root, c.CONTRACT)
    assert p.calls[1][0] == "stock_provenance"


@pytest.mark.parametrize("drift", ["family", "native_runner_eligible", "processing_authorized", "app_files", "release"])
def test_current_role_cannot_be_a_label_or_native_qa_grant(observer, drift):
    p = observer
    if drift in {"native_runner_eligible", "processing_authorized"}:
        p.identity[drift] = True
    elif drift == "family":
        p.identity["application_source"]["family"] = "hirewiz_schema26_app_rolling_v3"
    elif drift == "app_files":
        p.identity["application_source"]["app_files"] = 294
    else:
        p.identity["application_source"]["release"] = "d" * 40
    with pytest.raises(Exception, match="current_application_source_role"):
        p.source.verify_source_image(release="a" * 40, image_digest="sha256:" + "c" * 64, build_id="owned", ci_run_id=1)
    assert all(call[0] != "stock_provenance" for call in p.calls)
