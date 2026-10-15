"""Configure and verify the public Docker Hub cache on ephemeral Linux CI only.

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
        response = subprocess.run(
            ["docker", "info", "--format", "{{json .RegistryConfig.Mirrors}}"],
            check=True, capture_output=True, text=True, timeout=5,
        )
        mirrors = json.loads(response.stdout)
        if not isinstance(mirrors, list) or any(not isinstance(value, str) for value in mirrors):
            raise ValueError("Native daemon mirror observation is malformed.")
        if MIRROR in [value.rstrip("/") for value in mirrors]:
            return
        if time.monotonic() >= deadline:
            raise ValueError("Native daemon did not activate the configured public cache.")
        time.sleep(0.25)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "Linux":
        raise SystemExit("This helper is limited to ephemeral Linux GitHub Actions runners.")
    try:
        if args.verify:
            verify()
        else:
            configure(CONFIG)
    except Exception:
        raise SystemExit("Public registry cache configuration/verification failed; CI remains required.") from None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
