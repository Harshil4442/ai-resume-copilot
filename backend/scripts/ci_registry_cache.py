"""Configure verified public image caches on ephemeral Linux CI only.

Daemon reload and immutable image pulls remain explicit workflow operations.
No account credentials, daemon restart, image execution or scan exceptions.
"""

import argparse
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

MIRROR = "https://mirror.gcr.io"
CONFIG = Path("/etc/docker/daemon.json")
DOCUMENT_BASE_DIGEST = "sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1"
DOCUMENT_BASE = "public.ecr.aws/docker/library/python"
SOURCE_POLICY_ENV = "EXPERIMENTAL_BUILDKIT_SOURCE_POLICY"


def document_source_policy() -> Path:
    """Route only the existing document base to identical cached manifest bytes.

    Docker Hub daemon mirrors do not intercept an explicit ECR reference.
    BuildKit's source policy changes its transport without editing the attested
    Dockerfile or changing its base digest. The two literal selectors cover
    tag+digest and digest-only normalization, with no repository-wide rules.
    https://docs.docker.com/build/building/variables/#experimental_buildkit_source_policy
    """
    if os.environ.get(SOURCE_POLICY_ENV):
        raise ValueError("An inherited BuildKit source policy is not permitted.")
    runner_value = os.environ.get("RUNNER_TEMP", "")
    if not runner_value or any(ord(char) < 32 or ord(char) == 127 for char in runner_value):
        raise ValueError("A canonical runner temporary directory is required.")
    runner = Path(runner_value)
    if (not runner.is_absolute() or not runner.is_dir() or runner.resolve(strict=True) != runner
            or runner.stat().st_uid != os.getuid()):
        raise ValueError("A canonical owned runner temporary directory is required.")
    policy = {"version": 1, "rules": [
        {"action": "CONVERT", "selector": {"identifier": source, "matchType": "EXACT"},
         "updates": {"identifier": "docker-image://mirror.gcr.io/library/python@" + DOCUMENT_BASE_DIGEST}}
        for source in (
            "docker-image://" + DOCUMENT_BASE + ":3.12-slim-trixie@" + DOCUMENT_BASE_DIGEST,
            "docker-image://" + DOCUMENT_BASE + "@" + DOCUMENT_BASE_DIGEST,
        )
    ]}
    raw = (json.dumps(policy, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(raw) > 4096:
        raise ValueError("Document source policy exceeds the review bound.")
    path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="hirewiz-document-source-", suffix=".json",
                                         dir=runner, delete=False) as target:
            path = Path(target.name)
            os.fchmod(target.fileno(), 0o600)
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
        return path
    except BaseException:
        if path is not None:
            path.unlink(missing_ok=True)
        raise


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate daemon configuration key.")
        result[name] = value
    return result


def configure(path: Path) -> None:
    if path.is_symlink() or path.exists() and not path.is_file():
        raise ValueError("Daemon configuration must be a regular file.")
    raw = path.read_bytes() if path.exists() else b"{}"
    if len(raw) > 128 * 1024:
        raise ValueError("Daemon configuration exceeds the review bound.")
    config = json.loads(raw, object_pairs_hook=_object)
    if not isinstance(config, dict):
        raise ValueError("Daemon configuration must be a JSON object.")
    mirrors = config.get("registry-mirrors", [])
    if not isinstance(mirrors, list) or any(not isinstance(value, str) for value in mirrors):
        raise ValueError("Existing registry mirrors are malformed.")
    config["registry-mirrors"] = [MIRROR] + [value for value in mirrors if value.rstrip("/") != MIRROR]
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as target:
        temporary = Path(target.name)
        try:
            json.dump(config, target, sort_keys=True)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def verify() -> None:
    deadline = time.monotonic() + 10
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("Native daemon did not activate the configured public cache.")
        try:
            response = subprocess.run(
                ["docker", "info", "--format", "{{json .RegistryConfig.Mirrors}}"],
                check=True, capture_output=True, text=True, timeout=min(5, remaining),
            )
        except subprocess.TimeoutExpired:
            # A SIGHUP reload may briefly stall this read-only observation. The
            # same absolute budget covers every attempt; no daemon mutation or
            # cache-activation requirement is retried, waived or substituted.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(0.25, remaining))
            continue
        mirrors = json.loads(response.stdout)
        if not isinstance(mirrors, list) or any(not isinstance(value, str) for value in mirrors):
            raise ValueError("Native daemon mirror observation is malformed.")
        if MIRROR in [value.rstrip("/") for value in mirrors]:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("Native daemon did not activate the configured public cache.")
        time.sleep(min(0.25, remaining))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--verify", action="store_true")
    mode.add_argument("--document-source-policy", action="store_true")
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "Linux":
        raise SystemExit("This helper is limited to ephemeral Linux GitHub Actions runners.")
    try:
        if args.document_source_policy:
            print(document_source_policy())
        elif args.verify:
            verify()
        else:
            configure(CONFIG)
    except subprocess.TimeoutExpired:
        raise SystemExit("Public registry cache daemon observation timed out; CI remains required.") from None
    except subprocess.CalledProcessError:
        raise SystemExit("Public registry cache daemon observation command failed; CI remains required.") from None
    except Exception:
        raise SystemExit("Public registry cache configuration/verification failed; CI remains required.") from None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
