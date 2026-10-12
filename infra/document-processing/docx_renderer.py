"""Fixed Linux Writer execution inside the existing credential-free sandbox.

No host fallback, repair conversion, user command, network client or document
output escapes this helper. The service's already bounded owned launcher also
owns every Writer/version process group. Native image acceptance is required.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import tempfile
import time
from pathlib import Path

MANIFEST = Path("/opt/document/docx-font-manifest.json")
SOFFICE = "/usr/bin/soffice"
MAX_MANIFEST = 256 * 1024
MAX_PDF = 10 * 1024 * 1024
# Fixed documented PDF export options; no forms, notes or embedded native file.
EXPORT = 'pdf:writer_pdf_Export:' + json.dumps({
    key: {"type": "boolean", "value": value} for key, value in {
        "ExportBookmarks": "true", "UseTaggedPDF": "true", "ExportFormFields": "false",
        "ExportNotes": "false", "ExportNotesPages": "false", "IsAddStream": "false",
    }.items()
}, separators=(",", ":"))


class RendererUnavailable(ValueError):
    def __init__(self):
        super().__init__("docx_renderer_unavailable")


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise RendererUnavailable()
        result[key] = value
    return result


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _inventory() -> list[dict[str, str]]:
    """Bind installed fonts and their exact resolution rules, not font names."""
    paths = set()
    for directory in (Path("/usr/share/fonts"), Path("/usr/lib/libreoffice/share/fonts")):
        if directory.exists():
            for path in directory.rglob("*"):
                if path.suffix.lower() in {".ttf", ".otf", ".ttc"}:
                    paths.add(path.resolve(strict=True))
                    if len(paths) > 1000:
                        raise RendererUnavailable()
    if not paths:
        raise RendererUnavailable()
    for directory in (Path("/etc/fonts"), Path("/var/cache/fontconfig")):
        if directory.exists():
            for path in directory.rglob("*"):
                if path.is_file():
                    paths.add(path.resolve(strict=True))
                    if len(paths) > 1000:
                        raise RendererUnavailable()
    for filename in ("/usr/bin/soffice", "/usr/lib/libreoffice/program/soffice.bin"):
        paths.add(Path(filename).resolve(strict=True))
    if len(paths) > 1000:
        raise RendererUnavailable()
    rows, total = [], 0
    for path in sorted(paths):
        if (not path.is_file() or len(str(path)) > 512
                or not str(path).startswith(("/usr/share/", "/usr/lib/libreoffice/", "/etc/fonts/", "/var/cache/fontconfig/"))):
            raise RendererUnavailable()
        size = path.stat().st_size
        total += size
        if not 0 < size <= 32 * 1024 * 1024 or total > 256 * 1024 * 1024:
            raise RendererUnavailable()
        with path.open("rb") as stream:
            content = stream.read(32 * 1024 * 1024 + 1)
        if len(content) != size:
            raise RendererUnavailable()
        rows.append({"path": str(path), "sha256": hashlib.sha256(content).hexdigest()})
    return rows


def _run(arguments: list[str], *, deadline: float, limit: int = 65536) -> bytes:
    # Fixed local service module in /opt/document; it imports no application.
    from service import _run as owned_run
    remaining = deadline - time.monotonic()
    if not math.isfinite(remaining) or not 0 < remaining <= 55:
        raise RendererUnavailable()
    try:
        code, output = owned_run(arguments, timeout=remaining, limit=limit)
        if code != 0:
            raise RendererUnavailable()
        return output
    except Exception:
        raise RendererUnavailable() from None


def _version(*, deadline: float) -> str:
    with tempfile.TemporaryDirectory(prefix="writer-version-", dir="/tmp") as temporary:
        directory = Path(temporary)
        directory.chmod(0o700)
        output = _run([SOFFICE, "--headless", "--norestore",
                       "-env:UserInstallation=" + (directory / "profile").as_uri(), "--version"],
                      deadline=min(deadline, time.monotonic() + 5), limit=1024)
    version = output.decode("ascii", errors="strict").strip()
    # Distribution builds legitimately include e.g. `40(Build:2)`. Preserve
    # the full observed suffix; exact native acceptance pins it at the API.
    if (not 0 < len(version) <= 200
            or not re.fullmatch(r"LibreOffice [0-9]+\.[0-9]+\.[0-9]+(?:\.[0-9]+)*(?: [ -~]+)?", version)):
        raise RendererUnavailable()
    return version


def font_manifest(*, deadline: float) -> tuple[str, str]:
    if MANIFEST.is_symlink() or not 0 < MANIFEST.stat().st_size <= MAX_MANIFEST:
        raise RendererUnavailable()
    with MANIFEST.open("rb") as stream:
        raw = stream.read(MAX_MANIFEST + 1)
    if not 0 < len(raw) <= MAX_MANIFEST:
        raise RendererUnavailable()
    data = json.loads(raw, object_pairs_hook=_pairs)
    if (type(data) is not dict or set(data) != {"version", "renderer_version", "files"}
            or type(data["version"]) is not int or data["version"] != 1
            or data["files"] != _inventory()
            or data["renderer_version"] != _version(deadline=deadline)):
        raise RendererUnavailable()
    return data["renderer_version"], hashlib.sha256(raw).hexdigest()


def _render(content: bytes, directory: Path, *, deadline: float) -> bytes:
    if type(content) is not bytes or not 0 < len(content) <= 5 * 1024 * 1024:
        raise RendererUnavailable()
    directory.mkdir(mode=0o700)
    source = directory / "input.docx"
    source.write_bytes(content)
    source.chmod(0o600)
    output = directory / "pdf"
    output.mkdir(mode=0o700)
    _run([SOFFICE, "--headless", "--nologo", "--nodefault", "--norestore",
          "-env:UserInstallation=" + (directory / "profile").as_uri(),
          "--convert-to", EXPORT, "--outdir", str(output), str(source)], deadline=deadline)
    target = output / "input.pdf"
    if (source.read_bytes() != content or sorted(path.name for path in output.iterdir()) != ["input.pdf"]
            or target.is_symlink() or not target.is_file() or not 0 < target.stat().st_size <= MAX_PDF):
        raise RendererUnavailable()
    with target.open("rb") as stream:
        result = stream.read(MAX_PDF + 1)
    if not 0 < len(result) <= MAX_PDF or not result.startswith(b"%PDF-"):
        raise RendererUnavailable()
    return result


def validate_rendered_edit(source: bytes, candidate: bytes, edits: list[dict], *,
                           image: str, policy: str, deadline: float) -> dict:
    from app.services.docx_layout_validation import RendererIdentity, validate_docx_layout
    try:
        version, fonts = font_manifest(deadline=deadline)
        with tempfile.TemporaryDirectory(prefix="writer-compare-", dir="/tmp") as temporary:
            directory = Path(temporary)
            directory.chmod(0o700)
            before = _render(source, directory / "before", deadline=deadline)
            after = _render(candidate, directory / "after", deadline=deadline)
            result = validate_docx_layout(source_docx=source, candidate_docx=candidate, edits=edits,
                before_pdf=before, after_pdf=after,
                renderer=RendererIdentity(image, policy, version, fonts))
            if time.monotonic() >= deadline:
                raise RendererUnavailable()
            return result.to_dict()
    except RendererUnavailable:
        raise
    except Exception as error:
        from app.services.docx_layout_validation import DocxLayoutError
        if isinstance(error, DocxLayoutError):
            raise
        raise RendererUnavailable() from None


def main() -> None:
    if sys.argv[1:] != ["--write-font-manifest"]:
        raise RendererUnavailable()
    os.umask(0o077)
    sys.path.insert(0, "/opt/document")
    data = {"version": 1, "renderer_version": _version(deadline=time.monotonic() + 10), "files": _inventory()}
    raw = _canonical(data)
    if len(raw) > MAX_MANIFEST:
        raise RendererUnavailable()
    with MANIFEST.open("xb") as output:
        output.write(raw)
    MANIFEST.chmod(0o444)


if __name__ == "__main__":
    main()
