"""Pure, bounded native TeX project validation shared with the private inspector."""
from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile, ZipInfo

from .resume_layout import ResumeLayoutError

NATIVE_FORMATS = {"tex", "texzip"}
MAX_PROJECT_BYTES = 5 * 1024 * 1024
MAX_FILES = 64
# Deliberately small supported dependency set, all supplied in the pinned image.
PACKAGES = {"fontenc", "inputenc", "lmodern", "geometry", "hyperref", "xcolor", "color",
    "array", "tabularx", "graphicx", "url", "amsmath", "amssymb", "textcomp", "babel",
    "multicol", "fancyhdr", "parskip"}
CLASSES = {"article", "report", "book"}
EXTENSIONS = {".tex", ".sty", ".cls", ".png", ".jpg", ".jpeg", ".pdf"}
PATH = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,159}\Z")
DANGEROUS = re.compile(r"\\(?:write18|openin|openout|read|write|directlua|pdfobj|pdfcatalog|pdfannot|special|catcode)\b")
DYNAMIC_COMMANDS = re.compile(r"\\(?:csname|endcsname|scantokens)\b|\^\^")
DYNAMIC_DATES = re.compile(r"\\(?:today|year|month|day|time|pdfcreationdate|pdffilemoddate|maketitle)\b")
DEPENDENCY = re.compile(r"\\(?:usepackage|RequirePackage|documentclass|LoadClass)(?:\[[^\]\n]*\])?\s*\{([^{}]+)\}")
INCLUDE = re.compile(r"\\(?:input|include|includegraphics)(?:\[[^\]\n]*\])?\s*\{([^{}]+)\}")


def _path(name: str) -> str:
    path = PurePosixPath(name)
    if (not PATH.fullmatch(name) or path.is_absolute() or str(path) != name
            or any(part in {".", ".."} or part.startswith(".") for part in path.parts)
            or path.suffix not in EXTENSIONS):
        raise ResumeLayoutError("The TeX project contains an unsafe path or unsupported file. Upload a flat, path-safe source project or a PDF.")
    return name


@dataclass(frozen=True)
class Project:
    files: dict[str, bytes]
    main: str
    kind: str

    def bytes(self) -> bytes:
        if self.kind == "tex":
            return self.files[self.main]
        output = BytesIO()
        with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
            for name, data in sorted(self.files.items()):
                info = ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o100600 << 16
                archive.writestr(info, data)
        return output.getvalue()


def read_project(data: bytes, kind: str) -> Project:
    if kind not in NATIVE_FORMATS or not 0 < len(data) <= MAX_PROJECT_BYTES:
        raise ResumeLayoutError("Native TeX source must be nonempty and at most 5 MB. Upload PDF or DOCX instead.")
    if kind == "tex":
        files = {"resume.tex": data}
    else:
        files = {}
        try:
            with ZipFile(BytesIO(data)) as archive:
                entries = archive.infolist()
                if not 0 < len(entries) <= MAX_FILES:
                    raise ResumeLayoutError("TeX projects support at most 64 source/assets files. Remove generated files or upload PDF.")
                total = 0
                for entry in entries:
                    if entry.is_dir():
                        continue
                    name = _path(entry.filename)
                    mode = entry.external_attr >> 16
                    if name in files or entry.flag_bits & 1 or (mode & 0o170000) not in {0, 0o100000}:
                        raise ResumeLayoutError("Encrypted, duplicate or linked TeX archive entries are unsupported. Upload regular source files only.")
                    total += entry.file_size
                    if total > MAX_PROJECT_BYTES:
                        raise ResumeLayoutError("The TeX project expands beyond 5 MB. Remove generated assets or upload PDF.")
                    files[name] = archive.read(entry)
        except (BadZipFile, RuntimeError, OSError) as exc:
            raise ResumeLayoutError("The TeX ZIP project is malformed. Upload a regular source ZIP or the original PDF.") from exc
    mains = []
    for name, data in files.items():
        if PurePosixPath(name).suffix not in {".tex", ".sty", ".cls"}:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ResumeLayoutError("TeX source must use UTF-8 text. Export UTF-8 source or upload PDF.") from exc
        text = re.sub(r"(?<!\\)%[^\n]*", "", text)
        if "\x00" in text or DANGEROUS.search(text):
            raise ResumeLayoutError("The TeX project uses unsupported executable/file primitives. Remove them or upload the original PDF.")
        if DYNAMIC_COMMANDS.search(text):
            raise ResumeLayoutError("The TeX project uses dynamically constructed commands that may contain an unsupported interactive action. Use literal supported commands or upload the original/custom PDF.")
        if DYNAMIC_DATES.search(text):
            raise ResumeLayoutError("The TeX project uses a dynamic date or time whose original rendered value cannot be preserved. Replace it with the intended literal date in your source, or upload the original/custom PDF.")
        if re.search(r"\\documentclass\b", text):
            mains.append(name)
        for match in DEPENDENCY.finditer(text):
            for dep in match[1].split(","):
                dep = dep.strip()
                if dep not in PACKAGES | CLASSES and dep + ".sty" not in files and dep + ".cls" not in files:
                    raise ResumeLayoutError("The TeX project needs an unsupported dependency. Use the supported package list or upload the original PDF.")
        for match in INCLUDE.finditer(text):
            target = match[1].strip()
            candidates = {target, target + ".tex", target + ".pdf", target + ".png", target + ".jpg"}
            if not any(candidate in files for candidate in candidates):
                raise ResumeLayoutError("An included TeX source or asset is missing. Include every project file in the ZIP or upload PDF.")
    if len(mains) != 1:
        raise ResumeLayoutError("The TeX project needs exactly one documentclass entry file. Remove extra main files or upload PDF.")
    if "/" in mains[0]:
        raise ResumeLayoutError("Put the main TeX file at the ZIP root and use root-relative includes, or upload PDF.")
    return Project(files, mains[0], kind)
