"""Reproducible public pilot ZIP; source only, never credentials/test builders."""
from __future__ import annotations

import argparse
import hashlib
import json
import base64
import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "browser-companion" / "extension"
DESTINATION = ROOT / "frontend" / "public" / "downloads" / "hirewiz-browser-companion-0.8.8.zip"
SOURCE_FILES = (
    "assistance-runtime.js", "assistance-transport.js", "background.js", "config.js", "device.js",
    "greenhouse-native-network.js", "greenhouse-owned-upload.js", "greenhouse-resume-component.js", "local-adapter.js",
    "lost-key-recovery.js", "manifest.json", "pairing-runtime.js", "pairing-transport.js", "popup.css", "popup.html",
    "popup.js", "protocol.js", "razorpay-adapter.js", "startup-diagnostics.js", "submission-billing.js", "transport.js",
)


def native_configuration(path: Path) -> bytes:
    """Release input contains public pins only, never an enrollment capability."""
    raw = path.read_bytes()
    if path.is_symlink() or len(raw) > 4096:
        raise ValueError("native_public_configuration_bound")
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("native_public_configuration_duplicate")
            value[key] = item
        return value
    value = json.loads(raw, object_pairs_hook=pairs)
    # Package 0.8.8 adds explicit priced reviews; native identity admission stays pinned.
    if (type(value) is not dict or set(value) != {"executorRevision", "authorityKey"}
            or value["executorRevision"] != "companion_0_8_1"):
        raise ValueError("native_public_configuration_revision")
    key = value["authorityKey"]
    if (type(key) is not dict or set(key) != {"kty", "crv", "x", "y"}
            or key["kty"] != "EC" or key["crv"] != "P-256"
            or any(type(key[name]) is not str or re.fullmatch(r"[A-Za-z0-9_-]{43}", key[name]) is None for name in ("x", "y"))):
        raise ValueError("native_public_configuration_key")
    x, y = (int.from_bytes(base64.urlsafe_b64decode(key[name] + "="), "big") for name in ("x", "y"))
    prime = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
    curve_b = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
    if not 0 <= x < prime or not 0 <= y < prime or (y*y - x*x*x + 3*x - curve_b) % prime:
        raise ValueError("native_public_configuration_point")
    config = {"mode": "reviewed_assistance", "websiteOrigin": "https://www.hirewizhq.com",
        "portalOrigin": "https://job-boards.greenhouse.io", "extensionId": "pnjmegppacleipajnifjmbcopjjffklb", "nativePairing": value}
    return ("// Reviewed release public KMS key and executor pin; no secret or authority grant.\nexport const CONFIG = Object.freeze(" + json.dumps(config, sort_keys=True, separators=(",", ":")) + ");\n").encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify the delivered ZIP without writing files")
    parser.add_argument("--native-config", type=Path, help="Exact reviewed public claim-key/executor pins for normal-user pairing")
    arguments = parser.parse_args()
    manifest = json.loads((SOURCE / "manifest.json").read_text())
    if (manifest["version"] != "0.8.8" or manifest["host_permissions"] != []
            or manifest["optional_host_permissions"] != [
                "https://www.hirewizhq.com/*", "https://job-boards.greenhouse.io/*",
                "https://boards.greenhouse.io/*", "https://*.amazonaws.com/*"]
            or manifest["optional_permissions"] != ["webRequest"]):
        raise ValueError("reviewed_source_manifest_mismatch")
    sources = sorted(SOURCE.iterdir())
    if tuple(source.name for source in sources) != SOURCE_FILES or any(not source.is_file() or source.is_symlink()
                          or source.suffix not in {".js", ".json", ".html", ".css"} for source in sources):
        raise ValueError("reviewed_source_file_mismatch")
    names = ["hirewiz-browser-companion/" + source.name for source in sources]
    configured = native_configuration(arguments.native_config) if arguments.native_config else None
    def content(source):
        return configured if source.name == "config.js" and configured is not None else source.read_bytes()
    if arguments.check:
        if DESTINATION.is_symlink() or not DESTINATION.is_file():
            raise ValueError("reviewed_source_archive_missing")
        with ZipFile(DESTINATION, "r") as archive:
            entries = archive.infolist()
            if [entry.filename for entry in entries] != names:
                raise ValueError("reviewed_source_archive_entries_mismatch")
            for source, entry in zip(sources, entries, strict=True):
                body = content(source)
                if (entry.is_dir() or entry.file_size != len(body)
                        or entry.date_time != (2026, 10, 15, 0, 0, 0)
                        or entry.external_attr != 0o100644 << 16
                        or archive.read(entry) != body):
                    raise ValueError("reviewed_source_archive_bytes_mismatch")
    else:
        DESTINATION.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(DESTINATION, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
            for source, name in zip(sources, names, strict=True):
                entry = ZipInfo(name, date_time=(2026, 10, 15, 0, 0, 0))
                entry.compress_type = ZIP_DEFLATED
                entry.external_attr = 0o100644 << 16
                archive.writestr(entry, content(source), compress_type=ZIP_DEFLATED, compresslevel=9)
    label = "Verified" if arguments.check else "Reviewed"
    print(f"{label} source ZIP: {DESTINATION.name}; SHA-256 {hashlib.sha256(DESTINATION.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
