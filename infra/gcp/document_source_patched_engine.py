"""Distinct reviewed ClamAV 1.4.7 source identity, retaining wire contract five.

The old synchronous profile remains a structural predecessor. New source bytes,
policy, image, Build and fresh native evidence must all agree independently.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from infra.gcp import document_candidate as courier
from infra.gcp import document_source_candidate as base
from infra.gcp import document_source_candidate_v5 as predecessor

ROOT = Path(__file__).resolve().parents[2]
ENGINE_VERSION = "1.4.7"
CONTRACT_VERSION, REQUIRED_CASE_COUNT = 5, 17
KIND = "native_cloud_build_source_patched_engine_v1"
POLICY_PATH = "infra/document-processing/policy.source-patched-engine.json"
DOCKERFILE = "infra/document-processing/source-patched-engine.Dockerfile"
POLICY_SHA256 = "824d31150da47d5f058aaa289aea8479d185cc617ce7d0e6916aaaae8e52bad3"
TRANSPORT_ENV, TRANSPORT_MODE = predecessor.TRANSPORT_ENV, predecessor.TRANSPORT_MODE
RUNTIME = courier.RUNTIME | {
    "infra/document-processing/clamav_supplier.py": "/opt/document/clamav_supplier.py",
    "infra/document-processing/clamav-release.json": "/opt/document/clamav-release.json",
}
SOURCES = predecessor.SOURCES | {DOCKERFILE, *set(RUNTIME) - set(courier.RUNTIME)}
CONTRACT_PATHS = (*predecessor.CONTRACT_PATHS,
    "infra/gcp/document_source_patched_engine.py", "infra/gcp/document_patched_engine_release.py",
    "infra/gcp/schema27_patched_engine_recovery.py", "infra/gcp/document_consumed_native.py",
    "infra/gcp/schema27_mixed_maintenance.py", "infra/gcp/schema27_mixed_policy_successor.py",
    "infra/gcp/schema27_native_admission_closure.py", "infra/gcp/document_cloud_build_qa.py",
    "infra/document-processing/cloud-build-qa.sh", "infra/gcp/schema27_native_admission_completion.py")
_CHANGED = {courier.DOCKERFILE, "infra/document-processing/processor.py"}
git = base.git


def need(value, code):
    base.need(value, "patched_engine_" + code)


def _read(root, name):
    return predecessor._read(root, name)


def validate_policy(raw, root=ROOT):
    need(type(raw) is bytes and 0 < len(raw) <= 262144 and base.digest(raw) == POLICY_SHA256,
         "policy_changed")
    value = json.loads(raw)
    need(raw == base.canonical(value) + b"\n", "policy_canonical")
    previous = json.loads(_read(root, predecessor.POLICY_PATH))
    old_sources = predecessor.validate_policy(base.canonical(previous) + b"\n", root)
    current = value.get("source_sha256")
    need(type(current) is dict and set(current) == SOURCES
         and all(type(stamp) is str and re.fullmatch(r"[a-f0-9]{64}", stamp) for stamp in current.values())
         and {name for name in old_sources if current[name] != old_sources[name]} == _CHANGED,
         "source_scope")
    expected = json.loads(base.canonical(previous))
    expected["policy_id"] = "hirewiz-private-document-inspection-synchronous-clamav-1.4.7"
    expected["source_sha256"] = current
    expected["scanner"]["docx_request_daemon"]["engine_source_review"] = "ClamAV-1.4.7"
    need(value == expected, "policy_semantics_changed")
    return current


@dataclass(frozen=True)
class Source(base.Source):
    @property
    def kind(self):
        return KIND

    @property
    def policy_path(self):
        return POLICY_PATH

    @property
    def dockerfile(self):
        return DOCKERFILE

    @property
    def runtime(self):
        return {target: self.sources[name] for name, target in RUNTIME.items()}


def _contracts(root, commit):
    return {name: base.digest(base.git(root, "show", commit + ":" + name)) for name in CONTRACT_PATHS}


def committed(commit=None, *, root=ROOT, clean=False):
    need(type(clean) is bool, "selector")
    head = base.git(root, "rev-parse", "HEAD").decode().strip()
    commit = head if commit is None else commit
    need(type(commit) is str and re.fullmatch(r"[a-f0-9]{40}", commit), "commit")
    if clean:
        need(commit == head and not base.git(root, "status", "--porcelain", "--untracked-files=all"),
             "clean_committed_source_required")
    raw = base.git(root, "show", commit + ":" + POLICY_PATH)
    sources = validate_policy(raw, root)
    need(all(base.digest(base.git(root, "show", commit + ":" + name)) == stamp
             for name, stamp in sources.items()), "git_source_changed")
    result = Source(commit, sources, raw, base.digest(base.canonical(_contracts(root, commit))))
    if clean:
        require_working_contract(result, root)
    return result


def working_snapshot(root=ROOT):
    raw = _read(root, POLICY_PATH)
    sources = validate_policy(raw, root)
    contents = {name: _read(root, name) for name in sources}
    need(all(base.digest(contents[name]) == stamp for name, stamp in sources.items()), "working_source_changed")
    contents[POLICY_PATH] = raw
    need(sum(map(len, contents.values())) <= 4 * 1024 * 1024, "archive_bound")
    return contents, sources, POLICY_SHA256


def require_working_contract(source, root=ROOT):
    need(type(source) is Source and source.legacy is False and source.policy == POLICY_SHA256,
         "source_identity")
    expected = _contracts(root, source.commit)
    _, sources, _ = working_snapshot(root)
    need(source.sources == sources and base.digest(base.canonical(expected)) == source.qualification_sha256
         and all(base.digest(_read(root, name)) == stamp for name, stamp in expected.items()),
         "working_contract_changed")
