"""Typed current source revision; historical V2/V3 policies remain immutable.

This is source identity, never an image/build/native acceptance or deployment.
The Build entry requires committed, clean Git bytes; historical readback binds
the exact retained commit and the generation-bound source archive independently.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from infra.gcp import document_candidate as courier

ROOT = Path(__file__).resolve().parents[2]
LEGACY_KIND = "native_cloud_build_source_revision_v1"
KIND = "native_cloud_build_source_fixed_renderer_v1"
LEGACY_DEFINITIONS_KIND = "native_cloud_build_source_definitions_v1"
DEFINITIONS_KIND = "native_cloud_build_fixed_renderer_definitions_v1"
LEGACY_POLICY_PATH = "infra/document-processing/policy.source-revision.json"
POLICY_PATH = "infra/document-processing/policy.source-fixed-renderer.json"
DOCKERFILE = "infra/document-processing/source-fixed-renderer.Dockerfile"
SOURCES = frozenset({*courier.SOURCES, DOCKERFILE})
LEGACY_POLICY_SHA256 = "6a080773cc96cd7eca35160ef2e20e299d8dc458e8696b782f40b722d30c2fb1"
POLICY_SHA256 = "22a012f2fb7b6d3b696d8772563e9363566b9f03fb83385cfeffbc84a26459eb"
READ_POLICIES = frozenset({POLICY_SHA256, LEGACY_POLICY_SHA256})
READ_KINDS = frozenset({KIND, DEFINITIONS_KIND, LEGACY_KIND, LEGACY_DEFINITIONS_KIND})
CONTRACT_VERSION = 4
LEGACY_CONTRACT_PATHS = ("infra/gcp/document_source_candidate.py", "infra/gcp/document_source_payload.py",
                  "infra/gcp/document_source_receipt.py", "infra/gcp/document_source_release.py",
                  "backend/scripts/document_test_image.py", "backend/scripts/document_refresh.py",
                  "infra/gcp/document_candidate_probe.py", "infra/gcp/document_candidate_release.py",
                  "infra/gcp/document_refresh_transition.py", "infra/gcp/document_worker.py",
                  "infra/gcp/document_refresh_probe.py", "infra/gcp/document_refresh_recovery.py",
                  "infra/gcp/document_application_context.py")
CONTRACT_PATHS = (*LEGACY_CONTRACT_PATHS, "infra/gcp/fresh_monetary_release.py")
BASE_PAYLOAD_SHA256 = "bd3d526e421356527cffd1660170bca9bedb3eae2b3959c6ae5d25cb83b62a32"
# Reconstruct exact historical bytes, not new execution or a claim about the
# historical Build's Git provenance. Every member is checked against frozen V3.
FROZEN_V3_SOURCE_COMMIT = "ab2deadd1b1cecbe9cb10c73806dbcde851bf5e2"


class SourceDenied(RuntimeError):
    pass


def need(value, code):
    if not value:
        raise SourceDenied("document_source_revision_" + code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def git(root, *arguments):
    result = subprocess.run(["git", *arguments], cwd=root, stdin=subprocess.DEVNULL,
        capture_output=True, timeout=15, check=False)
    need(result.returncode == 0 and len(result.stdout) <= 4 * 1024**2, "git_unavailable")
    return result.stdout


def frozen_v3(root=ROOT):
    path = root / courier.POLICY_PATH
    need(not path.is_symlink() and path.is_file(), "historical_policy_changed")
    raw = path.read_bytes()
    need(digest(raw) == courier.POLICY_SHA256, "historical_policy_changed")
    value = json.loads(raw)
    need(set(value["source_sha256"]) == set(courier.SOURCES), "historical_inventory_changed")
    return raw, value


def validate_policy(raw, root=ROOT, *, legacy=False):
    need(type(legacy) is bool, "profile_invalid")
    need(type(raw) is bytes and 0 < len(raw) <= 256 * 1024 and digest(raw) == (LEGACY_POLICY_SHA256 if legacy else POLICY_SHA256),
         "policy_changed")
    value = json.loads(raw)
    _, previous = frozen_v3(root)
    need({key: row for key, row in value.items() if key != "source_sha256"}
         == {key: row for key, row in previous.items() if key != "source_sha256"},
         "policy_semantics_changed")
    sources = value.get("source_sha256")
    need(type(sources) is dict and set(sources) == set(courier.SOURCES if legacy else SOURCES)
         and all(type(row) is str and re.fullmatch(r"[a-f0-9]{64}", row) for row in sources.values()),
         "inventory_changed")
    return sources


@dataclass(frozen=True)
class Source:
    commit: str
    sources: dict[str, str]
    policy_raw: bytes
    qualification_sha256: str
    legacy: bool = False

    @property
    def kind(self):
        return LEGACY_KIND if self.legacy else KIND

    @property
    def policy_path(self):
        return LEGACY_POLICY_PATH if self.legacy else POLICY_PATH

    @property
    def dockerfile(self):
        return courier.DOCKERFILE if self.legacy else DOCKERFILE

    @property
    def policy(self):
        return digest(self.policy_raw)

    @property
    def runtime(self):
        return {target: self.sources[name] for name, target in courier.RUNTIME.items()}

    @property
    def identity(self):
        return {"kind": self.kind, "source_commit": self.commit, "policy_sha256": self.policy,
                "qualification_sha256": self.qualification_sha256, "source_sha256": self.sources}


def committed(commit=None, *, root=ROOT, clean=False, legacy=False):
    need(type(legacy) is bool and not (legacy and clean), "legacy_write_refused")
    observed = git(root, "rev-parse", "HEAD").decode().strip()
    commit = observed if commit is None else commit
    need(type(commit) is str and re.fullmatch(r"[a-f0-9]{40}", commit), "commit_invalid")
    if clean:
        need(commit == observed and not git(root, "status", "--porcelain", "--untracked-files=no"),
             "committed_clean_source_required")
    raw = git(root, "show", commit + ":" + (LEGACY_POLICY_PATH if legacy else POLICY_PATH))
    sources = validate_policy(raw, root, legacy=legacy)
    for name, expected in sources.items():
        content = git(root, "show", commit + ":" + name)
        need(digest(content) == expected, "git_source_changed")
        if clean:
            path = root / name
            need(path.is_file() and not path.is_symlink() and path.read_bytes() == content,
                 "working_source_changed")
    contracts = {name: digest(git(root, "show", commit + ":" + name)) for name in (LEGACY_CONTRACT_PATHS if legacy else CONTRACT_PATHS)}
    if clean:
        need(all(digest((root / name).read_bytes()) == expected for name, expected in contracts.items()),
             "working_contract_changed")
    return Source(commit, sources, raw, digest(canonical(contracts)), legacy)


def working_snapshot(root=ROOT):
    """Offline preparation only; this does not grant Build execution authority."""
    raw = (root / POLICY_PATH).read_bytes()
    sources = validate_policy(raw, root)
    contents = {name: (root / name).read_bytes() for name in sources}
    need(all(not (root / name).is_symlink() and digest(content) == sources[name]
             for name, content in contents.items()), "working_source_changed")
    contents[POLICY_PATH] = raw
    need(sum(map(len, contents.values())) <= 4 * 1024**2, "source_archive_bound")
    return contents, sources, digest(raw)


def historical_v3_contents(root=ROOT):
    raw, policy = frozen_v3(root)
    contents = {name: git(root, "show", FROZEN_V3_SOURCE_COMMIT + ":" + name)
                for name in policy["source_sha256"]}
    need(all(digest(content) == policy["source_sha256"][name] for name, content in contents.items()),
         "historical_source_changed")
    contents[courier.POLICY_PATH] = raw
    return contents


def definition_recipe(parent_image, root=ROOT):
    need(type(parent_image) is str and re.fullmatch(
        r"us-central1-docker\.pkg\.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-document-inspector@sha256:[a-f0-9]{64}",
        parent_image) and parent_image != courier.IMAGE, "definition_parent_invalid")
    raw = (root / courier.REFRESH_DOCKERFILE).read_bytes()
    need(digest(raw) == courier.REFRESH_SHA256 and raw.count(courier.IMAGE.encode()) == 2,
         "definition_recipe_changed")
    return raw.replace(courier.IMAGE.encode(), parent_image.encode())


def require_working_contract(source, root=ROOT):
    need(not source.legacy, "legacy_write_refused")
    expected = {name: digest(git(root, "show", source.commit + ":" + name)) for name in CONTRACT_PATHS}
    need(digest(canonical(expected)) == source.qualification_sha256
         and all(not (root / name).is_symlink() and digest((root / name).read_bytes()) == stamp
                 for name, stamp in expected.items())
         and all(not (root / name).is_symlink() and digest((root / name).read_bytes()) == stamp
                 for name, stamp in source.sources.items()), "working_contract_changed")
