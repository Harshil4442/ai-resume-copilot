"""Code-only source fixtures for the frozen native acquisition protocols."""
from __future__ import annotations

import io
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
FROZEN_SOURCE = "45b3e808c88d78c1d7d182cd4af4482df25c13f0"
FROZEN_APP_SHA256 = "f2775c3b9f8d8df1f4f3d97abbe190cf5d2eb46b87beeb942b5252c3840d8670"
FROZEN_APP_FILES = 290
FROZEN_FINGERPRINT = "ae56969b6a6aeb41d6b8202deca891c839e3f8564efc5743457777f2c1bd8fcb"
HELPER = "backend/scripts/qualify_native_quality_acquisition.py"
COMPATIBILITY = "backend/scripts/schema25_app_release_contract_v3.py"
ENGINE = "backend/evaluation/native_acquisition_v2/engine.py"
TRANSPORT = "backend/evaluation/development40_transport_v2.py"
SEMANTIC_ENTRY = "backend/scripts/native_acquisition_job.py"


def local_git(root, *args):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(root), "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_NO_LAZY_FETCH": "1",
           "GIT_NO_REPLACE_OBJECTS": "1"}
    return subprocess.run(["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
        "-C", str(root), *args], env=env, stdin=subprocess.DEVNULL, capture_output=True,
        check=True, timeout=30).stdout


def frozen_archive(target, paths):
    """Copy only explicit tracked paths from the exact retained commit."""
    raw = local_git(ROOT, "archive", "--format=tar", FROZEN_SOURCE, "--", *paths)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        assert all(member.isfile() or member.isdir() for member in archive.getmembers())
        archive.extractall(target, filter="data")


@pytest.fixture(scope="session")
def frozen_runtime_layout(tmp_path_factory):
    target = tmp_path_factory.mktemp("frozen-native-runtime")
    frozen_archive(target, (ENGINE, HELPER, COMPATIBILITY, SEMANTIC_ENTRY, TRANSPORT))
    return target


@pytest.fixture(scope="session")
def frozen_controller_root(tmp_path_factory):
    target = tmp_path_factory.mktemp("frozen-native-source") / "source"
    local_git(ROOT, "clone", "--quiet", "--shared", "--no-checkout", str(ROOT), str(target))
    local_git(target, "reset", "--soft", FROZEN_SOURCE)
    local_git(target, "read-tree", "--empty")
    # These paths cover the source guard, app fingerprint, protocol and public
    # font dependencies. No evaluation manifest, golden case or corpus is read.
    frozen_archive(target, (".dockerignore", "Dockerfile", ".github/workflows/ci.yml",
        ".github/workflows/stage5-owned-capacity.yml", "backend/alembic.ini",
        "backend/alembic", "backend/app", "backend/capacity/stage5-owned-http-v1.json",
        "backend/evaluation", "backend/openapi.json",
        "backend/pyproject.toml", "backend/uv.lock", "backend/scripts", "backend/tests",
        "backend/evals/assets/courier_prime", "browser-companion", "docs/runbooks", "frontend/app",
        "frontend/components", "frontend/instrumentation-client.ts", "frontend/lib", "frontend/next.config.mjs",
        "frontend/package.json", "frontend/package-lock.json", "frontend/public/downloads", "frontend/tsconfig.json",
        "frontend/vitest.config.mts", "frontend/vitest.setup.ts", "infra"))
    local_git(target, "add", "--all")
    local_git(target, "-c", "commit.gpgsign=false", "-c", "user.name=Owned native source test",
              "-c", "user.email=owned@example.invalid", "commit", "-qm", "Frozen native code only")
    assert local_git(target, "rev-parse", "HEAD^").decode().strip() == FROZEN_SOURCE
    assert not local_git(target, "status", "--porcelain", "--untracked-files=all")
    assert {path.name for path in (target / "backend/evals").iterdir()} == {"assets"}
    return target
