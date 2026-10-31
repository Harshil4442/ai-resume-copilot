"""Finite source closure for the isolated evaluator, separate from app protocol.

Default backend pytest collects both named modules. This source check does not
substitute for their genuine exact-head CI, native metadata or Job acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

RUNNER_RELEASE = "7be8cac2a332a5ad19366402013d5f385f97105b"
RUNNER_FINGERPRINT = "739d9751132a9864bda0f3fda89b6f03a095f3c2c195c3f9ad9696fb5943ce86"
COMPONENT_PATHS = (
    "infra/gcp/native_quality_acquisition_isolation.py",
    "backend/scripts/qualify_native_quality_acquisition.py",
    "backend/tests/test_native_quality_acquisition_isolation.py",
    "backend/tests/test_qualify_native_quality_acquisition.py",
)
SUITES = ("tests/test_native_quality_acquisition_isolation.py", "tests/test_qualify_native_quality_acquisition.py")
RUNNER_PATHS = (
    "Dockerfile", "backend/pyproject.toml", "backend/uv.lock",
    "infra/gcp/document_worker.py", "infra/gcp/native_tex_worker.py", "infra/gcp/document_network.py",
    "infra/native-tex/policy.json",
    "backend/scripts/native_acquisition_job.py", "backend/scripts/native_acquisition_channel.py",
    "backend/app/domains/analysis/native_input_acquisition.py",
    "backend/app/services/document_ingestion.py", "backend/app/services/native_tex_transport.py",
)


def need(value, code):
    if not value:
        raise ValueError("native_quality_source_" + code)


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False, timeout=10)
    need(result.returncode == 0, "git_unavailable")
    return result.stdout


def component_hashes(root: Path) -> dict[str, str]:
    entries = {}
    for name in COMPONENT_PATHS:
        p = root / name
        need(p.is_file() and not p.is_symlink() and 0 < p.stat().st_size <= 512 * 1024, "component_path")
        entries[name] = hashlib.sha256(p.read_bytes()).hexdigest()
    return entries


def source_identity(root: Path) -> dict:
    need(root.is_absolute() and root.resolve() == root, "root")
    sys.path[:0] = [str(root / "backend"), str(root / "infra/gcp")]
    import unified_credit_schema25_release as release

    from scripts import schema25_app_release_contract_v3 as base

    need(Path(base.__file__).resolve() == root / "backend/scripts/schema25_app_release_contract_v3.py"
         and Path(release.__file__).resolve() == root / "infra/gcp/unified_credit_schema25_release.py", "module_source")
    original = base.source_fingerprint(root, release.prior_app_files(root, base.PRIOR_RELEASE))
    need(original == RUNNER_FINGERPRINT, "frozen_runner_or_contract_changed")
    for name in RUNNER_PATHS:
        need((root / name).read_bytes() == git(root, "show", RUNNER_RELEASE + ":" + name), "runner_entry_or_dependency_changed")
    entries = component_hashes(root)
    body = {"schema_version": "native-quality-component-v1", "runner_release": RUNNER_RELEASE,
            "runner_app_and_contract_sha256": original,
            "runner_eligibility": "historical_7be_CI_failed_no_Build_not_eligible", "component_paths": entries, "full_ci_suites": list(SUITES)}
    return {**body, "runner_entry_sha256": hashlib.sha256(git(root, "show", RUNNER_RELEASE + ":backend/scripts/native_acquisition_job.py")).hexdigest(), "controller_sha256": hashlib.sha256(json.dumps(body, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    try:
        need(not git(args.root, "status", "--porcelain"), "clean_source_required")
        result = source_identity(args.root)
        print(json.dumps({**result, "controller_head": git(args.root, "rev-parse", "HEAD").decode().strip(),
            "source_only": True, "CI_qualification_claimed": False, "native_approval": False,
            "provider_calls": 0, "processing_authorized": False}, sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({"state": "native_quality_source_refused", "native_approval": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
