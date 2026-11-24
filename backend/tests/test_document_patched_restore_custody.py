"""Read-only restore composition never adopts a different mutator identity."""
from __future__ import annotations

import copy
import hashlib
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from infra.gcp import schema27_native_admission_closure as admission
from infra.gcp import schema27_patched_engine_recovery as patched
from infra.gcp import schema27_staged_restore as restore
from test_schema27_staged_restore import application as application
from test_schema27_staged_restore import context as context
from test_schema27_staged_restore import controlled as controlled
from test_schema27_staged_restore import current as current
from test_schema27_staged_restore import handoff as handoff
from test_schema27_staged_restore import lane as lane
from test_schema27_staged_restore import mixed_app as mixed_app
from test_schema27_staged_restore import no_network as no_network
from test_schema27_staged_restore import owned_app_roots as owned_app_roots
from test_schema27_staged_restore import profile as profile
from test_schema27_staged_restore import staged as staged


@pytest.fixture
def restored(staged, monkeypatch, tmp_path):
    files = {*restore.owned.SOURCE_FILES, "infra/gcp/schema27_staged_restore.py"}
    retained = {name: (patched.identities.ROOT / name).read_bytes() for name in files}
    staged.stamp["files"] = {name: hashlib.sha256(raw).hexdigest() for name, raw in retained.items()}
    plan = restore.capture(staged.original, staged.original_sha256, staged.boundary,
        nonce="d" * 32, journal_directory=staged.directory)
    journal = restore.owned.Journal(staged.directory, plan, create=True)
    restore.execute("restore", plan, staged.boundary, journal)
    original_git = restore.mixed.app.successor._git
    root = tmp_path / "historical-helper-bytes"
    root.mkdir()
    for name, raw in retained.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    def git(selected_root, *args):
        if selected_root == root and args == ("rev-parse", plan.source["release"] + "^{tree}"):
            return plan.source["tree"].encode()
        if selected_root == root and args[0] == "show" and args[1].startswith(plan.source["release"] + ":"):
            return retained[args[1].split(":", 1)[1]]
        return original_git(selected_root, *args)
    monkeypatch.setattr(restore.mixed.app.successor, "_git", git)
    monkeypatch.setattr(patched.identities, "ROOT", root)
    monkeypatch.setattr(patched, "RESTORE_RELEASE", plan.source["release"])
    monkeypatch.setattr(patched, "RESTORE_TREE", plan.source["tree"])
    path = tmp_path / "retained-plan.json"
    raw = restore.rolling.canonical(asdict(plan))
    path.write_bytes(raw)
    path.chmod(0o600)
    digest = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(patched, "RESTORE_PLAN_FILE_SHA256", digest)
    closure = object.__new__(admission.ClosedAdmission)
    closure.plan = SimpleNamespace(parent={"plan": str(path), "plan_sha256": digest,
        "journal": str(journal.directory),
        "complete_sha256": hashlib.sha256((journal.directory / "complete.json").read_bytes()).hexdigest()})
    closure.verify_custody = lambda: {"new_admission_closed": True}
    closure.verify_current = lambda: {"fresh_closure_snapshot": True}
    return SimpleNamespace(staged=staged, plan=plan, journal=journal, closure=closure, root=root,
        path=path, digest=digest)


def test_exact_historical_consumer_accepts_completed_restore_without_relabeling_mutator(restored, monkeypatch):
    c = restored
    new_source = copy.deepcopy(c.plan.source)
    new_source["release"] = "e" * 40
    monkeypatch.setattr(restore, "source_state", lambda: new_source)
    with pytest.raises(restore.rolling.RollingDenied, match="source_changed"):
        restore.VerifiedRestore(c.plan, c.journal)
    target = patched.HistoricalRestore(c.plan, c.journal, closed=c.closure)
    assert target.verify_custody()["all_homogeneous_source70"] is True
    assert target.verify_current() == {"fresh_closure_snapshot": True}
    assert len(c.staged.c.fixture.writes) == 6
    assert c.plan.source["release"] != new_source["release"]


@pytest.mark.parametrize("fault", ["helper", "git_bytes", "tree", "ack", "checked", "parent_digest", "parent_journal"])
def test_historical_restore_requires_old_git_bytes_all_receipts_and_exact_closure_parent(restored, monkeypatch, fault):
    c = restored
    if fault == "helper":
        path = c.root / "infra/gcp/schema27_staged_restore.py"
        path.write_bytes(path.read_bytes() + b"\n# drift\n")
    elif fault == "git_bytes":
        original = restore.mixed.app.successor._git
        def git(root, *args):
            return b"different Git blob" if root == c.root and args[0] == "show" else original(root, *args)
        monkeypatch.setattr(restore.mixed.app.successor, "_git", git)
    elif fault == "tree":
        monkeypatch.setattr(patched, "RESTORE_TREE", "f" * 40)
    elif fault in {"ack", "checked"}:
        (c.journal.directory / ("restore-stage." + restore.SERVICES[0] + "." + fault + ".json")).unlink()
    elif fault == "parent_digest":
        c.closure.plan.parent["complete_sha256"] = "f" * 64
    else:
        c.closure.plan.parent["journal"] = str(c.root)
    with pytest.raises(restore.rolling.RollingDenied):
        patched.HistoricalRestore(c.plan, c.journal, closed=c.closure)
    assert len(c.staged.c.fixture.writes) == 6


def test_closure_shaped_mapping_is_not_a_verified_closed_admission(restored):
    with pytest.raises(restore.rolling.RollingDenied, match="closed_admission_kind"):
        patched.HistoricalRestore(restored.plan, restored.journal, closed=SimpleNamespace(new_admission_closed=True))


def test_exact_completion_reader_uses_honest_restore_parent_not_closure_parent(restored, monkeypatch):
    from infra.gcp import schema27_native_admission_completion as completion

    c = restored
    closed = object.__new__(completion.ClosedAdmissionCompletion)
    closed.plan = SimpleNamespace(parent={"owner": "separate-original-closure"})
    closed.verify_custody = lambda: {"new_admission_closed": True}
    closed.verify_current = lambda: {"fresh_completed_closure_snapshot": True}
    monkeypatch.setattr(completion, "read_parent", lambda parent: (
        SimpleNamespace(parent=c.closure.plan.parent), None, None)
        if parent == closed.plan.parent else pytest.fail("unexpected parent read"))
    target = patched.HistoricalRestore(c.plan, c.journal, closed=closed)
    assert target.verify_current() == {"fresh_completed_closure_snapshot": True}
    assert target.closed.plan.parent != c.closure.plan.parent
    assert len(c.staged.c.fixture.writes) == 6


def test_completion_subclass_cannot_enter_the_typed_restore_bridge(restored):
    from infra.gcp import schema27_native_admission_completion as completion

    class PretendCompletion(completion.ClosedAdmissionCompletion):
        pass
    with pytest.raises(restore.rolling.RollingDenied, match="closed_admission_kind"):
        patched.HistoricalRestore(restored.plan, restored.journal, closed=object.__new__(PretendCompletion))


def test_exact_internal_reader_keeps_both_incomplete_parents_separate(restored, monkeypatch):
    from infra.gcp import schema27_internal_coordinator_completion as internal

    c = restored
    closed = object.__new__(internal.ClosedInternalAdmission)
    closed.plan = SimpleNamespace(parent={"owner": "genuine-stage-and-incomplete-external-health"})
    completed_parent = {"owner": "original-api-closure-and-definite-coordinator-rejection"}
    closed.verify_custody = lambda: {"new_admission_closed": True, "response_json_observed": False}
    closed.verify_current = lambda: {"fresh_internal_closure_snapshot": True}
    monkeypatch.setattr(internal, "read_parent", lambda parent: (
        SimpleNamespace(parent=completed_parent), None)
        if parent == closed.plan.parent else pytest.fail("unexpected internal parent read"))
    monkeypatch.setattr(internal.prior, "read_parent", lambda parent: (
        SimpleNamespace(parent=c.closure.plan.parent), None, None)
        if parent == completed_parent else pytest.fail("unexpected completion parent read"))
    target = patched.HistoricalRestore(c.plan, c.journal, closed=closed)
    assert target.verify_current() == {"fresh_internal_closure_snapshot": True}
    assert closed.restore_parent == c.closure.plan.parent
    assert closed.plan.parent != completed_parent != closed.restore_parent
    assert len(c.staged.c.fixture.writes) == 6


def test_internal_subclass_cannot_enter_the_typed_restore_bridge(restored):
    from infra.gcp import schema27_internal_coordinator_completion as internal

    class PretendInternal(internal.ClosedInternalAdmission):
        pass
    with pytest.raises(restore.rolling.RollingDenied, match="closed_admission_kind"):
        patched.HistoricalRestore(restored.plan, restored.journal, closed=object.__new__(PretendInternal))


def test_cli_closure_reader_selectors_are_mutually_exclusive(monkeypatch):
    import sys

    argv = ["schema27_patched_engine_recovery", "build", "--publish-candidate"]
    for name in ("identity-profile", "identity-profile-sha256", "restore-plan", "restore-plan-sha256",
                 "restore-journal", "admission-closure-plan", "admission-closure-plan-sha256",
                 "admission-closure-journal", "prior-history-directory", "output-directory"):
        argv.extend(["--" + name, "unused-before-parser-validation"])
    argv.extend(["--admission-closure-completed", "--admission-closure-internal"])
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as error:
        patched.main()
    assert error.value.code == 2
