"""Owned source-family fixtures only; never actual corpus, Root checks or IO."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import socket
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from evaluation.native_acquisition_v2 import qualification as profile
from scripts import qualify_native_quality_acquisition as q

FAMILY = "hirewiz_schema27_app_v1"
LEGACY = "hirewiz_schema26_app_rolling_v3"
HEAD = "a" * 40
STAMP = "b" * 64


@pytest.fixture(autouse=True)
def no_actual_source_or_network(monkeypatch):
    def denied(*_args, **_kwargs):
        pytest.fail("Owned source controls must not reach actual Git, Root bootstrap, network or providers")
    monkeypatch.setattr(q, "git", denied)
    monkeypatch.setattr(profile, "_ports", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


@pytest.fixture
def modules(tmp_path, monkeypatch):
    root = tmp_path / "owned-modules"
    root.mkdir()
    monkeypatch.setattr(q, "ROOT", root)
    import scripts
    def module(name, path, **values):
        value = ModuleType(name)
        value.__file__ = str(root / path)
        value.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, value)
        return value
    native = module("schema27_native_migration", "infra/gcp/schema27_native_migration.py",
        Source27BridgeBoundary=lambda root: ("Source27", root))
    contract = module("scripts.schema27_app_release_contract", "backend/scripts/schema27_app_release_contract.py",
        CONTRACT=FAMILY, CANDIDATE_APP_SHA256=q.CURRENT_APPLICATION_APP_SHA256,
        CANDIDATE_APP_FILES=312, PRIOR_RELEASE="c" * 40)
    monkeypatch.setattr(scripts, "schema27_app_release_contract", contract, raising=False)
    release = module("unified_credit_schema25_release", "infra/gcp/unified_credit_schema25_release.py")
    old = (SimpleNamespace(Source26Boundary=lambda root: ("Source26", root)), SimpleNamespace(), SimpleNamespace())
    monkeypatch.setattr(q, "_source_modules", lambda: old)
    return SimpleNamespace(root=root, native=native, contract=contract, release=release, old=old)


def test_current_family_and_observer_are_explicit_while_old_default_remains(modules):
    m = modules
    assert q.current_application_modules(FAMILY) == (m.native, m.contract, m.release)
    assert q.current_application_observer(m.root, family=FAMILY) == ("Source27", m.root)
    assert q.current_application_modules(LEGACY) is m.old
    assert q.current_application_observer(m.root, family=LEGACY) == ("Source26", m.root)


@pytest.mark.parametrize("family", [None, True, "", "schema27", "20261105_0027"])
def test_unknown_family_is_not_selected_from_a_schema_or_boolean(modules, family):
    with pytest.raises(ValueError, match="application_family"):
        q.current_application_modules(family)


@pytest.mark.parametrize("name", ["native", "contract", "release"])
def test_current_family_refuses_foreign_validator_origins(modules, name):
    getattr(modules, name).__file__ = str(modules.root / "foreign.py")
    with pytest.raises(ValueError, match="foreign_validator"):
        q.current_application_modules(FAMILY)


@pytest.mark.parametrize("name,value", [("CONTRACT", LEGACY), ("CANDIDATE_APP_SHA256", "0" * 64),
    ("CANDIDATE_APP_FILES", 294)])
def test_current_family_requires_the_exact_new_contract_and312_pin(modules, name, value):
    setattr(modules.contract, name, value)
    with pytest.raises(ValueError, match="application_pins"):
        q.current_application_modules(FAMILY)


def test_application_source_invokes_its_actual_family_fingerprint_and_two_source_fences(modules, monkeypatch):
    m, calls = modules, []
    m.native.rolling = SimpleNamespace(release=SimpleNamespace(source_guard=SimpleNamespace(
        checked_files=lambda root, head: calls.append(("checked", root, head)))))
    m.release.prior_app_files = lambda root, prior: {"owned-prior.py": b"owned prior source"}
    m.contract.source_fingerprint = lambda root, files: calls.append(("fingerprint", root, files)) or STAMP
    monkeypatch.setattr(q, "git", lambda root, *args: HEAD.encode())
    result = q.current_application_source(m.root, family=FAMILY)
    assert [row[0] for row in calls] == ["checked", "fingerprint", "checked"]
    assert result["qualification_sha256"] == STAMP and result["app_files"] == 312
    assert result["family"] == FAMILY and result["role"] == "current_application_operator_source"
    assert result["native_runner_eligible"] is result["retained_producer_proof"] is result["processing_authorized"] is False
    assert "entry_sha256" not in result and "build_id" not in result


def test_application_source_refuses_head_change_after_source_fingerprint(modules, monkeypatch):
    m = modules
    m.native.rolling = SimpleNamespace(release=SimpleNamespace(source_guard=SimpleNamespace(checked_files=lambda *_: None)))
    m.release.prior_app_files = lambda *_: {}
    m.contract.source_fingerprint = lambda *_: STAMP
    answers = iter([HEAD.encode(), b"c" * 40])
    monkeypatch.setattr(q, "git", lambda *_: next(answers))
    with pytest.raises(ValueError, match="head_changed"):
        q.current_application_source(m.root, family=FAMILY)


@pytest.fixture(params=[LEGACY, FAMILY])
def owned_profile(tmp_path, monkeypatch, request):
    root, family = tmp_path / "owned-source", request.param
    root.mkdir()
    contract, observer = profile.APPLICATION_CONTRACTS[family]
    app_sha = q.CURRENT_APPLICATION_APP_SHA256 if family == FAMILY else q.APP_SHA256
    count = 312 if family == FAMILY else 294
    literal = {"CONTRACT": family, "CANDIDATE_APP_SHA256": app_sha, "CANDIDATE_APP_FILES": count,
        "COMPATIBILITY_STEP": "Owned compatibility", "PROTOCOL_STEP": "Owned protocol", "RETAINED_STEP": "Owned retained"}
    port = {"CURRENT_APPLICATION_FAMILY": FAMILY, "CURRENT_APPLICATION_APP_SHA256": q.CURRENT_APPLICATION_APP_SHA256,
            "CURRENT_APPLICATION_APP_FILES": 312} if family == FAMILY else {"APP_SHA256": q.APP_SHA256}
    files = {profile.PORT_PATH: "\n".join(name + " = " + repr(value) for name, value in port.items()) + "\nimport " + Path(observer).stem + "\n",
        contract: "\n".join(name + " = " + repr(value) for name, value in literal.items()) + "\n",
        observer: "from app import owned_helper\n", "backend/app/owned_helper.py": '"""Owned source; never execute."""\n'}
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    source = {"role": "current_application_operator_source", "family": family, "release": HEAD,
        "qualification_sha256": STAMP, "app_sha256": app_sha, "app_files": count,
        "source_only": True, "native_runner_eligible": False, "retained_producer_proof": False,
        "processing_authorized": False, "native_approval": False}
    state = SimpleNamespace(root=root, family=family, contract=contract, observer=observer,
        source=source, tracked=set(files), head=HEAD, dirty=set())
    def git(where, *args):
        assert where == root
        if args == ("rev-parse", "HEAD"):
            return state.head.encode()
        assert args[0] == "show" and args[1].startswith(HEAD + ":")
        name = args[1].split(":", 1)[1]
        if name not in state.tracked:
            raise ValueError("owned_untracked")
        return b"old committed bytes" if name in state.dirty else (root / name).read_bytes()
    ports = SimpleNamespace(git=git, current_application_source=lambda root, family: copy.deepcopy(source))
    monkeypatch.setattr(profile, "_ports", lambda: ports)
    state.ports = ports
    return state


def test_separate_operator_role_is_source_bound_and_never_changes_producer_roles(owned_profile):
    s = owned_profile
    result = profile.current_application_identity(s.root, family=s.family)
    assert result["application_source"] == s.source and result["family"] == s.family
    assert set(result["controller_dependencies"]) == s.tracked
    assert result["literal_application_inputs"][s.contract]["CANDIDATE_APP_FILES"] == s.source["app_files"]
    assert result["native_runner_eligible"] is result["retained_producer_proof"] is result["native_approval"] is False
    assert result["processing_authorized"] is result["authorizes_retail"] is False
    assert "runtime_dependencies_sha256" not in result and "entry_sha256" not in result
    assert profile.digest({key: value for key, value in result.items() if key != "application_operator_sha256"}) == result["application_operator_sha256"]


@pytest.mark.parametrize("change", ["literal", "uncommitted", "role", "head"])
def test_operator_role_refuses_literal_commit_purpose_and_source_changes(owned_profile, change):
    s = owned_profile
    if change == "literal":
        path = s.root / s.contract
        path.write_text(path.read_text().replace(repr(s.source["app_sha256"]), repr("0" * 64)))
    elif change == "uncommitted":
        s.dirty.add(s.contract)
    elif change == "role":
        s.source["native_runner_eligible"] = True
    else:
        s.head = "c" * 40
    with pytest.raises(ValueError):
        profile.current_application_identity(s.root, family=s.family)


@pytest.mark.parametrize("change", ["missing", "duplicate", "transitive_uncommitted"])
def test_operator_role_requires_complete_unambiguous_literals_and_committed_imports(owned_profile, change):
    s = owned_profile
    path = s.root / s.contract
    if change == "missing":
        path.write_text(path.read_text().replace("RETAINED_STEP = 'Owned retained'\n", ""))
    elif change == "duplicate":
        path.write_text(path.read_text() + "CANDIDATE_APP_FILES = 1\n")
    else:
        s.dirty.add("backend/app/owned_helper.py")
    with pytest.raises(ValueError):
        profile.current_application_identity(s.root, family=s.family)


def test_current_operator_family_rejects_unknown_before_bootstrap(tmp_path):
    with pytest.raises(ValueError, match="application_family"):
        profile.current_application_identity(tmp_path, family="20261105_0027")


def test_bootstrap_pin_matches_exact_current_q_bytes_without_loading_root_checker():
    assert hashlib.sha256(Path(q.__file__).read_bytes()).hexdigest() == profile.PORT_SHA256


@pytest.mark.parametrize("module,entry", [(q, "current_application_source"), (profile, "current_application_identity")])
def test_explicit_application_cli_consumes_the_separate_role_without_default_runner(module, entry, tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(module, "ROOT", tmp_path)
    def current(where, *, family):
        calls.append((where, family))
        return {"family": family, "source_only": True, "native_runner_eligible": False, "processing_authorized": False}
    monkeypatch.setattr(module, entry, current)
    monkeypatch.setattr(module, "source_identity", lambda *_: pytest.fail("Current application CLI reached the old producer role"))
    monkeypatch.setattr(sys, "argv", ["owned-cli", "--root", str(tmp_path), "--application-family", FAMILY])
    assert module.main() == 0 and calls == [(tmp_path, FAMILY)]
    assert json.loads(capsys.readouterr().out)["native_runner_eligible"] is False


@pytest.mark.parametrize("module,entry", [(q, "current_application_source"), (profile, "current_application_identity")])
def test_application_and_retained_source_child_roles_cannot_be_combined(module, entry, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(module, entry, lambda *_args, **_kwargs: pytest.fail("Conflicting source roles reached execution"))
    monkeypatch.setattr(sys, "argv", ["owned-cli", "--root", str(tmp_path), "--source-child", "--application-family", FAMILY])
    assert module.main() == 1
    assert json.loads(capsys.readouterr().out)["native_approval"] is False


def test_retained_producer_runtime_paths_literals_and_pin_roles_are_unchanged():
    assert q.APP_SHA256 == "4c86409a4eae9bd941e2e89a3b000b972fd891f3d1c67a9ed0fe68b3effb27af"
    assert q.REGISTRY_RUNNER_RELEASE == "f361ac16aa17a53c640740a75c627421a9e758c9"
    assert q.REGISTRY_RUNNER_SHA256 == "f99d4294c232de5e09769e3582abf1cd4ba245bb65fa4faebd027ff16d7f16a5"
    assert profile.LITERAL_INPUTS == {
        "backend/scripts/qualify_native_quality_acquisition.py": ("APP_SHA256", "ENTRY", "ENTRY_SHA256", "HISTORICAL_RELEASE"),
        "backend/scripts/schema25_app_release_contract_v3.py": ("CANDIDATE_APP_SHA256", "CANDIDATE_APP_FILES", "COMPATIBILITY_STEP", "PROTOCOL_STEP", "RETAINED_STEP")}
    frozen = {q: {"dependency_hashes": "696738fa4ca2aab593a74a8b81a2298c6be54ba2b1fdbb47787fd3522a6d9abb",
        "controller_dependency_hashes": "194b2f5d3a3df55eee783a39819a0c345495a202965c2e3e252df7fd8c518880",
        "_execution_inventory": "90da32589a5ee56a972255b2fb70cadf3fee6fcda7d61ae7e9971f0c15835ee5",
        "_source_modules": "bed1e517ee05ff0752e89ed7ee32280b3932db6b69fb831c5f9bca47d60b0136",
        "_runner_source": "085720acf5488343d0b97c9bd1a8eba0b25a762dbbc09240c3f5db604d1d05f1",
        "verify_qualified_runner": "cce5e67b984508d40fac41ecd9ef047449bf0fbb669aaaa1ca16b3624d1587d2"},
        profile: {"_literal_inputs": "3ff297c0ba42cc5f4070cf68818ae2b5f395d9cae92e2234964483d95f775a87",
        "source_identity": "985a1e7fcdc908ac5c6ad225c0ad5ea6b8ed70d25f0ac5489ed7422e96b6b466",
        "_source_child": "c0b9f8bdcb3577d5adb8daca9494d69a74efde27da23b3cff35bfc73695e9af7",
        "verify_qualified_runner": "6763a381d36322ba6ba2a29c4710316489042369693452e898f8d3efdfeb2952"}}
    for module, functions in frozen.items():
        source = Path(module.__file__).read_text()
        tree = ast.parse(source)
        for name, expected in functions.items():
            node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
            assert hashlib.sha256(ast.get_source_segment(source, node).encode()).hexdigest() == expected
