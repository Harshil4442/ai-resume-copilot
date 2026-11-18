"""Qualify each Source27 checkout in its own interpreter, without native action."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from infra.gcp import schema27_continuity_scope as scope

from scripts import schema26_app_successor as successor

FAMILY = "hirewiz_schema27_app_v1"


@dataclass(frozen=True, slots=True)
class QualifiedSource:
    release: str
    app_sha256: str
    app_files: int
    qualification_sha256: str
    operator_sha256: str

    def validate(self):
        scope.rolling.need(type(self) is QualifiedSource
            and type(self.release) is str and re.fullmatch(r"[a-f0-9]{40}", self.release)
            and type(self.app_files) is int and 1 <= self.app_files <= 2048
            and all(scope._hash(value) for value in
                    (self.app_sha256, self.qualification_sha256, self.operator_sha256)),
            "schema27_app_source_descriptor")


# A new interpreter is essential: the prior root has different pinned app bytes
# and must not be evaluated through the controller's already-imported modules.
_CHILD = """
import json,sys
from pathlib import Path
root=Path(sys.argv[1])
sys.path[:0]=[str(root),str(root/'backend'),str(root/'infra/gcp')]
from evaluation.native_acquisition_v2.qualification import current_application_identity
value=current_application_identity(root,family='hirewiz_schema27_app_v1')
source=value['application_source']
assert value['role']==source['role']=='current_application_operator_source'
assert value['family']==source['family']=='hirewiz_schema27_app_v1'
assert value['authorizes_retail'] is False
for row in (value,source):
    assert row['source_only'] is True
    assert all(row[key] is False for key in ('native_runner_eligible','retained_producer_proof','processing_authorized','native_approval'))
print(json.dumps(dict(release=source['release'],app_sha256=source['app_sha256'],app_files=source['app_files'],qualification_sha256=source['qualification_sha256'],operator_sha256=value['application_operator_sha256'])))
"""


def qualify(root: Path, release: str) -> QualifiedSource:
    """Source-only validation; no provider credential is passed into the child."""
    root = root.resolve()
    scope.rolling.need(successor._git(root, "rev-parse", "HEAD").decode().strip() == release
        and not successor._git(root, "status", "--porcelain=v1", "--untracked-files=all"),
        "schema27_app_source_checkout")
    environment = {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        child = subprocess.run([sys.executable, "-I", "-B", "-c", _CHILD, str(root)],
            cwd=root, env=environment, capture_output=True, timeout=90, check=False,
            stdin=subprocess.DEVNULL)
        scope.rolling.need(child.returncode == 0 and len(child.stdout) <= 2048,
                           "schema27_app_source_qualification")
        value = QualifiedSource(**json.loads(child.stdout))
        value.validate()
        scope.rolling.need(value.release == release, "schema27_app_source_release")
        scope.rolling.need(successor._git(root, "rev-parse", "HEAD").decode().strip() == release
            and not successor._git(root, "status", "--porcelain=v1", "--untracked-files=all"),
            "schema27_app_source_changed")
        return value
    except scope.rolling.RollingDenied:
        raise
    except (OSError, subprocess.SubprocessError, TypeError, ValueError, RecursionError):
        raise scope.rolling.RollingDenied("schema27_app_source_qualification_unavailable") from None
