"""Build-only verification of the unmodified, licensed Courier Prime bundle.

This does not render candidate data or assert native deployment acceptance.
The runtime's existing font inventory additionally rechecks the installed bytes.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

FONT_ROOT = Path("/usr/share/fonts/truetype/hirewiz-courier-prime")
NOTICE_ROOT = Path("/usr/share/doc/hirewiz-courier-prime")
PINS = {
    "CourierPrime-Regular.ttf": "72f793376f8e2841656bf21d77a5de010f2929bd6956a22ee848ad0c7eb978af",
    "CourierPrime-Bold.ttf": "ff1f38786c849d1c41fa8e447960abdb2bd75fdfb0cfcdeb524fad65a5af3638",
    "CourierPrime-Italic.ttf": "f1b9a5829789f7e56432a9f3bc7665ef4531dbba1c112639e48bef39621a006b",
    "CourierPrime-BoldItalic.ttf": "3355273f3ea6d6362658f9564f4e83d59e156cf1a182e3c1592b5c5943627dcc",
    "OFL.txt": "9a755af092b494944c99f471be6fddd19b006a448fefdc4717e4ee0aa09a97b0",
}
STYLES = {
    "CourierPrime-Regular.ttf": "weight=regular:slant=roman",
    "CourierPrime-Bold.ttf": "weight=bold:slant=roman",
    "CourierPrime-Italic.ttf": "weight=regular:slant=italic",
    "CourierPrime-BoldItalic.ttf": "weight=bold:slant=italic",
}


def require(value):
    if not value:
        raise ValueError("courier_prime_bundle_unverified")


def verify_bundle(fonts: Path = FONT_ROOT, notices: Path = NOTICE_ROOT) -> None:
    for name, expected in PINS.items():
        path = (fonts if name.endswith(".ttf") else notices) / name
        require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= 131072)
        require(hashlib.sha256(path.read_bytes()).hexdigest() == expected)
    provenance = notices / "provenance.json"
    require(provenance.is_file() and not provenance.is_symlink() and 0 < provenance.stat().st_size <= 16384)
    metadata = json.loads(provenance.read_bytes())
    require(metadata["family"] == "Courier Prime" and metadata["modified"] is False
            and metadata["license"] == "SIL Open Font License 1.1"
            and metadata["source_commit"] == "7204f5a9b1e530633e00c67bd4954ceb7d8c2483")
    recorded = {row["path"]: row["sha256"] for row in metadata["files"]}
    require(all(recorded.get(name) == digest for name, digest in PINS.items()))
    for name, style in STYLES.items():
        result = subprocess.run(
            ["/usr/bin/fc-match", "--format=%{family}\n%{file}\n", "Courier Prime:" + style],
            env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"}, capture_output=True, timeout=5, check=False,
        )
        require(result.returncode == 0 and not result.stderr and len(result.stdout) <= 1024)
        require(result.stdout.decode("utf-8").splitlines() == ["Courier Prime", str(fonts / name)])


if __name__ == "__main__":
    verify_bundle()
