"""Pinned font packaging checks; native Linux rendering remains a separate gate."""
import importlib.util
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "backend/evals/assets/courier_prime"


def module(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    reviewed = module(ROOT / "infra/document-processing/courier_prime_fonts.py")
    fonts, notices = tmp_path / "fonts", tmp_path / "notices"
    fonts.mkdir()
    notices.mkdir()
    for name in (*reviewed.PINS, "provenance.json"):
        shutil.copyfile(ASSETS / name, (fonts if name.endswith(".ttf") else notices) / name)
    calls = []

    def match(args, **kwargs):
        assert args[:2] == ["/usr/bin/fc-match", "--format=%{family}\n%{file}\n"]
        assert kwargs == {"env": {"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
                          "capture_output": True, "timeout": 5, "check": False}
        name = next(name for name, style in reviewed.STYLES.items() if args[2] == "Courier Prime:" + style)
        calls.append(name)
        return SimpleNamespace(returncode=0, stderr=b"", stdout=("Courier Prime\n" + str(fonts / name) + "\n").encode())

    monkeypatch.setattr(reviewed.subprocess, "run", match)
    return reviewed, fonts, notices, calls


def test_exact_unmodified_fonts_and_license_match_official_pins(bundle):
    reviewed, fonts, notices, calls = bundle
    reviewed.verify_bundle(fonts, notices)
    assert len(calls) == 4 and set(calls) == set(reviewed.STYLES)


@pytest.mark.parametrize("change", ["font_bytes", "license_bytes", "missing_license", "provenance", "symlink"])
def test_changed_font_or_license_refuses_before_resolution(bundle, change):
    reviewed, fonts, notices, calls = bundle
    if change == "font_bytes":
        (fonts / "CourierPrime-Regular.ttf").write_bytes(b"substituted-font")
    elif change == "license_bytes":
        (notices / "OFL.txt").write_bytes(b"different-license")
    elif change == "missing_license":
        (notices / "OFL.txt").unlink()
    elif change == "provenance":
        path = notices / "provenance.json"
        data = json.loads(path.read_text())
        data["modified"] = True
        path.write_text(json.dumps(data))
    else:
        target = fonts / "CourierPrime-Regular.ttf"
        target.unlink()
        target.symlink_to(ASSETS / target.name)
    with pytest.raises((ValueError, FileNotFoundError), match="courier_prime|OFL"):
        reviewed.verify_bundle(fonts, notices)
    assert calls == []


@pytest.mark.parametrize("output", [b"Liberation Mono\n/fallback.ttf\n", b"Courier Prime\n/wrong-copy.ttf\n"])
def test_fontconfig_substitution_is_refused(bundle, monkeypatch, output):
    reviewed, fonts, notices, _calls = bundle
    monkeypatch.setattr(reviewed.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout=output, stderr=b""))
    with pytest.raises(ValueError, match="courier_prime_bundle_unverified"):
        reviewed.verify_bundle(fonts, notices)


def test_font_notice_and_verifier_are_in_exact_build_context_and_runtime_attestation():
    reviewed = module(ROOT / "infra/document-processing/courier_prime_fonts.py")
    builder = module(ROOT / "backend/scripts/document_test_image.py")
    source = "infra/document-processing/courier_prime_fonts.py"
    assert source in builder.SOURCES and builder.RUNTIME[source] == "/opt/document/courier_prime_fonts.py"
    hashes = builder.source_hashes()
    for name in (*reviewed.PINS, "provenance.json"):
        key = "backend/evals/assets/courier_prime/" + name
        assert key in builder.SOURCES and key in hashes
        destination = reviewed.FONT_ROOT if name.endswith(".ttf") else reviewed.NOTICE_ROOT
        assert builder.RUNTIME[key] == str(destination / name)
    assert not any("times" in path.lower() for path in builder.SOURCES)
