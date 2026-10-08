"""Local identity/schema guard; deliberately not a writer-retirement verifier."""
from __future__ import annotations

import ast
import configparser
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

ALLOWED_CHAIN = (
    "20260803_0001", "20260803_0002", "20260803_0003", "20261007_0004",
    "20261008_0005", "20261008_0006", "20261008_0007", "20261008_0008",
    "20261008_0009",
)
MIGRATIONS = "backend/alembic/versions/"
DECLARATIONS = {"revision", "down_revision", "branch_labels", "depends_on"}


class PreflightDenied(RuntimeError):
    pass


def git(root: Path, *args: str) -> bytes:
    # Repository redirection must not let another checkout supply this identity.
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    try:
        return subprocess.run(
            ["git", "--no-replace-objects", "-c", "core.fsmonitor=false", "-C", str(root), *args],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=environment,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        raise PreflightDenied("A verifiable local Git checkout is required.") from None


def checked_files(root: Path, release: str) -> set[str]:
    if Path(os.fsdecode(git(root, "rev-parse", "--show-toplevel").strip())).resolve() != root:
        raise PreflightDenied("Source must be the Git checkout root.")
    if git(root, "rev-parse", "HEAD").decode().strip() != release:
        raise PreflightDenied("Requested release does not match checked-out HEAD.")
    if git(root, "status", "--porcelain", "--untracked-files=all"):
        raise PreflightDenied("Release source has uncommitted or untracked changes.")
    tracked: set[str] = set()
    for entry in git(root, "ls-tree", "-r", "-z", "--full-tree", release).split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        mode, kind, expected = metadata.split()
        path = os.fsdecode(raw_path)
        source = root / path
        if mode not in {b"100644", b"100755"} or kind != b"blob" or source.is_symlink():
            raise PreflightDenied("Unsupported source link or Git tree entry.")
        if not source.is_file():
            raise PreflightDenied("Release source file is unavailable.")
        if any(parent.is_symlink() for parent in source.parents if parent != root and root in parent.parents):
            raise PreflightDenied("Release source contains a linked directory.")
        body = source.read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body, usedforsecurity=False)
        if actual.hexdigest().encode() != expected:
            # Check bytes directly even when Git assumes a file is unchanged.
            raise PreflightDenied("Release source bytes differ from the checked commit.")
        tracked.add(path)
    return tracked


def migration_head(root: Path, tracked: set[str]) -> str:
    configuration = configparser.ConfigParser(interpolation=None)
    configuration.read(root / "backend/alembic.ini")
    script_location = configuration.get("alembic", "script_location", fallback="")
    version_locations = configuration.get("alembic", "version_locations", fallback="")
    if (script_location != "%(here)s/alembic" or version_locations
            or configuration.getboolean("alembic", "recursive_version_locations", fallback=False)):
        raise PreflightDenied("Unsupported migration discovery configuration.")
    paths = sorted((root / MIGRATIONS).glob("*.py"))
    if not paths or {path.relative_to(root).as_posix() for path in paths} != {
        path for path in tracked if path.startswith(MIGRATIONS) and path.endswith(".py")
    }:
        raise PreflightDenied("Migration sources are missing or untracked.")
    revisions: dict[str, str | None] = {}
    for path in paths:
        values: dict[str, object] = {}
        # Parse declarations without importing migration/app code or running upgrade().
        for node in ast.parse(path.read_bytes()).body:
            expression: ast.expr | None
            if isinstance(node, ast.Assign):
                targets, expression = node.targets, node.value
            elif isinstance(node, ast.AnnAssign):
                targets, expression = [node.target], node.value
            else:
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    if any((alias.asname or alias.name.split(".")[0]) in DECLARATIONS for alias in node.names):
                        raise PreflightDenied("Migration imports shadow declarations.")
                elif isinstance(node, ast.FunctionDef):
                    if node.name in DECLARATIONS or node.decorator_list:
                        raise PreflightDenied("Unsupported migration function declaration.")
                elif not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                          and isinstance(node.value.value, str)):
                    raise PreflightDenied("Migration metadata has executable module-level control flow.")
                continue
            for target in targets:
                if not isinstance(target, ast.Name) or target.id not in DECLARATIONS:
                    raise PreflightDenied("Unsupported module-level migration assignment.")
                if target.id in values or expression is None:
                    raise PreflightDenied("Migration declarations are ambiguous.")
                values[target.id] = ast.literal_eval(expression)
        if (set(values) != DECLARATIONS or not isinstance(values["revision"], str)
                or not re.fullmatch(r"[a-zA-Z0-9_]{1,128}", values["revision"])
                or not (values["down_revision"] is None or isinstance(values["down_revision"], str))
                or values["branch_labels"] is not None or values["depends_on"] is not None):
            raise PreflightDenied("Unsupported migration declarations.")
        revision = values["revision"]
        if revision in revisions:
            raise PreflightDenied("Duplicate migration revision.")
        parent = values["down_revision"]
        assert parent is None or isinstance(parent, str)
        revisions[revision] = parent
    if any(parent is not None and parent not in revisions for parent in revisions.values()):
        raise PreflightDenied("Migration ancestry is incomplete.")
    heads = set(revisions) - set(revisions.values())
    if len(heads) != 1:
        raise PreflightDenied("Exactly one migration head is required.")
    head = next(iter(heads))
    chain: list[str] = []
    cursor: str | None = head
    while cursor is not None:
        if cursor in chain:
            raise PreflightDenied("Migration ancestry contains a cycle.")
        chain.append(cursor)
        cursor = revisions[cursor]
    if len(chain) != len(revisions) or tuple(reversed(chain)) != ALLOWED_CHAIN:
        raise PreflightDenied(
            "Legacy release.sh accepts only the reviewed schema0009 chain. "
            "Monetary/unknown migrations require a separate approved cutover entry point, not yet implemented."
        )
    return head


def verify_source(root: Path, release: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{40}", release):
        raise PreflightDenied("A full release commit is required.")
    root = root.resolve()
    head = migration_head(root, checked_files(root, release))
    if git(root, "status", "--porcelain", "--untracked-files=all"):
        raise PreflightDenied("Source changed during preflight.")
    return head


def main() -> int:
    try:
        if len(sys.argv) != 2:
            raise PreflightDenied("Exactly one release commit argument is required.")
        head = verify_source(Path(__file__).resolve().parents[2], sys.argv[1])
    except PreflightDenied as exc:
        print(f"Release preflight refused: {exc}", file=sys.stderr)
        return 65
    except (OSError, ValueError, TypeError, SyntaxError, configparser.Error):
        # Do not print source content, Git diagnostics, configuration or exception extras.
        print("Release preflight refused: unknown/mismatched source or unsupported schema; no cloud calls permitted.", file=sys.stderr)
        return 65
    print(f"Checked source {sys.argv[1]} has permitted schema {head}; writer retirement is not asserted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
