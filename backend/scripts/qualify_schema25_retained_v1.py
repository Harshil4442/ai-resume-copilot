"""Run original V1 qualification only in its actual ab2 source checkout.

Local Git/process work only. The directory and detached checkout are owned by
this invocation; no cloud operation, source rewrite or original journal exists.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

PRIOR = "ab2deadd1b1cecbe9cb10c73806dbcde851bf5e2"


def run(root: Path):
    base = Path(tempfile.mkdtemp(prefix="hirewiz-app25-retained-v1-"))
    base.chmod(0o700)
    checkout = base / "source"
    created = False
    try:
        subprocess.run(["git", "worktree", "add", "--detach", str(checkout), PRIOR], cwd=root,
            capture_output=True, check=True, timeout=30)
        created = True
        assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip() == PRIOR
        result = subprocess.run([sys.executable, "-I", "-B", "-m", "pytest", "--noconftest", "-q",
            "tests/test_schema25_app_compatibility_postgres.py"], cwd=checkout / "backend",
            env={**os.environ, "APP_ENV": "test", "HIREWIZ_APP_COMPATIBILITY_REQUIRED": "true"},
            timeout=300, check=False)
        if result.returncode:
            raise RuntimeError("Retained actual-source V1 qualification failed")
    finally:
        if created:
            assert checkout.parent == base and base.is_dir() and not base.is_symlink()
            assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip() == PRIOR
            subprocess.run(["git", "worktree", "remove", "--force", str(checkout)], cwd=root,
                capture_output=True, check=True, timeout=30)
        base.rmdir()


if __name__ == "__main__":
    run(Path(__file__).resolve().parents[2])

