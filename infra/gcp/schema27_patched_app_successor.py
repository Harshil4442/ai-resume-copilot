"""App-only Source27 release retaining the qualified patched document worker.

Use after the prior application is genuinely promoted. This explicit entrypoint
selects the existing patched document proof; the mixed V1 plan, native guards,
current policy references and once/ACK/readback protocol remain unchanged.
It creates no Build, migration, policy, identity or payment activation authority.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from infra.gcp import document_patched_engine_release as patched
from infra.gcp import schema27_mixed_app_successor as mixed

MODES = ("capture", "stage", "advance", "observe", "reconcile", "rollback")
RELATIVE = "infra/gcp/schema27_patched_app_successor.py"
rolling, app, identities = mixed.rolling, mixed.app, mixed.identities

_CHILD = """
import json,sys
from pathlib import Path
root=Path(sys.argv[1])
sys.path[:0]=[str(root),str(root/'backend'),str(root/'infra/gcp')]
from scripts import schema27_app_release_contract as contract
files=contract.current_app(root)
inventory=(contract.app_digest(files),len(files))
matches=[row for row in json.loads(sys.argv[2]) if inventory==tuple(row[1:])]
assert len(matches)==1
family=matches[0][0]
if family != contract.CONTRACT:
    from scripts import schema27_app_r02_release_contract as contract
assert family==contract.CONTRACT
assert inventory==(contract.CANDIDATE_APP_SHA256,contract.CANDIDATE_APP_FILES)
from evaluation.native_acquisition_v2.qualification import current_application_identity
value=current_application_identity(root,family=contract.CONTRACT)
source=value['application_source']
assert value['role']==source['role']=='current_application_operator_source'
assert value['family']==source['family']==contract.CONTRACT
assert value['authorizes_retail'] is False
for row in (value,source):
    assert row['source_only'] is True
    assert all(row[key] is False for key in ('native_runner_eligible','retained_producer_proof','processing_authorized','native_approval'))
print(json.dumps(dict(release=source['release'],app_sha256=source['app_sha256'],app_files=source['app_files'],qualification_sha256=source['qualification_sha256'],operator_sha256=value['application_operator_sha256'])))
"""


def need(value, code):
    rolling.need(value, "schema27_patched_app_" + code)


def _origin(root):
    root = root.resolve()
    for module, name in ((mixed, "schema27_mixed_app_successor.py"),
                         (patched, "document_patched_engine_release.py"),
                         (identities, "schema27_mixed_identity_profile.py")):
        need(Path(module.__file__).resolve() == root / "infra/gcp" / name, "module_origin")
    need(Path(__file__).resolve() == root / RELATIVE, "operator_origin")


def qualify_selected(root, release):
    """Each clean checkout qualifies its own exact inventory in a new process."""
    root = root.resolve()
    git = app.successor._git
    need(git(root, "rev-parse", "HEAD").decode().strip() == release
         and not git(root, "status", "--porcelain=v1", "--untracked-files=all"), "source_checkout")
    try:
        profiles = [(contract.CONTRACT, contract.CANDIDATE_APP_SHA256, contract.CANDIDATE_APP_FILES)
                    for contract in _application_contracts()]
        child = subprocess.run([sys.executable, "-I", "-B", "-c", _CHILD, str(root), json.dumps(profiles)],
            cwd=root, env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True, timeout=90, check=False, stdin=subprocess.DEVNULL)
        need(child.returncode == 0 and len(child.stdout) <= 2048, "source_qualification")
        result = app.sources.QualifiedSource(**json.loads(child.stdout))
        result.validate()
        _application_contract(result)
        need(result.release == release and git(root, "rev-parse", "HEAD").decode().strip() == release
             and not git(root, "status", "--porcelain=v1", "--untracked-files=all"), "source_changed")
        return result
    except rolling.RollingDenied:
        raise
    except (OSError, subprocess.SubprocessError, TypeError, ValueError, RecursionError):
        raise rolling.RollingDenied("schema27_patched_app_source_qualification_unavailable") from None


def _application_contracts():
    from scripts import schema27_app_r02_release_contract as r02
    from scripts import schema27_app_release_contract as legacy

    for contract in (legacy, r02):
        need(Path(contract.__file__).resolve() == Path(__file__).resolve().parents[2]
             / "backend/scripts" / (contract.__name__.split(".")[-1] + ".py"), "module_origin")
    return legacy, r02


def _application_contract(descriptor):
    for contract in _application_contracts():
        if (descriptor.app_sha256, descriptor.app_files) == (contract.CANDIDATE_APP_SHA256, contract.CANDIDATE_APP_FILES):
            return contract
    raise rolling.RollingDenied("schema27_patched_app_source_profile")


class SourceBoundary(patched.MixedSourceBoundary):
    """Existing patched native worker proof, with explicit old/R02 app sources."""
    def verify_source_image(self, *, release, image_digest, build_id, ci_run_id):
        self.descriptor = qualify_selected(self.root, release)
        _application_contract(self.descriptor).verify_native_ci(self, ci_run_id)
        self._verify_source_image_provenance(release=release, image_digest=image_digest,
                                            build_id=build_id, ci_run_id=ci_run_id)


def build_boundary(root, target_root, prior_root, profile):
    # Refuse a foreign controller before the stock constructor can load auth.
    _origin(root)
    need(type(profile) is identities.Profile, "profile_kind")
    boundary = mixed.NativeBoundary(root, target_root, prior_root, profile)
    boundary.observer = SourceBoundary(boundary.target_root, profile)
    boundary.prior_observer = SourceBoundary(boundary.prior_root, profile)
    return boundary


def _selected(boundary):
    need(type(boundary) is mixed.NativeBoundary, "exact_native_owner")
    _origin(boundary.root)
    for observer, root in ((boundary.observer, boundary.target_root),
                           (boundary.prior_observer, boundary.prior_root)):
        need(type(observer) is SourceBoundary and observer.root == root
             and observer.identity_profile == boundary.identity_profile,
             "patched_observer_required")


def execute(mode, plan, boundary, journal):
    _selected(boundary)
    # In particular, no alternate stage/traffic builder or reconciliation path.
    return mixed.execute(mode, plan, boundary, journal)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=MODES)
    for name in ("root", "target-root", "prior-root", "plan", "journal", "identity-profile"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--identity-profile-sha256", required=True)
    parser.add_argument("--capture-config", type=Path)
    parser.add_argument("--capture-config-sha256")
    args = parser.parse_args()
    boundary = None
    try:
        _origin(args.root)
        read = app.bridge.bridge.app26.initial._read_pinned
        profile = identities.Profile.from_dict(rolling.release._json(read(
            args.identity_profile, args.identity_profile_sha256)))
        profile.verify_custody()
        boundary = build_boundary(args.root, args.target_root, args.prior_root, profile)
        _selected(boundary)
        if args.mode == "capture":
            pins, options = app.bridge._capture_config(read(args.capture_config, args.capture_config_sha256))
            need(set(options) == {"controller_ci_run_id", "nonce", "workers_release"}, "app_only_options")
            plan = boundary.capture(*pins, **options, journal_directory=args.journal)
            boundary.journal = app.bridge._bind_new(plan, args.journal)
            boundary.verify_global(plan)
            result = {"status": "patched_document_app_captured", "native_mutations": 0,
                      "plan_sha256": app.bridge._write_plan(args.plan, plan)}
        else:
            plan = mixed.load(read(args.plan, args.plan_sha256), args.plan_sha256)
            need(plan.identities == profile, "profile_changed")
            journal = rolling.Journal(args.journal, plan.custody())
            result = execute(args.mode, plan, boundary, journal)
        print(rolling.canonical(result).decode())
        return 0
    except Exception as error:
        result = mixed.diagnostics.diagnostic(error, args.mode)
        # Fixed new-entrypoint denial codes, never native exception text.
        if isinstance(error, rolling.RollingDenied) and len(error.args) == 1:
            code = error.args[0]
            if type(code) is str and code in {
                "schema27_patched_app_" + name for name in ("module_origin", "operator_origin", "profile_kind",
                "exact_native_owner", "patched_observer_required", "app_only_options", "profile_changed",
                "source_checkout", "source_qualification", "source_changed", "source_profile",
                "source_qualification_unavailable")
            }:
                result["denial_code"] = code
        print(rolling.canonical(result).decode())
        return 1
    finally:
        if boundary is not None:
            try:
                boundary.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
