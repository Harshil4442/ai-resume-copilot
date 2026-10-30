"""Failure diagnostics are sanitized, bounded and never operation authority."""
from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path

import pytest
import requests
import yaml

from scripts import document_refresh as refresh

SECRET = "private-token-do-not-retain"


def cli_failure(monkeypatch, capsys, output, error, *, mode="full-source-refresh", equals=False):
    calls = []
    argv = (["document_refresh.py", "--mode=" + mode, "--output-directory=" + str(output)] if equals else
            ["document_refresh.py", "--mode", mode, "--output-directory", str(output)])
    monkeypatch.setattr(sys, "argv", argv)
    def failed():
        calls.append("original operation once")
        raise error
    monkeypatch.setattr(refresh, "main", failed)
    monkeypatch.setattr(refresh, "emit_status", lambda value: calls.append(value))
    assert refresh.run_cli() == 2
    console = json.loads(capsys.readouterr().out)
    return calls, console


@pytest.mark.parametrize("mode", ["full-source-refresh", "monitor"])
@pytest.mark.parametrize("equals", [False, True])
def test_original_failure_outcome_and_single_heartbeat_are_preserved_with_private_chain(
        monkeypatch, capsys, tmp_path, mode, equals):
    output = tmp_path / "private"
    output.mkdir(mode=0o700)
    try:
        try:
            json.loads("{" + SECRET)
        except json.JSONDecodeError:
            raise OSError(28, SECRET, "/private/" + SECRET) from None
    except OSError as cause:
        error = RuntimeError("https://example.invalid/" + SECRET)
        error.__cause__ = cause
    calls, console = cli_failure(monkeypatch, capsys, output, error, mode=mode, equals=equals)
    assert calls == ["original operation once", {"mode": "monitor", "definitions": {"status": "unavailable"}}]
    assert console == {"status": "maintenance_attention_required", "fixed_code": "document_refresh_unavailable",
                       "state_requires_read_only_reconciliation": True, "automatic_replay": False}
    path = output / "failure-diagnostic.json"
    raw = path.read_text()
    assert SECRET not in raw and "https://" not in raw and str(refresh.ROOT) not in raw
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    value = json.loads(raw)
    assert {k: value[k] for k in console} == console
    nodes = value["diagnostics"]["nodes"]
    assert [row["kind"] for row in nodes] == ["RuntimeError", "OSError", "JSONDecodeError"]
    assert nodes[1]["errno"] == 28 and nodes[1]["suppress_context"] is True
    assert nodes[2]["json_position"] == {"pos": 1, "lineno": 1, "colno": 2}
    frames = [frame for row in nodes for frame in row["frames"]]
    assert frames and all(not Path(frame["file"]).is_absolute() and frame["line"] > 0 for frame in frames)


@pytest.mark.parametrize("code", ["document_refresh_source_revision_app_context_required", "document_refresh_unavailable"])
def test_source_owned_literal_codes_remain_unchanged(monkeypatch, capsys, tmp_path, code):
    output = tmp_path / "private"
    output.mkdir(mode=0o700)
    _, console = cli_failure(monkeypatch, capsys, output, refresh.RefreshDenied(code))
    value = json.loads((output / "failure-diagnostic.json").read_text())
    assert console["fixed_code"] == value["fixed_code"] == code
    assert value["diagnostics"]["nodes"][0]["fixed_code"] == code


def test_current_application_code_is_literal_bound_not_just_prefix_matched(monkeypatch):
    monkeypatch.syspath_prepend(str(refresh.ROOT / "infra/gcp"))
    from infra.gcp.document_application_context import ApplicationDenied
    valid = refresh._failure_diagnostics(ApplicationDenied("document_application_caller_uid_changed"))
    invalid = refresh._failure_diagnostics(ApplicationDenied("document_application_" + SECRET))
    assert valid["nodes"][0]["fixed_code"] == "document_application_caller_uid_changed"
    assert invalid["nodes"][0]["kind"] == "ApplicationDenied" and "fixed_code" not in invalid["nodes"][0]
    assert SECRET not in json.dumps(invalid)


@pytest.mark.parametrize("suffix", ["cloudbuild_metadata_read_denied", "run_metadata_read_denied"])
def test_native_audit_conditional_permission_codes_are_source_reviewed(monkeypatch, suffix):
    monkeypatch.syspath_prepend(str(refresh.ROOT / "infra/gcp"))
    from infra.gcp.document_consumed_history import HistoryDenied
    code = "document_consumed_history_" + suffix
    value = refresh._failure_diagnostics(HistoryDenied(code))
    assert value["nodes"][0]["kind"] == "HistoryDenied"
    assert value["nodes"][0]["fixed_code"] == code


@pytest.mark.parametrize("message", [SECRET, "https://vendor.invalid/?key=" + SECRET, "/private/" + SECRET])
def test_unreviewed_first_party_message_is_redacted_on_console_and_disk(monkeypatch, capsys, tmp_path, message):
    output = tmp_path / "private"
    output.mkdir(mode=0o700)
    _, console = cli_failure(monkeypatch, capsys, output, refresh.RefreshDenied(message))
    raw = (output / "failure-diagnostic.json").read_text()
    assert console["fixed_code"] == "document_refresh_unavailable"
    assert SECRET not in raw and "fixed_code" not in json.loads(raw)["diagnostics"]["nodes"][0]


def test_unknown_class_name_and_hooks_are_never_inspected_or_printed(monkeypatch, capsys, tmp_path):
    def forbidden(*_):
        pytest.fail("untrusted exception display hook was invoked")
    cls = type("https://" + SECRET, (RuntimeError,), {"__str__": forbidden,
        "status_code": property(forbidden), "response": property(forbidden)})
    output = tmp_path / "private"
    output.mkdir(mode=0o700)
    _, console = cli_failure(monkeypatch, capsys, output, cls(SECRET))
    raw = (output / "failure-diagnostic.json").read_text()
    assert SECRET not in raw and console["fixed_code"] == "document_refresh_unavailable"
    assert json.loads(raw)["diagnostics"]["nodes"][0]["kind"] == "unclassified"


def test_spoofed_known_class_is_not_a_reviewed_exception_kind():
    cls = type("IdentityDenied", (RuntimeError,), {"__module__": "infra.gcp.document_refresh_auth"})
    value = refresh._failure_diagnostics(cls("document_refresh_service_identity_unverified"))
    assert value["nodes"][0]["kind"] == "unclassified" and "fixed_code" not in value["nodes"][0]


def test_large_untrusted_kind_and_message_do_not_expand_metadata():
    cls = type("x" * 100000, (RuntimeError,), {"__module__": "y" * 100000})
    value = refresh._failure_diagnostics(cls(SECRET * 100000))
    assert value["nodes"][0]["kind"] == "unclassified"
    assert len(json.dumps(value)) < 1000 and SECRET not in json.dumps(value)


@pytest.mark.parametrize("mode", ["full-source-refresh", "reconcile-final-v2-transition"])
def test_unknown_exception_metaclass_cannot_run_display_hooks(monkeypatch, capsys, tmp_path, mode):
    class Meta(type):
        def __hash__(cls):
            pytest.fail("untrusted class hash executed")
        def __eq__(cls, other):
            pytest.fail("untrusted class equality executed")
        def __getattribute__(cls, name):
            if name in {"__name__", "__module__", "__mro__"}:
                pytest.fail("untrusted class metadata hook executed")
            return super().__getattribute__(name)
    class Unknown(RuntimeError, metaclass=Meta):
        pass
    output = tmp_path / "private"
    output.mkdir(mode=0o700)
    _, console = cli_failure(monkeypatch, capsys, output, Unknown(SECRET), mode=mode)
    path = output / ("failure-diagnostic.json" if mode == "full-source-refresh" else "failure.json")
    value = json.loads(path.read_text())
    if mode == "full-source-refresh":
        assert value["diagnostics"]["nodes"][0]["kind"] == "unclassified"
    else:
        assert "diagnostics" not in value
    assert console["fixed_code"] == "document_refresh_unavailable"
    assert SECRET not in json.dumps(value)


@pytest.mark.parametrize("status", [403, 429, 503])
def test_actual_http_error_retains_only_numeric_status(status):
    response = requests.Response()
    response.status_code = status
    response.url = "https://vendor.invalid/" + SECRET
    response.headers["Authorization"] = SECRET
    response._content = SECRET.encode()
    error = requests.HTTPError(SECRET, response=response)
    value = refresh._failure_diagnostics(error)
    assert value["nodes"][0]["http_status"] == status
    assert SECRET not in json.dumps(value) and "response" not in value["nodes"][0]


@pytest.mark.parametrize("status", [True, 99, 600, SECRET, "403 " + SECRET])
def test_invalid_http_status_is_omitted(status):
    response = requests.Response()
    response.status_code = status
    value = refresh._failure_diagnostics(requests.HTTPError(SECRET, response=response))
    assert "http_status" not in value["nodes"][0] and SECRET not in json.dumps(value)


def test_errno_is_read_without_untrusted_property():
    class Unknown(OSError):
        @property
        def errno(self):
            pytest.fail("untrusted errno property called")
    value = refresh._failure_diagnostics(Unknown(28, SECRET))
    assert value["nodes"][0]["errno"] == 28 and SECRET not in json.dumps(value)


def test_external_and_symlink_frame_paths_are_not_retained(monkeypatch, tmp_path):
    root = tmp_path / "root"
    source = root / "backend/scripts"
    source.mkdir(parents=True)
    outside = tmp_path / (SECRET + ".py")
    outside.write_text("raise RuntimeError('" + SECRET + "')")
    link = source / "linked.py"
    link.symlink_to(outside)
    monkeypatch.setattr(refresh, "ROOT", root)
    for path in (outside, link):
        try:
            exec(compile(outside.read_text(), str(path), "exec"), {})
        except RuntimeError as error:
            value = refresh._failure_diagnostics(error)
        assert not value["nodes"][0]["frames"] and SECRET not in json.dumps(value)


def test_chain_cycle_depth_frame_and_metadata_byte_budgets(monkeypatch, tmp_path):
    root = tmp_path / "root"
    path = root / "backend/scripts/source.py"
    path.parent.mkdir(parents=True)
    source = "def recurse(n):\n if n: return recurse(n-1)\n raise RuntimeError('" + SECRET + "')\nrecurse(180)\n"
    path.write_text(source)
    monkeypatch.setattr(refresh, "ROOT", root)
    try:
        exec(compile(source, str(path), "exec"), {})
    except RuntimeError as error:
        head = error
    node = head
    for _ in range(20):
        child = RuntimeError(SECRET * 10000)
        node.__cause__ = child
        node = child
    node.__context__ = head
    value = refresh._failure_diagnostics(head)
    assert len(value["nodes"]) == refresh.FAILURE_DIAGNOSTIC_NODES
    assert sum(len(x["frames"]) for x in value["nodes"]) <= refresh.FAILURE_DIAGNOSTIC_FRAMES
    assert value["truncated"] is True
    raw = json.dumps(value).encode()
    assert len(raw) < refresh.FAILURE_DIAGNOSTIC_BYTES and SECRET.encode() not in raw
    head.__cause__ = head
    cycle = refresh._failure_diagnostics(head)
    assert len(cycle["nodes"]) == 1 and cycle["nodes"][0]["cause"] == 0


@pytest.mark.parametrize("shape", ["absent", "public", "symlink", "ancestor_symlink", "wrong_owner"])
def test_unowned_or_missing_output_is_not_created_or_written(monkeypatch, capsys, tmp_path, shape):
    actual = tmp_path / "actual"
    actual.mkdir(mode=0o700)
    output = tmp_path / "private"
    if shape == "public":
        output.mkdir(mode=0o755)
        # Establish the unsafe fixture even under a restrictive caller umask.
        output.chmod(0o755)
        assert stat.S_IMODE(output.stat().st_mode) == 0o755
    elif shape == "symlink":
        output.symlink_to(actual, target_is_directory=True)
    elif shape == "ancestor_symlink":
        link = tmp_path / "link"
        link.symlink_to(tmp_path, target_is_directory=True)
        output = link / "actual"
    elif shape == "wrong_owner":
        output.mkdir(mode=0o700)
        monkeypatch.setattr(refresh.os, "getuid", lambda: -1)
    calls, console = cli_failure(monkeypatch, capsys, output, RuntimeError(SECRET))
    assert calls[0] == "original operation once" and console["automatic_replay"] is False
    assert not (output / "failure-diagnostic.json").exists() and not (actual / "failure-diagnostic.json").exists()
    if shape == "absent":
        assert not output.exists()


@pytest.mark.parametrize("existing", ["file", "symlink", "directory"])
def test_existing_failure_is_never_replaced(monkeypatch, capsys, tmp_path, existing):
    output = tmp_path / "private"
    output.mkdir(mode=0o700)
    secret = tmp_path / "source"
    secret.write_text(SECRET)
    failure = output / "failure-diagnostic.json"
    if existing == "file":
        failure.write_text("original custody")
    elif existing == "symlink":
        failure.symlink_to(secret)
    else:
        failure.mkdir()
    _, console = cli_failure(monkeypatch, capsys, output, RuntimeError(SECRET))
    assert console["fixed_code"] == "document_refresh_unavailable" and secret.read_text() == SECRET
    if existing == "file":
        assert failure.read_text() == "original custody"
    elif existing == "symlink":
        assert failure.is_symlink()
    else:
        assert failure.is_dir()


def test_directory_replacement_is_refused_before_create(monkeypatch, tmp_path):
    output = tmp_path / "private"
    output.mkdir(mode=0o700)
    monkeypatch.setattr(sys, "argv", ["document_refresh.py", "--mode", "monitor", "--output-directory", str(output)])
    original_open = os.open
    moved = tmp_path / "original"
    def swap(path, flags, *args, **kwargs):
        if path == output:
            output.rename(moved)
            output.mkdir(mode=0o700)
        return original_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(refresh.os, "open", swap)
    assert refresh._record_failure_diagnostics(RuntimeError(SECRET), "document_refresh_unavailable") is False
    assert not (moved / "failure-diagnostic.json").exists() and not (output / "failure-diagnostic.json").exists()


def test_diagnostic_storage_failure_does_not_change_original_outcome_or_call_count(monkeypatch, capsys, tmp_path):
    output = tmp_path / "private"
    output.mkdir(mode=0o700)
    def failed_storage(*_):
        raise OSError(28, SECRET)
    monkeypatch.setattr(refresh, "_record_failure_diagnostics", failed_storage)
    calls, console = cli_failure(monkeypatch, capsys, output, RuntimeError(SECRET))
    assert len(calls) == 2 and console["automatic_replay"] is False


def test_exhausted_metadata_byte_budget_does_not_write_or_change_outcome(monkeypatch, capsys, tmp_path):
    output = tmp_path / "private"
    output.mkdir(mode=0o700)
    monkeypatch.setattr(refresh, "FAILURE_DIAGNOSTIC_BYTES", 1)
    calls, console = cli_failure(monkeypatch, capsys, output, RuntimeError(SECRET))
    assert len(calls) == 2 and console["automatic_replay"] is False
    assert not (output / "failure-diagnostic.json").exists()


@pytest.mark.parametrize("shape", ["missing", "duplicate", "relative"])
def test_unusable_cli_output_selection_does_not_write(monkeypatch, capsys, tmp_path, shape):
    output = tmp_path / "private"
    output.mkdir(mode=0o700)
    argv = ["document_refresh.py", "--mode", "monitor"]
    if shape == "duplicate":
        argv += ["--output-directory", str(output), "--output-directory=" + str(output)]
    elif shape == "relative":
        monkeypatch.chdir(tmp_path)
        argv += ["--output-directory", "private"]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(refresh, "main", lambda: (_ for _ in ()).throw(RuntimeError(SECRET)))
    monkeypatch.setattr(refresh, "emit_status", lambda _: None)
    assert refresh.run_cli() == 2 and not (output / "failure-diagnostic.json").exists()
    assert SECRET not in capsys.readouterr().out


def test_success_has_no_added_diagnostic_or_heartbeat_call(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(refresh, "main", lambda: calls.append("original success once"))
    monkeypatch.setattr(refresh, "emit_status", lambda _: pytest.fail("added heartbeat"))
    monkeypatch.setattr(refresh, "_record_failure_diagnostics", lambda *_: pytest.fail("added diagnostic"))
    assert refresh.run_cli() == 0 and calls == ["original success once"]


@pytest.mark.parametrize("mode", ["audit-consumed-v2-history", "reconcile-final-v2-transition"])
def test_read_only_audited_modes_keep_no_heartbeat_and_existing_receipt_shape(monkeypatch, capsys, tmp_path, mode):
    output = tmp_path / "private"
    output.mkdir(mode=0o700)
    calls, console = cli_failure(monkeypatch, capsys, output, RuntimeError(SECRET), mode=mode)
    assert calls == ["original operation once"] and console["automatic_replay"] is False
    assert not (output / "failure-diagnostic.json").exists()
    path = output / "failure.json"
    if mode == "audit-consumed-v2-history":
        assert not path.exists()
    else:
        assert json.loads(path.read_text()) == console
        assert "diagnostics" not in console


def test_workflow_keeps_diagnostics_out_of_every_native_history_prefix():
    workflow = yaml.safe_load((refresh.ROOT / ".github/workflows/document-refresh.yml").read_text())
    steps = workflow["jobs"]["maintenance"]["steps"]
    normal = next(s for s in steps if s.get("name") == "Retain bounded maintenance evidence")
    diagnostic = next(s for s in steps if s.get("name") ==
                      "Retain sanitized failure diagnostics separately from native custody")
    base = "${{ env.OUTPUT_DIRECTORY }}/"
    assert normal["with"]["path"].splitlines() == [base + "**/*.json", "!" + base + "failure-diagnostic.json"]
    assert diagnostic["with"]["path"] == base + "failure-diagnostic.json"
    assert normal["uses"] == diagnostic["uses"] == "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
    assert normal["if"] == diagnostic["if"] == "always() && env.OUTPUT_DIRECTORY != ''"
    assert diagnostic["with"]["retention-days"] == normal["with"]["retention-days"] == 14
    assert diagnostic["with"]["if-no-files-found"] == "ignore"
    assert diagnostic["with"]["name"] == (
        "document-refresh-diagnostics-${{ env.REFRESH_MODE }}-${{ github.run_id }}-${{ github.run_attempt }}")
    prefixes = set()
    for step in steps:
        prefixes.update(re.findall(r'startswith\("([^"\n]+)"\)', step.get("run", "")))
    assert prefixes == {"document-refresh-refresh-", "document-refresh-full-source-refresh-",
                        "document-refresh-resume-full-source-refresh-"}
    for mode in ("refresh", "full-source-refresh", "resume-full-source-refresh", "monitor",
                 "audit-consumed-v2-history", "reconcile-final-v2-transition"):
        name = f"document-refresh-diagnostics-{mode}-123456-1"
        assert all(not name.startswith(prefix) for prefix in prefixes)
    # Native evidence is still retained under its exact original prefixes.
    for mode in ("refresh", "full-source-refresh", "resume-full-source-refresh"):
        assert sum(f"document-refresh-{mode}-123456-1".startswith(prefix) for prefix in prefixes) == 1
