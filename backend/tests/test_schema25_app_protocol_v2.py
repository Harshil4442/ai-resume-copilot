"""Installed SDK/wire + owned-emulator qualification, never cloud/IAM/capacity proof."""
from __future__ import annotations

import importlib.metadata
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from xml.etree import ElementTree

import pytest

from scripts import schema25_app_release_contract_v2 as contract

ROOT = Path(__file__).resolve().parents[2]
SDK_PACKAGES = ("google-cloud-firestore", "google-api-core", "google-cloud-storage",
                "google-auth", "proto-plus", "protobuf", "grpcio", "redis", "limits")
SUITES = contract.PROTOCOL_SUITES


def test_exact_installed_native_dependencies_and_reviewed_transport_source():
    locked = {row["name"]: row["version"] for row in tomllib.loads((ROOT / "backend/uv.lock").read_text())["package"]}
    assert all(importlib.metadata.version(name) == locked[name] for name in SDK_PACKAGES)
    sys.path.insert(0, str(ROOT / "infra/gcp"))
    import unified_credit_schema25_release as release
    assert contract.source_fingerprint(ROOT, release.prior_app_files(ROOT, contract.PRIOR_RELEASE))
    assert all((ROOT / name).is_file() for name in contract.TRANSPORT_PATHS)


def test_installed_gapic_redis_and_data_emulator_protocol_selection_has_no_skipped_cases():
    required = os.getenv("HIREWIZ_APP_COMPATIBILITY_REQUIRED") == "true"
    if os.getenv("HIREWIZ_AUTHORITY_EMULATOR_REQUIRED") != "true":
        if required:
            pytest.fail("The mandatory v2 native qualification needs the owned CI data emulator")
        pytest.skip("Run the mandatory qualification in the explicit owned emulator context")
    with tempfile.TemporaryDirectory(prefix="hirewiz-app25-v2-native-") as temporary:
        path = Path(temporary)
        path.chmod(0o700)
        report = path / "junit.xml"
        result = subprocess.run([sys.executable, "-I", "-B", "-m", "pytest", "--noconftest", "-q", *SUITES,
            "--junitxml", str(report)], cwd=ROOT / "backend",
            env={**os.environ, "APP_ENV": "test", "HIREWIZ_AUTHORITY_EMULATOR_REQUIRED": "true",
                 "HIREWIZ_METADATA_PACING_REQUIRED": "true"}, capture_output=True, timeout=240)
        # Do not export raw request/log/fixture content; the exact command/source
        # and finite failure/skip counts are sufficient to refuse qualification.
        status = result.returncode
        assert status == 0, "Native protocol subprocess failed; inspect privately"
        assert report.is_file() and report.stat().st_size <= 4 * 1024**2
        cases = ElementTree.parse(report).findall(".//testcase")
        assert cases and not any(case.find("skipped") is not None or case.find("failure") is not None
                                 or case.find("error") is not None for case in cases)

