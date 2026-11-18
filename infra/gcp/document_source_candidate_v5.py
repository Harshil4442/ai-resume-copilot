"""V5 document source identity for the synchronous Cloud Run launcher contract."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from infra.gcp import document_source_candidate as v4

ROOT = Path(__file__).resolve().parents[2]
KIND = "native_cloud_build_source_synchronous_v1"
DEFINITIONS_KIND = "native_cloud_build_synchronous_definitions_v1"
POLICY_PATH = "infra/document-processing/policy.source-synchronous.json"
POLICY_SHA256 = "ea6607f4cfcd1470cc8466e33be3f4db37be7192303e375ef59a4b77217ac5a5"
CONTRACT_VERSION = 5
TRANSPORT_ENV = "DOCUMENT_WORKER_SANDBOX_TRANSPORT"
TRANSPORT_MODE = "synchronous-do-v1"
SOURCES = v4.SOURCES
DOCKERFILE = v4.DOCKERFILE
CONTRACT_PATHS = (
    *v4.CONTRACT_PATHS,
    "infra/gcp/document_source_candidate_v5.py",
    "infra/gcp/document_source_payload_v5.py",
    "infra/gcp/document_source_receipt_v5.py",
)
_UPDATED_RUNTIME = {"infra/document-processing/processor.py", "infra/document-processing/service.py"}
_MAX_SOURCE_BYTES = 4 * 1024 * 1024
git = v4.git
definition_recipe = v4.definition_recipe


def need(value, code):
    v4.need(value, "synchronous_" + code)


def _read(root: Path, name: str) -> bytes:
    path = root / name
    need(path.is_file() and path.resolve() == root.resolve() / name
         and 0 < path.stat().st_size <= _MAX_SOURCE_BYTES, "working_file_invalid")
    return path.read_bytes()


def validate_policy(raw: bytes, root: Path = ROOT) -> dict[str, str]:
    need(type(raw) is bytes and 0 < len(raw) <= 256 * 1024 and v4.digest(raw) == POLICY_SHA256,
         "policy_changed")
    value = json.loads(raw)
    need(raw == v4.canonical(value) + b"\n", "policy_not_canonical")
    prior = _read(root, v4.POLICY_PATH)
    sources = v4.validate_policy(prior, root)
    current = value.get("source_sha256")
    need(type(current) is dict and set(current) == SOURCES
         and all(type(stamp) is str and re.fullmatch(r"[a-f0-9]{64}", stamp) for stamp in current.values())
         and {key for key in sources if current[key] != sources[key]} == _UPDATED_RUNTIME, "policy_source_scope")
    expected = json.loads(prior)
    expected["policy_id"] = "hirewiz-private-document-inspection-native-source-synchronous-v5"
    expected["source_sha256"] = current
    controls = expected["required_native_deployment_controls"]
    del controls["forced_owned_sandbox_delete_before_release"]
    controls["owned_synchronous_sandbox_teardown"] = {
        "activation_setting": {"name": TRANSPORT_ENV, "value": TRANSPORT_MODE},
        "positive_owned_reaped_launcher_exit": 0,
        "explicit_force_delete_after_unacknowledged_authorized_launch": True,
        "strict_force_delete_exit_for_unknown_launch": 0,
        "unknown_cleanup_quarantines_admission": True,
        "transport_envelope_keys": ["processor_code", "result"],
        "inspection_processor_mode": "--sandbox-stdin",
    }
    temporary = controls["request_only_private_tmp_bind"]
    del temporary["erase_after_confirmed_native_delete"]
    temporary["erase_after_confirmed_native_teardown"] = True
    expected["docx_layout"]["processor_mode"] = "--sandbox-docx-layout-stdin"
    for scope in (expected["outcomes"], expected["docx_layout"]["outcomes"]):
        for outcome in scope.values():
            if isinstance(outcome, dict) and "processor_exit" in outcome:
                outcome["processor_code"] = outcome.pop("processor_exit")
    need(value == expected, "policy_semantics_changed")
    return current


@dataclass(frozen=True)
class Source(v4.Source):
    @property
    def kind(self):
        return KIND

    @property
    def policy_path(self):
        return POLICY_PATH

    @property
    def dockerfile(self):
        return DOCKERFILE


def _contracts(root: Path, commit: str) -> dict[str, str]:
    return {name: v4.digest(git(root, "show", commit + ":" + name)) for name in CONTRACT_PATHS}


def committed(commit=None, *, root: Path = ROOT, clean: bool = False) -> Source:
    need(type(clean) is bool, "clean_selector_invalid")
    head = git(root, "rev-parse", "HEAD").decode().strip()
    commit = head if commit is None else commit
    need(type(commit) is str and re.fullmatch(r"[a-f0-9]{40}", commit), "commit_invalid")
    if clean:
        need(commit == head and not git(root, "status", "--porcelain", "--untracked-files=no"),
             "committed_clean_source_required")
    raw = git(root, "show", commit + ":" + POLICY_PATH)
    sources = validate_policy(raw, root)
    need(all(v4.digest(git(root, "show", commit + ":" + name)) == stamp for name, stamp in sources.items()),
         "git_source_changed")
    source = Source(commit, sources, raw, v4.digest(v4.canonical(_contracts(root, commit))))
    if clean:
        require_working_contract(source, root)
    return source


def working_snapshot(root: Path = ROOT) -> tuple[dict[str, bytes], dict[str, str], str]:
    raw = _read(root, POLICY_PATH)
    sources = validate_policy(raw, root)
    contents = {name: _read(root, name) for name in sources}
    need(all(v4.digest(contents[name]) == stamp for name, stamp in sources.items()), "working_source_changed")
    contents[POLICY_PATH] = raw
    need(sum(map(len, contents.values())) <= _MAX_SOURCE_BYTES, "source_archive_bound")
    return contents, sources, POLICY_SHA256


def require_working_contract(source: Source, root: Path = ROOT) -> None:
    need(type(source) is Source and source.legacy is False and source.policy == POLICY_SHA256
         and type(source.commit) is str and re.fullmatch(r"[a-f0-9]{40}", source.commit), "source_identity_invalid")
    expected = _contracts(root, source.commit)
    _, sources, _ = working_snapshot(root)
    need(source.sources == sources and v4.digest(v4.canonical(expected)) == source.qualification_sha256
         and all(v4.digest(_read(root, name)) == stamp for name, stamp in expected.items()),
         "working_contract_changed")
