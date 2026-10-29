"""One exact consumed V2 transition; audit-only, never recovery/replay authority.

Serialized reports grant no authority. Reconstruct this typed disposition from
the fixed original bytes, three spent native claims, historical API restoration
and fresh current application/document proof on every daily preflight.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import schema25_maintenance as maintenance

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-document-inspector@sha256:5811233d8dd35b1fadc73f4c1a43783c83edde47c5e90142a1e96c1428312db9"
RESTORE_PLAN = "99a74874d50910dcf7a7a729d2a2feb18db04887c83bb2654b654541c220d54f"
_AUTHORITY = object()


class HistoryDenied(RuntimeError):
    """Only fixed nonsecret reasons cross the audit boundary."""


def need(value, code):
    if not value:
        raise HistoryDenied("document_consumed_history_" + code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Artifact:
    run: int
    artifact: int | None
    name: str
    source: str
    zip_sha256: str
    tree_sha256: str
    count: int


ARTIFACTS = (
    Artifact(40771705772, 12852818465, "document-refresh-refresh-40771705772-1", "e26c28310ca9fe9abaa9c6c432aacd7790bc5039", "39f09c28b9a745041b3052c8604fb1a420622de291ff6b2275fa9b4abdefb144", "a93deb43534387809f81e73cac0a0adfe27bda83122598890773e45f55f77c1b", 28),
    Artifact(40781707554, 12858021573, "document-refresh-resume-expired-v2-transition-40781707554-1", "e680b522a12410d597a02a653a0c8cdb9ec5b3ea", "e67577ef8f59495554fba9c27c59748d40fb38e80c9c52d61af69fc3b839cde4", "28e1259a77a37418a364f18a8ce11fd9fd2a76daa76175815fdb3ef874bb853b", 35),
    Artifact(40788512340, 12860975525, "document-refresh-resume-partial-v2-transition-40788512340-1", "cbad4abb39017dcf744ede77ce6bfc2826395257", "ffe242705f3bc036de6f41e8cfa4a85f2b447804f40ad926d38edc046fafefe3", "6d11534e5557b9b36e728ab9f89d36a9bd7a83d8b4c82b1db16fd96d9f213621", 40),
    Artifact(40805300453, 12868086986, "document-refresh-resume-final-v2-transition-40805300453-1", "ebee72761f433c43d2e91c9ca61d736631fea8cd", "2704e3657731002c1ba008a18cd995d14c227d1a89dbc1f13fb4f03af7df02f6", "044f4204022137d0e0fa04cafe7189d669c381b823c2991fb1e375cb8c424f00", 46),
    Artifact(40811330747, None, "document-refresh-reconcile-final-v2-transition-40811330747-1", "40f03a63af332cf244d3c2d5633e66b593e7cec3", "01e0d53837e76f80c2dc662a9eac013bbffb5d061abc82f01078f8e6049debb7", "8829bc87317bb79301b1009a1d659c71ac440dd694269b578acd58cb94c63ad7", 48),
)
RESTORE_FILES = {
    "maintenance.json": "7e68d55d76e3da6bf5bbea33721d34a4385a3ab8482e5a4d54b809c5fbe67d50",
    "service.ai-resume-parser.restore.started.json": "4c530594a0bd329c22f4a9b443b548cdd7337e31bb0ba3bce990bd53e12ff509",
    "service.ai-resume-parser.restore.acknowledged.json": "b9b6453ccbca557d2540aabccebdfa39cb38d34e4caab4508f19775a7010be26",
    "service.ai-resume-parser.restore.checked.json": "9181052da2e0b8fc0d8f640c4073e1f5b2e71a5822bf13464853d34b5e3ea5b8",
}


def tree(files):
    return sha(canonical({name: sha(raw) for name, raw in files.items()}))


def read_tree(directory: Path):
    need(directory.is_absolute() and directory.resolve() == directory and directory.is_dir()
         and directory.stat().st_uid == os.getuid() and stat.S_IMODE(directory.stat().st_mode) == 0o700,
         "private_artifact_required")
    files, total = {}, 0
    for path in directory.rglob("*"):
        info = path.lstat()
        need(not path.is_symlink() and info.st_uid == os.getuid(), "artifact_path_refused")
        if path.is_dir():
            need(stat.S_IMODE(info.st_mode) == 0o700, "artifact_path_refused")
            continue
        name = path.relative_to(directory).as_posix()
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600
             and path.suffix == ".json" and 0 < info.st_size <= 1048576, "artifact_file_refused")
        files[name] = path.read_bytes()
        total += info.st_size
        need(len(files) <= 100 and total <= 2097152, "artifact_bound")
    return files


def load_bundle(bundle: Path):
    result = {}
    for pin in ARTIFACTS:
        files = read_tree(bundle / str(pin.run))
        need(len(files) == pin.count and tree(files) == pin.tree_sha256, "exact_artifact_bytes_required")
        result[pin.run] = files
    verify_preservation(result)
    return result


def required(prior_history: Path | None):
    """Only the exact original row needs this finite historical disposition."""
    if prior_history is None:
        return False
    need(prior_history.is_absolute() and prior_history.is_dir() and not prior_history.is_symlink(),
         "private_history_required")
    rows = list(prior_history.iterdir())
    need(len(rows) <= 3, "history_bound")
    return any(tree(read_tree(row)) == ARTIFACTS[0].tree_sha256 for row in rows)


def decoded(files, path):
    value = json.loads(files[path])
    need(type(value) is dict, "json_object_required")
    return value


def verify_preservation(rows):
    complete = rows[40811330747]
    maps = ((40771705772, "native-proof/", "native-proof/"),
            (40771705772, "transition/", "source-transition/"),
            (40781707554, "native-proof/", "native-proof/"),
            (40781707554, "transition/", "source-transition/"),
            (40788512340, "source-transition/", "source-transition/"),
            (40788512340, "partial-transition/", "partial-transition/"),
            (40805300453, "source-transition/", "source-transition/"),
            (40805300453, "partial-transition/", "partial-transition/"),
            (40805300453, "final-transition/", "final-transition/"))
    for run, original, retained in maps:
        for path, raw in rows[run].items():
            if path.startswith(original):
                need(complete.get(retained + path.removeprefix(original)) == raw,
                     "original_bytes_not_preserved")
    need(complete["source-failure.json"] == rows[40805300453]["failure.json"], "failure_not_preserved")
    result = decoded(complete, "result.json")
    need(result == decoded(complete, "readback/complete.json")
         and result["status"] == "document_refresh_final_caller_restoration_observed"
         and result["read_only_reconciliation"] is True and result["automatic_replay"] is False
         and result["api_remains_maintenance_closed"] is True
         and result["image"] == IMAGE and result["source_run_id"] == 40805300453
         and result["source_artifact_id"] == 12868086986
         and result["native_acceptance_sha256"] == sha(canonical(decoded(complete, "native-proof/acceptance.json"))),
         "historical_readback_changed")


def restore_evidence(root: Path):
    directory = root / "infra/document-processing/consumed-history-api-restore"
    result = {}
    for name, expected in RESTORE_FILES.items():
        path = directory / name
        need(path.is_file() and not path.is_symlink() and sha(path.read_bytes()) == expected,
             "original_api_restore_evidence_changed")
        result[name] = json.loads(path.read_bytes())
    plan = maintenance.load(canonical(result["maintenance.json"]))
    need(sha(canonical(result["maintenance.json"])) == RESTORE_PLAN
         and all(value["plan_sha256"] == RESTORE_PLAN for name, value in result.items() if name != "maintenance.json"),
         "original_api_restore_plan_changed")
    checked = result["service.ai-resume-parser.restore.checked.json"]
    need(checked["generation"] == 345 and checked["revision"].endswith("/ai-resume-parser-m25-5c8888070c0e-restore"),
         "original_api_restore_result_changed")
    return plan, result


@dataclass(frozen=True)
class AuditedDisposition:
    original_tree_sha256: str
    artifacts: tuple[tuple[int, str], ...]
    native_claims_sha256: str
    restored_api_sha256: str
    current_applications_sha256: str
    current_document_sha256: str
    _authority: Any = field(repr=False, compare=False, default=None)

    def matches(self, directory):
        need(self._authority is _AUTHORITY and self.original_tree_sha256 == ARTIFACTS[0].tree_sha256,
             "fresh_native_audit_required")
        return tree(read_tree(directory)) == self.original_tree_sha256

    def report(self):
        return {"status": "exact_consumed_v2_history_audited", "read_only_audit": True,
            "original_artifacts_sha256": dict(self.artifacts), "original_journals_modified": False,
            "native_claims_sha256": self.native_claims_sha256, "restored_api_sha256": self.restored_api_sha256,
            "current_applications_sha256": self.current_applications_sha256,
            "current_document_sha256": self.current_document_sha256,
            "new_build_job_patch_claim_authorized": False, "automatic_replay": False}


def audit(bundle: Path, boundary):
    """The CLI supplies only its fixed read-only native boundary; reports inert."""
    rows = load_bundle(bundle)
    complete = rows[40811330747]
    claims = boundary.verify_claims(complete)
    restored = boundary.verify_original_api_restore()
    current_apps = boundary.verify_current_applications()
    current_document = boundary.verify_current_document(complete)
    # Close the observation against concurrent app/pin changes. No old source
    # image or once-true local flag substitutes for this fresh physical read.
    need(boundary.verify_current_applications() == current_apps, "current_application_changed")
    return AuditedDisposition(ARTIFACTS[0].tree_sha256,
        tuple((pin.run, pin.tree_sha256) for pin in ARTIFACTS), sha(canonical(claims)),
        sha(canonical(restored)), sha(canonical(current_apps)), sha(canonical(current_document)), _AUTHORITY)
