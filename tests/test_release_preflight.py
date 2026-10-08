"""Run the actual shell entry point; a local gcloud sentinel forbids cloud access."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1]
PROJECT = "ai-resume-parser-482412"


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-c", "core.hooksPath=/dev/null", "-C", str(root), *args],
        stderr=subprocess.DEVNULL,
    ).decode().strip()


def commit(root: Path) -> str:
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "Synthetic release source")
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def checkout(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "source"
    scripts = root / "infra/gcp"
    scripts.mkdir(parents=True)
    for name in ("release.sh", "release_preflight.py"):
        shutil.copyfile(SOURCE / "infra/gcp" / name, scripts / name)
    migrations = root / "backend/alembic/versions"
    migrations.mkdir(parents=True)
    for source in (SOURCE / "backend/alembic/versions").glob("*.py"):
        # Only the real reviewed0009 ancestry belongs to the positive fixture.
        if "0010" not in source.name:
            shutil.copyfile(source, migrations / source.name)
    shutil.copyfile(SOURCE / "backend/alembic.ini", root / "backend/alembic.ini")
    (root / "README.md").write_text("Synthetic source; no accounts or credentials.\n")
    git(root, "init", "-q")
    git(root, "config", "user.name", "Synthetic release proof")
    git(root, "config", "user.email", "proof@example.invalid")
    git(root, "config", "commit.gpgsign", "false")
    commit(root)
    tools = tmp_path / "sentinel-bin"
    tools.mkdir()
    sentinel = tools / "gcloud"
    sentinel.write_text('#!/bin/sh\nprintf "called\\n" >> "$GCLOUD_SENTINEL"\nexit 97\n')
    sentinel.chmod(0o755)
    return root, tools, tmp_path / "cloud-calls"


def run_release(checkout: tuple[Path, Path, Path], release: str | None = None, **extra: str) -> subprocess.CompletedProcess[str]:
    root, tools, sentinel = checkout
    release = release or git(root, "rev-parse", "HEAD")
    environment = {**os.environ, **extra, "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}", "GCLOUD_SENTINEL": str(sentinel)}
    return subprocess.run(
        ["bash", str(root / "infra/gcp/release.sh"), PROJECT, "us-central1",
         f"us-central1-docker.pkg.dev/{PROJECT}/cloud-run-source-deploy/hirewiz:{release}", release,
         f"gs://{PROJECT}-hirewiz-application-artifacts/releases/synthetic.dump"],
        cwd=root, env=environment, capture_output=True, text=True, timeout=10, check=False,
    )


def refused(checkout: tuple[Path, Path, Path], **extra: str) -> str:
    result = run_release(checkout, **extra)
    assert result.returncode == 65, result.stdout + result.stderr
    assert "Release preflight refused" in result.stderr
    assert not checkout[2].exists(), "Refusal must precede all gcloud calls, including metadata reads"
    return result.stderr


def add_revision(root: Path, revision: str, parent: str, name: str = "renamed_migration.py") -> Path:
    target = root / "backend/alembic/versions" / name
    target.write_text(
        f'revision = "{revision}"\ndown_revision = "{parent}"\nbranch_labels = None\ndepends_on = None\n'
        'def upgrade():\n    raise RuntimeError("Upgrade code must never execute during preflight")\n'
    )
    return target


def test_clean_exact_0009_source_reaches_only_first_cloud_sentinel(checkout):
    result = run_release(checkout)
    assert result.returncode == 97
    assert "20261008_0009" in result.stdout
    assert "writer retirement is not asserted" in result.stdout
    assert checkout[2].read_text() == "called\n"


def test_release_mismatch_calls_no_cloud(checkout):
    refused(checkout, release="0" * 40)


def test_tracked_source_change_calls_no_cloud(checkout):
    (checkout[0] / "README.md").write_text("Changed outside the migration directory.\n")
    refused(checkout)


def test_untracked_monetary_source_calls_no_cloud(checkout):
    add_revision(checkout[0], "20261009_0010", "20261008_0009")
    refused(checkout)


@pytest.mark.parametrize("revision,parent", [
    ("20261009_0010", "20261008_0009"),
    ("20261010_0011", "20261008_0009"),
    ("alternative_head", "20261008_0008"),
    ("20261008_0009", "20261008_0008"),
    ("orphan_head", "missing_parent"),
])
def test_committed_dynamic_migration_dag_refuses_unsafe_heads(checkout, revision, parent):
    add_revision(checkout[0], revision, parent)
    commit(checkout[0])
    refused(checkout)


def test_disconnected_cycle_cannot_hide_behind_0009_head(checkout):
    add_revision(checkout[0], "cycle_one", "cycle_two", "cycle_one.py")
    add_revision(checkout[0], "cycle_two", "cycle_one", "cycle_two.py")
    commit(checkout[0])
    refused(checkout)


def test_ignored_monetary_migration_is_not_clean_source(checkout):
    (checkout[0] / ".gitignore").write_text("backend/alembic/versions/renamed_migration.py\n")
    commit(checkout[0])
    add_revision(checkout[0], "20261009_0010", "20261008_0009")
    assert not git(checkout[0], "status", "--porcelain")
    refused(checkout)


def test_assume_unchanged_cannot_hide_source_bytes(checkout):
    git(checkout[0], "update-index", "--assume-unchanged", "README.md")
    (checkout[0] / "README.md").write_text("Hidden changed source.\n")
    assert not git(checkout[0], "status", "--porcelain")
    refused(checkout)


def test_dynamic_declaration_is_denied_without_execution(checkout, tmp_path):
    marker = tmp_path / "must-not-execute"
    path = add_revision(checkout[0], "20261009_0010", "20261008_0009")
    path.write_text(path.read_text().replace(
        'revision = "20261009_0010"',
        f"revision = __import__('pathlib').Path({str(marker)!r}).write_text('executed')",
    ))
    commit(checkout[0])
    refused(checkout)
    assert not marker.exists()


@pytest.mark.parametrize("shadow", [
    'if True:\n    revision = "20261009_0010"\n',
    'from os import environ as revision\n',
    'def revision():\n    return "20261009_0010"\n',
])
def test_module_flow_cannot_replace_literal_migration_metadata(checkout, shadow):
    path = next((checkout[0] / "backend/alembic/versions").glob("*0009*.py"))
    path.write_text(path.read_text() + "\n" + shadow)
    commit(checkout[0])
    refused(checkout)


@pytest.mark.parametrize("old,replacement", [
    ('revision = "20261009_0010"', 'revision = "20261009_0010"\nrevision = "20261008_0009"'),
    ('revision = "20261009_0010"', 'revision = {[]}'),
    ('down_revision = "20261008_0009"', 'down_revision = ("20261008_0008", "20261008_0009")'),
    ('branch_labels = None', 'branch_labels = "another_branch"'),
    ('depends_on = None', 'depends_on = "20261008_0007"'),
])
def test_ambiguous_or_branched_declarations_call_no_cloud(checkout, old, replacement):
    path = add_revision(checkout[0], "20261009_0010", "20261008_0009")
    # Duplicate assignments, merges, labels and dependencies all require review.
    path.write_text(path.read_text().replace(old, replacement))
    commit(checkout[0])
    refused(checkout)


def test_alternate_alembic_discovery_is_not_implicitly_allowed(checkout):
    configuration = checkout[0] / "backend/alembic.ini"
    configuration.write_text(configuration.read_text().replace("%(here)s/alembic", "elsewhere"))
    commit(checkout[0])
    refused(checkout)


def test_archive_without_git_identity_calls_no_cloud(checkout):
    release = git(checkout[0], "rev-parse", "HEAD")
    shutil.rmtree(checkout[0] / ".git")
    refused(checkout, release=release)


def test_boolean_and_local_json_retirement_assertions_cannot_waive_refusal(checkout):
    add_revision(checkout[0], "20261009_0010", "20261008_0009")
    proof = checkout[0] / "claimed-retirement.json"
    proof.write_text('{"approved":true,"all_writers_retired":true}')
    commit(checkout[0])
    refused(checkout, ALLOW_0010="true", GENERATION_QUIESCED="true", QUIESCE_PROOF=str(proof))


def test_git_environment_cannot_supply_another_identity(checkout, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q")
    result = run_release(checkout, GIT_DIR=str(other / ".git"), GIT_WORK_TREE=str(other))
    assert result.returncode == 97
    assert checkout[2].read_text() == "called\n"
