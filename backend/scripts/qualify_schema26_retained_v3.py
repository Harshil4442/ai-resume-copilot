"""Run the genuinely qualified45b3 schema26 V3 source; never relabel current27.

This provisions no servers. Frozen tests use prepared CI services and own their
temporary database/TLS fixtures. No provider authority or serving-prior pin is
created. Unknown children retain the checkout rather than claiming cleanup or
retrying the original command.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

SOURCE = "45b3e808c88d78c1d7d182cd4af4482df25c13f0"
FINGERPRINT = "ae56969b6a6aeb41d6b8202deca891c839e3f8564efc5743457777f2c1bd8fcb"
APP_SHA256 = "f2775c3b9f8d8df1f4f3d97abbe190cf5d2eb46b87beeb942b5252c3840d8670"
PINS = {'backend/uv.lock': '427f77314ac25b72b0079ca4becb95f4eab5b9877b3d541997fb5ef17cad0dec', 'backend/pyproject.toml': '6c1f9ff4c05ead913348aa0d4e5656f3a51eedbec5015a6b3947a0efe5bb29dc', 'backend/scripts/schema25_app_release_contract_v3.py': '3cd812bf7b40554bdd09af016f0f74763d53f7adbc02c6b854197296ad6abcdc', 'backend/scripts/qualify_schema26_compatibility.py': 'ea5452a2bd92e772fa2b872c4f12be8750b382d863080dd1f10a07d6cbbb90cd', '.github/workflows/ci.yml': '8f7173f0b08e3c1f6826c48a41c4402d31f33b63bdf79f7f673287b663054de8', 'backend/tests/test_schema25_app_compatibility_v3_postgres.py': '25e2913d4d49adb40691f6492c2740f0c01497c5fd0dcadb03b714229e5ed924', 'backend/tests/test_schema25_app_protocol_v3.py': '75dc0d0baf94de0b7677260bf77fc7705682a1f2fbfcdd6d3a1521da1d9d980d', 'backend/tests/test_schema25_app_rolling_v3.py': '0b462e5099b8390e92e9cbdef5942d2087e79c2de9f55b8e39a542c5d76fd1b3'}
COMPATIBILITY_COMMAND = ("-I", "-B", "scripts/qualify_schema26_compatibility.py")
ROLLING = ("tests/test_schema25_app_rolling_v3.py",)
PROTOCOL = ("tests/test_schema25_app_protocol_v3.py",)
_STANDARD_ENV = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TZ", "TMPDIR", "TMP", "TEMP",
                 "RUNNER_TEMP", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_ACTIONS", "CI")
_NATIVE_ENV = ("HIREWIZ_TEST_POSTGRES_URL", "HIREWIZ_OWNED_REDIS_SERVER", "HIREWIZ_DOCUMENT_TEST_REPORT",
               "HIREWIZ_NATIVE_TEX_TEST_IMAGE", "HIREWIZ_TRANSFER_PG_DUMP", "HIREWIZ_TRANSFER_PG_RESTORE",
               "HIREWIZ_TRANSFER_WORKDIR", "HIREWIZ_TRANSFER_POSTGRES_REQUIRED")
_VERIFY = r'''
import json,sys
from pathlib import Path
root=Path(sys.argv[1])
sys.path[:0]=[str(root/"backend"),str(root/"infra/gcp"),str(root)]
from scripts import schema25_app_release_contract_v3 as contract
import unified_credit_schema25_release as release
assert Path(contract.__file__).resolve()==root/"backend/scripts/schema25_app_release_contract_v3.py"
assert Path(release.__file__).resolve()==root/"infra/gcp/unified_credit_schema25_release.py"
prior=release.prior_app_files(root,contract.PRIOR_RELEASE)
fingerprint=contract.source_fingerprint(root,prior)
app=release.prior_app_files(root,sys.argv[2])
print(json.dumps({"fingerprint":fingerprint,"app":release.prior_app_digest(app),"files":len(app),
 "compatibility":contract.COMPATIBILITY_COMMAND,"protocol":contract.PROTOCOL_COMMAND},sort_keys=True))
'''


class RetainedV3Denied(RuntimeError):
    pass


class _UnknownOutcome(RetainedV3Denied):
    pass


def _need(value: bool, reason: str) -> None:
    if not value:
        raise RetainedV3Denied("retained_v3_" + reason)


def _private_input(value: str, parent: Path, *, executable: bool = False) -> None:
    path = Path(value)
    _need(path.is_absolute() and path.resolve() == path and path.is_relative_to(parent)
          and path.is_file() and not path.is_symlink() and path.stat().st_uid == os.getuid(), "native_input_path")
    _need(os.access(path, os.X_OK) if executable else 0 < path.stat().st_size <= 4 * 1024**2, "native_input_file")


def _environment(source: dict[str, str], *, platform: str) -> dict[str, str]:
    _need(platform == "linux" and source.get("CI") == "true" and source.get("GITHUB_ACTIONS") == "true",
          "owned_linux_ci_required")
    _need(all(re.fullmatch(r"[1-9][0-9]*", source.get(key, "")) for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")),
          "ci_identity_required")
    temporary = Path(source.get("RUNNER_TEMP", ""))
    _need(temporary.is_absolute() and temporary.resolve() == temporary and temporary.is_dir()
          and not temporary.is_symlink() and temporary.stat().st_uid == os.getuid(), "ci_temporary_required")
    try:
        url = urlsplit(source.get("HIREWIZ_TEST_POSTGRES_URL", ""))
        valid = (url.scheme == "postgresql+psycopg" and url.hostname == "127.0.0.1"
                 and url.port in {55433, 55434} and url.path == "/hirewiz_admission_test"
                 and not url.query and not url.fragment and bool(url.username))
    except ValueError:
        valid = False
    _need(valid, "owned_ci_postgres_required")
    _need(not source.get("HIREWIZ_APP25_TEST_ROOT") and source.get("HIREWIZ_AUTHORITY_EMULATOR_PORT", "58877") == "58877",
          "owned_service_override_refused")
    _private_input(source.get("HIREWIZ_OWNED_REDIS_SERVER", ""), temporary, executable=True)
    _private_input(source.get("HIREWIZ_DOCUMENT_TEST_REPORT", ""), temporary)
    _need(re.fullmatch(r"sha256:[a-f0-9]{64}", source.get("HIREWIZ_NATIVE_TEX_TEST_IMAGE", "")) is not None,
          "native_tex_image_required")
    transfers = ("HIREWIZ_TRANSFER_PG_DUMP", "HIREWIZ_TRANSFER_PG_RESTORE", "HIREWIZ_TRANSFER_WORKDIR")
    if any(source.get(key) for key in transfers):
        _need(all(source.get(key) for key in transfers)
              and source.get("HIREWIZ_TRANSFER_POSTGRES_REQUIRED") == "true", "transfer_inputs_incomplete")
        for key in transfers[:2]:
            _private_input(source[key], temporary, executable=True)
        work = Path(source[transfers[2]])
        _need(work.is_absolute() and work.resolve() == work and work.is_relative_to(temporary)
              and work.is_dir() and not work.is_symlink() and work.stat().st_uid == os.getuid(), "transfer_input_path")
    result = {key: source[key] for key in (*_STANDARD_ENV, *_NATIVE_ENV) if key in source}
    result.update(APP_ENV="test", HIREWIZ_APP_COMPATIBILITY_REQUIRED="true",
                  HIREWIZ_AUTHORITY_EMULATOR_REQUIRED="true", PYTHONDONTWRITEBYTECODE="1",
                  GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    return result


def _call(args: list[str], *, cwd: Path, env: dict[str, str], timeout: int = 30) -> bytes:
    try:
        result = subprocess.run(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=timeout, check=False)
    except BaseException:
        # A timeout/interrupt does not prove nested native fixture cleanup.
        raise _UnknownOutcome("retained_v3_child_outcome_unknown") from None
    _need(result.returncode == 0, "child_failed")
    _need(len(result.stdout) <= 2 * 1024**2 and len(result.stderr) <= 2 * 1024**2, "child_output_bound")
    return result.stdout


def _git(root: Path, env: dict[str, str], *args: str) -> bytes:
    return _call(["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull, *args], cwd=root, env=env)


def _identity(base: Path, checkout: Path, owner: tuple[int, int, int], env: dict[str, str],
              anchor: tuple[bytes, int, int, int] | None) -> tuple[bytes, int, int, int]:
    info = base.stat()
    _need(not base.is_symlink() and stat.S_IMODE(info.st_mode) == 0o700
          and (info.st_dev, info.st_ino, info.st_uid) == owner and checkout.parent == base
          and checkout.is_dir() and not checkout.is_symlink() and checkout.resolve() == checkout, "checkout_owner_changed")
    directory = checkout.stat()
    _need(directory.st_uid == os.getuid(), "checkout_owner_changed")
    link = checkout / ".git"
    _need(link.is_file() and not link.is_symlink() and link.stat().st_uid == os.getuid()
          and 0 < link.stat().st_size <= 4096, "checkout_link_changed")
    raw = link.read_bytes()
    current = (raw, directory.st_dev, directory.st_ino, directory.st_uid)
    _need(anchor is None or current == anchor, "checkout_link_or_owner_changed")
    _need(_git(checkout, env, "rev-parse", "HEAD").decode().strip() == SOURCE, "historical_head_changed")
    _need(_git(checkout, env, "rev-parse", "--show-toplevel").decode().strip() == str(checkout), "checkout_root_changed")
    _need(not _git(checkout, env, "status", "--porcelain", "--untracked-files=all"), "checkout_source_changed")
    return current


def _verify(checkout: Path, env: dict[str, str]) -> None:
    for name, expected in PINS.items():
        path = checkout / name
        _need(path.is_file() and not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest() == expected,
              "historical_file_changed")
    raw = _call([sys.executable, "-I", "-B", "-c", _VERIFY, str(checkout), SOURCE], cwd=checkout / "backend", env=env)
    try:
        observed = json.loads(raw)
    except (ValueError, UnicodeError):
        raise RetainedV3Denied("retained_v3_original_verification_invalid") from None
    _need(observed == {"fingerprint": FINGERPRINT, "app": APP_SHA256, "files": 290,
                      "compatibility": ".venv/bin/python -I -B scripts/qualify_schema26_compatibility.py",
                      "protocol": ".venv/bin/python -I -B -m pytest --noconftest -q " + " ".join(PROTOCOL)},
          "original_verification_changed")


def _pytest(checkout: Path, env: dict[str, str], suites: tuple[str, ...], report: Path) -> None:
    from xml.etree import ElementTree

    _call([sys.executable, "-I", "-B", "-m", "pytest", "--noconftest", "-q", *suites,
           "--junitxml", str(report)], cwd=checkout / "backend", env=env, timeout=900)
    _need(report.is_file() and not report.is_symlink() and 0 < report.stat().st_size <= 4 * 1024**2,
          "report_required")
    raw = report.read_bytes()
    _need(b"<!doctype" not in raw.lower() and b"<!entity" not in raw.lower(), "report_invalid")
    cases = ElementTree.fromstring(raw).findall(".//testcase")
    _need(bool(cases) and not any(case.find(tag) is not None for case in cases
          for tag in ("failure", "error", "skipped")), "every_original_case_required")
    _need(all(any(case.get("classname", "").split(".")[-1] == Path(suite).stem for case in cases)
              for suite in suites), "original_suite_missing")
    report.unlink()


def run(root: Path, phase: str) -> None:
    _need(phase in {"compatibility", "protocol"}, "phase_required")
    _need(root.is_absolute() and root.resolve() == root, "source_root_invalid")
    env = _environment(dict(os.environ), platform=sys.platform)
    for name in ("backend/uv.lock", "backend/pyproject.toml"):
        _need(hashlib.sha256((root / name).read_bytes()).hexdigest() == PINS[name], "installed_lock_changed")
    _need(_git(root, env, "rev-parse", "--show-toplevel").decode().strip() == str(root), "source_root_invalid")
    base = Path(tempfile.mkdtemp(prefix="hirewiz-schema26-retained-v3-", dir=env["RUNNER_TEMP"]))
    base.chmod(0o700)
    info = base.stat()
    owner = (info.st_dev, info.st_ino, info.st_uid)
    checkout, anchor = base / "source", None
    created = unknown = False
    try:
        _git(root, env, "worktree", "add", "--detach", str(checkout), SOURCE)
        created = True
        anchor = _identity(base, checkout, owner, env, None)
        _verify(checkout, env)
        try:
            if phase == "compatibility":
                _call([sys.executable, *COMPATIBILITY_COMMAND], cwd=checkout / "backend", env=env, timeout=900)
                _pytest(checkout, env, ROLLING, base / "rolling.xml")
            else:
                _pytest(checkout, env, PROTOCOL, base / "protocol.xml")
        except RetainedV3Denied:
            # Even exit1 can contain an inner timeout. Never replay or remove a
            # checkout whose descendants/owned native fixtures are uncertain.
            unknown = True
            raise
        _identity(base, checkout, owner, env, anchor)
        _verify(checkout, env)
    except _UnknownOutcome:
        unknown = True
        raise
    finally:
        # Partial creation, changed identity or unknown descendants stay retained.
        # There is no broad rmtree, worktree prune, retry or source substitution.
        if created and not unknown and anchor is not None:
            _identity(base, checkout, owner, env, anchor)
            _git(root, env, "worktree", "remove", "--force", str(checkout))
            _need(not checkout.exists() and not checkout.is_symlink(), "cleanup_not_confirmed")
            try:
                base.rmdir()
            except OSError:
                raise RetainedV3Denied("retained_v3_cleanup_not_confirmed") from None


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True, choices=("compatibility", "protocol"))
    arguments = parser.parse_args()
    try:
        run(Path(__file__).resolve().parents[2], arguments.phase)
    except Exception:
        raise SystemExit("retained_v3_unavailable_or_child_outcome_unknown") from None
    print("Retained actual-source45b3 schema26 V3 passed; not current27 or rollout authority.")
