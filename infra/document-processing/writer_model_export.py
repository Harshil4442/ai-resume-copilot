"""Private fixed native Writer experiment; never a production render fallback."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path

MAX_SOURCE = 5 * 1024**2
MAX_PDF = 10 * 1024**2
EXPORT_OPTIONS = {
    "ExportBookmarks": True, "UseTaggedPDF": False, "ExportFormFields": False,
    "ExportNotes": False, "ExportNotesPages": False, "IsAddStream": False,
}


def remaining(deadline: float) -> float:
    value = deadline - time.monotonic()
    if not math.isfinite(value) or not 0 < value <= 55:
        raise ValueError()
    return value


def source_bytes(path: Path) -> bytes:
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or not 0 < info.st_size <= MAX_SOURCE):
        raise ValueError()
    with path.open("rb") as stream:
        data = stream.read(MAX_SOURCE + 1)
    if len(data) != info.st_size:
        raise ValueError()
    return data


def pdf_file(path: Path) -> None:
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
            or not 0 < info.st_size <= MAX_PDF):
        raise ValueError()
    with path.open("rb") as stream:
        if not stream.read(5) == b"%PDF-":
            raise ValueError()


def property_value(uno, name: str, value):
    property_ = uno.createUnoStruct("com.sun.star.beans.PropertyValue")
    property_.Name = name
    property_.Value = value
    return property_


def export_model(uno, model, handler, source: Path, output: Path, deadline: float) -> dict:
    """Two unconditional exports of the same model, with no model setters."""
    original = source_bytes(source)
    source_uri = source.as_uri()
    if not model.supportsService("com.sun.star.text.TextDocument") or model.getURL() != source_uri:
        raise ValueError()
    flags = [model.isModified()]
    if type(flags[0]) is not bool:
        raise ValueError()
    options = tuple(property_value(uno, key, value) for key, value in EXPORT_OPTIONS.items())
    properties = (
        property_value(uno, "FilterName", "writer_pdf_Export"),
        property_value(uno, "FilterData", uno.Any("[]com.sun.star.beans.PropertyValue", options)),
        property_value(uno, "Overwrite", False),
        property_value(uno, "InteractionHandler", handler),
    )
    url_unchanged = True
    for filename in ("first.pdf", "final.pdf"):
        remaining(deadline)
        target = output / filename
        if target.exists() or target.is_symlink():
            raise ValueError()
        model.storeToURL(target.as_uri(), properties)
        remaining(deadline)
        pdf_file(target)
        if source_bytes(source) != original:
            raise ValueError()
        url_unchanged = url_unchanged and model.getURL() == source_uri
        flags.append(model.isModified())
        if type(flags[-1]) is not bool:
            raise ValueError()
    if sorted(path.name for path in output.iterdir()) != ["final.pdf", "first.pdf"]:
        raise ValueError()
    return {
        "modified_before": flags[0], "modified_after_first": flags[1], "modified_after_final": flags[2],
        "model_url_unchanged": url_unchanged, "source_bytes_unchanged": True,
    }


def inputs(arguments: list[str]) -> tuple[Path, float]:
    if len(arguments) != 4 or arguments[0] != "--render":
        raise ValueError()
    directory = Path(arguments[1])
    if not re.fullmatch(r"/tmp/docx-writer-model-[a-f0-9]{32}", str(directory)):
        raise ValueError()
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
            or sorted(path.name for path in directory.iterdir()) != ["input.docx"]):
        raise ValueError()
    deadline = float(arguments[2])
    remaining(deadline)
    if not re.fullmatch(r"[a-f0-9]{64}", arguments[3]):
        raise ValueError()
    if hashlib.sha256(source_bytes(directory / "input.docx")).hexdigest() != arguments[3]:
        raise ValueError()
    if os.getpgrp() != os.getpid():
        raise ValueError()
    return directory, deadline


def main(arguments: list[str]) -> dict:
    directory, deadline = inputs(arguments)
    # Debian's bridge belongs to this fixed system Python, never /usr/local.
    import uno
    import unohelper
    from com.sun.star.connection import NoConnectException
    from com.sun.star.task import XInteractionHandler

    class AbortOnly(unohelper.Base, XInteractionHandler):
        def handle(self, request):
            abort_type = uno.getTypeByName("com.sun.star.task.XInteractionAbort")
            for continuation in request.getContinuations():
                abort = continuation.queryInterface(abort_type)
                if abort is not None:
                    abort.select()
                    return
            raise ValueError()

    profile, output = directory / "profile", directory / "pdf"
    profile.mkdir(mode=0o700)
    output.mkdir(mode=0o700)
    name = "hirewiz_writer_" + uuid.uuid4().hex
    socket_path = Path("/tmp") / ("OSL_PIPE_" + str(os.geteuid()) + "_" + name)
    if socket_path.exists() or socket_path.is_symlink():
        raise ValueError()
    office = desktop = model = None
    result = None
    try:
        remaining(deadline)
        office = subprocess.Popen([
            "/usr/bin/soffice", "--headless", "--nologo", "--nodefault", "--norestore",
            "-env:UserInstallation=" + profile.as_uri(),
            "--accept=pipe,name=" + name + ";urp;StarOffice.ComponentContext",
        ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=False,
            env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "TZ": "UTC"})
        if office.pid <= 0 or os.getpgid(office.pid) != os.getpgrp():
            raise ValueError()
        local = uno.getComponentContext()
        resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
        startup = min(deadline, time.monotonic() + 5)
        while True:
            remaining(startup)
            if office.poll() is not None:
                raise ValueError()
            try:
                remote = resolver.resolve("uno:pipe,name=" + name + ";urp;StarOffice.ComponentContext")
                break
            except NoConnectException:
                time.sleep(min(0.05, remaining(startup)))
        info = socket_path.lstat()
        if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o077):
            raise ValueError()
        remaining(deadline)
        desktop = remote.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", remote)
        handler = AbortOnly()
        load = (
            property_value(uno, "Hidden", True), property_value(uno, "ReadOnly", True),
            property_value(uno, "MacroExecutionMode", uno.getConstantByName("com.sun.star.document.MacroExecMode.NEVER_EXECUTE")),
            property_value(uno, "UpdateDocMode", uno.getConstantByName("com.sun.star.document.UpdateDocMode.NO_UPDATE")),
            property_value(uno, "RepairPackage", False), property_value(uno, "PickListEntry", False),
            property_value(uno, "InteractionHandler", handler),
        )
        model = desktop.loadComponentFromURL((directory / "input.docx").as_uri(), "_blank", 0, load)
        remaining(deadline)
        if model is None:
            raise ValueError()
        result = export_model(uno, model, handler, directory / "input.docx", output, deadline)
        model.close(True)
        model = None
        if desktop.terminate() is not True:
            raise ValueError()
        desktop = None
        if office.wait(timeout=remaining(deadline)) != 0 or socket_path.exists() or socket_path.is_symlink():
            raise ValueError()
        result["office_reaped"] = True
        if hashlib.sha256(source_bytes(directory / "input.docx")).hexdigest() != arguments[3]:
            raise ValueError()
        return result
    finally:
        # Any blocked UNO call remains bounded by the parent-owned group.
        # Normal success waits the direct child; failure never claims cleanup.
        if office is not None and office.poll() is None:
            office.terminate()
            office.wait(timeout=min(2, remaining(deadline)))


if __name__ == "__main__":
    os.umask(0o077)
    try:
        print(json.dumps(main(sys.argv[1:]), separators=(",", ":"), allow_nan=False))
    except Exception:
        print('{"status":"unavailable"}')
        raise SystemExit(1) from None
