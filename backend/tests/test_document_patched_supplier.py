"""Pinned supplier integrity and engine mismatches; no native/network calls."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from infra.gcp import document_worker as worker

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


supplier = load("patched_supplier", "infra/document-processing/clamav_supplier.py")
processor = load("patched_supplier_processor", "infra/document-processing/processor.py")


def pinned_files():
    value = supplier.release()
    rows = {row["path"]: row for row in value["runtime_files"] if row["kind"] == "file"}
    return [{"path": path, "sha256": rows[path]["sha256"], "size": rows[path]["size"], "elf_machine": 62}
            for path in value["native_manifest_files"]]


def test_official_release_and_all_selected_files_are_pinned():
    value = supplier.release()
    assert value["engine"] == "1.4.7" and value["package"]["architecture"] == "amd64"
    assert value["package"]["sha256"] == "3bbb8ac72e9f9238d51ec6d52d8c34ab78967061ce6d15908fcbe4c4fb2bea3a"
    assert sum(row.get("size", 0) for row in value["runtime_files"]) == 38463824
    assert not any(row["path"].endswith(".a") or "/include/" in row["path"] or "/doc/" in row["path"]
                   for row in value["runtime_files"])
    assert value["signature_notice"]["used_as_strong_authentication"] is False
    assert value["signature_notice"]["weak_digest_override_permitted"] is False
    assert supplier.matches_files(pinned_files())


@pytest.mark.parametrize("change", ["bytes", "path", "architecture", "count", "order"])
def test_native_manifest_cannot_substitute_an_engine(change):
    files = pinned_files()
    if change == "bytes":
        files[0]["sha256"] = "f" * 64
    elif change == "path":
        files[0]["path"] = "/usr/sbin/clamd"
    elif change == "architecture":
        files[0]["elf_machine"] = 183
    elif change == "count":
        files.pop()
    else:
        files.reverse()
    assert not supplier.matches_files(files)
    assert not processor._vendor_matches(files)


@pytest.mark.parametrize("engine", ["1.4.3", "1.4.6", "1.4.7", "1.4.7-wrapper"])
def test_installed_manifest_requires_exact_patched_version_and_vendor_bytes(monkeypatch, engine):
    files = pinned_files()
    by_path = {row["path"]: row for row in files}
    library = Path(files[2]["path"])
    monkeypatch.setattr(processor.Path, "glob", lambda *_: [library])
    monkeypatch.setattr(processor.Path, "resolve", lambda self, **_: self)
    monkeypatch.setattr(processor, "_native_file", lambda path: copy.deepcopy(by_path[str(path)]))
    monkeypatch.setattr(processor, "_command", lambda *_args, **_kwargs: (0, "ClamAV " + engine))
    if engine == "1.4.7":
        observed = processor._installed_clamd()
        assert observed == {"version": 1, "engine": "1.4.7", "files": files}
    else:
        with pytest.raises(processor.InspectionUnavailable):
            processor._installed_clamd()


def test_modified_package_stops_before_prune_install_or_native_invocation(tmp_path, monkeypatch):
    package = tmp_path / "wrong.deb"
    package.write_bytes(b"altered supplier bytes")
    value = supplier.release()
    value["package"].update(size=package.stat().st_size, sha256="a" * 64)
    monkeypatch.setattr(supplier, "release", lambda: value)
    monkeypatch.setattr(supplier.os, "getuid", lambda: 0)
    monkeypatch.setattr(supplier.platform, "system", lambda: "Linux")
    monkeypatch.setattr(supplier.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(supplier, "preservation_snapshot", lambda: {"original-font": "preserved"})
    monkeypatch.setattr(supplier, "prune_old_engine", lambda: pytest.fail("Unverified package reached system mutation"))
    monkeypatch.setattr(supplier.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("Unexpected native operation"))
    with pytest.raises(supplier.SupplierError):
        supplier.install(package)


def test_pruning_refuses_any_renderer_or_font_package_removal(monkeypatch):
    calls = []
    def run(args, **_kwargs):
        calls.append(args)
        if args[0] == "dpkg-query":
            return SimpleNamespace(returncode=0, stdout=b"installed")
        assert args[:2] == ["apt-get", "--simulate"]
        return SimpleNamespace(returncode=0, stdout=b"Purg clamav [1.4.6]\nRemv libreoffice-writer [25.2]\n")
    monkeypatch.setattr(supplier.subprocess, "run", run)
    with pytest.raises(supplier.SupplierError):
        supplier.prune_old_engine()
    assert all(args[0] == "dpkg-query" or "--simulate" in args for args in calls)


def test_release_configuration_tampering_is_rejected(tmp_path, monkeypatch):
    raw = (supplier.HERE / "clamav-release.json").read_bytes()
    value = json.loads(raw)
    value["engine"] = "1.4.3"
    (tmp_path / "clamav-release.json").write_text(json.dumps(value))
    monkeypatch.setattr(supplier, "HERE", tmp_path)
    with pytest.raises(supplier.SupplierError):
        supplier.release()
    assert hashlib.sha256(raw).hexdigest() == supplier.RELEASE_SHA256


def test_supplier_failure_diagnostic_never_exposes_exception_values(monkeypatch, capsys):
    def fail(_package):
        raise RuntimeError("private-token-and-path-must-not-escape")
    monkeypatch.setattr(supplier, "install", fail)
    monkeypatch.setattr("sys.argv", ["clamav_supplier.py", "--install"])
    assert supplier.main() == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"error": "document_clamav_supplier_unverified", "stage": "setup"}
    assert captured.err == "" and "private-token" not in captured.out


def test_vendor_version_reporting_uses_owned_config_without_starting_a_daemon(monkeypatch):
    calls = []
    def run(arguments, **_kwargs):
        calls.append(arguments)
        valid = (arguments[0] == "/usr/local/bin/clamscan"
                 or arguments[0].endswith("/freshclam") and "--config-file=/etc/clamav/freshclam.conf" in arguments
                 or arguments[0].endswith("/clamd") and "--config-file=/dev/null" in arguments)
        return SimpleNamespace(returncode=0 if valid else 2, stdout=b"ClamAV 1.4.7\n" if valid else b"",
                               stderr=b"" if valid else b"Missing vendor default configuration")
    monkeypatch.setattr(supplier.subprocess, "run", run)
    supplier.verify_installation({"runtime_files": []})
    assert len(calls) == 3 and all("--version" in args for args in calls)


def test_retained_v5_shape_is_read_only_and_cannot_claim_current_source(monkeypatch):
    from infra.gcp import document_source_candidate_v5 as v5

    monkeypatch.setattr(v5, "working_snapshot", lambda *_: pytest.fail("Historical shape requested current source"))
    image = worker.ACCEPTED_IMAGE
    value = worker.render(image, "a" * 40, retained_source_v5=True)
    assert value["retained_source_v5"] is True and value["current_source_qualified"] is False
    assert value["api_wiring_enabled"] is False and value["runtime_isolation_proven"] is False
    env = value["service"]["template"]["containers"][0]["env"]
    assert env[1]["value"] == v5.POLICY_SHA256
    assert {"name": v5.TRANSPORT_ENV, "value": v5.TRANSPORT_MODE} in env
    for other in ["candidate_v3", "source_revision", "source_v5", "source_patched_engine"]:
        with pytest.raises(worker.ConfigurationDenied):
            worker.render(image, "a" * 40, retained_source_v5=True, **{other: True})
