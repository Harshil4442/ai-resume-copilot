"""Build and verify the secretless, architecture-specific native TeX test image.

This local Docker attestation is a CI prerequisite, not production isolation proof.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

TEST_IMAGE_ENV = "HIREWIZ_NATIVE_TEX_TEST_IMAGE"
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
ROOT = Path(__file__).resolve().parents[2]
SOURCES = ("Dockerfile", "processor.py", "service.py")
LABEL_PREFIX = "hirewiz.native-test.source."
HASH_PROGRAM = (
    "import hashlib,json,pathlib; "
    "print(json.dumps({n:hashlib.sha256(pathlib.Path('/'+n).read_bytes()).hexdigest() "
    "for n in ('processor.py','service.py')},sort_keys=True))"
)


class NativeTexTestImageError(RuntimeError):
    """A required native test prerequisite is unavailable or does not match source."""


def source_hashes(root: Path = ROOT) -> dict[str, str]:
    try:
        return {
            name: hashlib.sha256((root / "infra/native-tex" / name).read_bytes()).hexdigest()
            for name in SOURCES
        }
    except OSError as exc:
        raise NativeTexTestImageError("Native TeX test build sources are unavailable.") from exc


def _run(command: list[str], *, timeout: int = 30) -> bytes:
    try:
        result = subprocess.run(
            command, check=False, capture_output=True, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NativeTexTestImageError(
            "Required native TeX Docker verification is unavailable or timed out."
        ) from exc
    if result.returncode != 0 or len(result.stdout) > 64 * 1024:
        raise NativeTexTestImageError(
            "Required native TeX Docker verification failed; rebuild the test image."
        )
    return result.stdout


def _cleanup(owned: str) -> None:
    try:
        result = subprocess.run(
            ["docker", "rm", "--force", owned],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NativeTexTestImageError("Native test verification container cleanup failed.") from exc
    if result.returncode and b"No such container:" not in result.stderr:
        raise NativeTexTestImageError("Native test verification container cleanup failed.")


def verify_image(image: str, hashes: dict[str, str], platform: str | None = None) -> dict[str, Any]:
    if not IMAGE_ID.fullmatch(image):
        raise NativeTexTestImageError(
            f"{TEST_IMAGE_ENV} must be an explicit lowercase sha256 image ID; no tags or fallback."
        )
    try:
        entries = json.loads(_run(["docker", "image", "inspect", image]))
        if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
            raise ValueError("invalid inspect contract")
        metadata = entries[0]
        config = metadata.get("Config")
        if not isinstance(config, dict) or not isinstance(config.get("Labels"), dict):
            raise ValueError("missing source attestation")
        labels = config["Labels"]
        if metadata.get("Id") != image or metadata.get("Os") != "linux":
            raise ValueError("image identity changed")
        architecture = metadata.get("Architecture")
        if architecture not in {"amd64", "arm64"} or (
            platform and platform != f"linux/{architecture}"
        ):
            raise ValueError("image architecture mismatch")
        if any(labels.get(LABEL_PREFIX + name) != digest for name, digest in hashes.items()):
            raise ValueError("checked-in source changed")
    except (ValueError, TypeError) as exc:
        raise NativeTexTestImageError(
            "Native TeX image identity, platform or checked-in source attestation changed; rebuild it."
        ) from exc
    owned = "hirewiz-tex-attest-" + uuid.uuid4().hex
    command = [
        "docker",
        "run",
        "--pull=never",
        "--rm",
        "--name",
        owned,
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user=65534:65534",
        "--cpus=1",
        "--memory=256m",
        "--memory-swap=256m",
        "--pids-limit=32",
        "--entrypoint=python3",
        image,
        "-I",
        "-c",
        HASH_PROGRAM,
    ]
    try:
        workers = json.loads(_run(command))
        expected = {name: hashes[name] for name in ("processor.py", "service.py")}
        if workers != expected:
            raise ValueError("worker content changed")
    except (ValueError, TypeError, KeyError) as exc:
        raise NativeTexTestImageError(
            "Native TeX image worker bytes differ from checked-in source; rebuild it."
        ) from exc
    finally:
        # Owned daemon-side cleanup also covers a timeout of the Docker client.
        _cleanup(owned)
    return {
        "image_id": image,
        "platform": f"linux/{architecture}",
        "source_sha256": hashes,
        "worker_sha256": workers,
    }


@lru_cache(maxsize=8)
def _verified(image: str, items: tuple[tuple[str, str], ...]) -> str:
    verify_image(image, dict(items))
    return image


def required_test_image() -> str:
    image = os.environ.get(TEST_IMAGE_ENV, "")
    if not IMAGE_ID.fullmatch(image):
        raise NativeTexTestImageError(
            f"Set {TEST_IMAGE_ENV} to the verified sha256 ID from scripts/native_tex_test_image.py; native compiler tests must run, not skip."
        )
    return _verified(image, tuple(sorted(source_hashes().items())))


def build_image(platform: str) -> dict[str, Any]:
    hashes = source_hashes()
    with tempfile.TemporaryDirectory(prefix="hirewiz-native-tex-ci-build-") as temporary:
        context = Path(temporary) / "context"
        context.mkdir()
        for name in SOURCES:
            data = (ROOT / "infra/native-tex" / name).read_bytes()
            if hashlib.sha256(data).hexdigest() != hashes[name]:
                raise NativeTexTestImageError("Native TeX source changed during the build.")
            copied = context / name
            copied.write_bytes(data)
            # These three checked-in nonsecret build inputs must be readable by
            # the image's nonroot worker even when the caller uses umask 0077.
            copied.chmod(0o644)
        iid = Path(temporary) / "image.id"
        command = ["docker", "build", "--pull=false", "--platform", platform, "--iidfile", str(iid)]
        for name, digest in hashes.items():
            command += ["--label", LABEL_PREFIX + name + "=" + digest]
        command.append(str(context))
        try:
            subprocess.run(command, check=True, timeout=600)
            image = iid.read_text().strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise NativeTexTestImageError(
                "Native TeX source build failed; no test image was published."
            ) from exc
    if source_hashes() != hashes:
        raise NativeTexTestImageError("Native TeX source changed after the build.")
    return verify_image(image, hashes, platform)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("linux/amd64", "linux/arm64"), required=True)
    parser.add_argument("--github-env", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_image(args.platform)
    payload = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload)
    if args.github_env:
        with args.github_env.open("a") as target:
            target.write(f"{TEST_IMAGE_ENV}={report['image_id']}\n")
    print(payload, end="")


if __name__ == "__main__":
    main()
