"""Reproducible public pilot ZIP; source only, never credentials/test builders."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "browser-companion" / "extension"
DESTINATION = ROOT / "frontend" / "public" / "downloads" / "hirewiz-browser-companion-0.2.0.zip"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify the delivered ZIP without writing files")
    arguments = parser.parse_args()
    manifest = json.loads((SOURCE / "manifest.json").read_text())
    if (manifest["version"] != "0.2.0" or manifest["host_permissions"] != []
            or manifest["optional_host_permissions"] != [
                "https://www.hirewizhq.com/*", "https://job-boards.greenhouse.io/*"]):
        raise ValueError("reviewed_source_manifest_mismatch")
    sources = sorted(SOURCE.iterdir())
    if not sources or any(not source.is_file() or source.is_symlink()
                          or source.suffix not in {".js", ".json", ".html", ".css"} for source in sources):
        raise ValueError("reviewed_source_file_mismatch")
    names = ["hirewiz-browser-companion/" + source.name for source in sources]
    if arguments.check:
        if DESTINATION.is_symlink() or not DESTINATION.is_file():
            raise ValueError("reviewed_source_archive_missing")
        with ZipFile(DESTINATION, "r") as archive:
            entries = archive.infolist()
            if [entry.filename for entry in entries] != names:
                raise ValueError("reviewed_source_archive_entries_mismatch")
            for source, entry in zip(sources, entries, strict=True):
                body = source.read_bytes()
                if (entry.is_dir() or entry.file_size != len(body)
                        or entry.date_time != (2026, 10, 14, 0, 0, 0)
                        or entry.external_attr != 0o100644 << 16
                        or archive.read(entry) != body):
                    raise ValueError("reviewed_source_archive_bytes_mismatch")
    else:
        DESTINATION.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(DESTINATION, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
            for source, name in zip(sources, names, strict=True):
                entry = ZipInfo(name, date_time=(2026, 10, 14, 0, 0, 0))
                entry.compress_type = ZIP_DEFLATED
                entry.external_attr = 0o100644 << 16
                archive.writestr(entry, source.read_bytes(), compress_type=ZIP_DEFLATED, compresslevel=9)
    label = "Verified" if arguments.check else "Reviewed"
    print(f"{label} source ZIP: {DESTINATION.name}; SHA-256 {hashlib.sha256(DESTINATION.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
