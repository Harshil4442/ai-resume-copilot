"""Secretless compilation payload processor; runtime isolation is mandatory.

stdin/stdout protocol for a restricted OCI job. Never run this on the API host.
A Cloud Run job/service wrapper must enforce the documented network/filesystem
contract before invoking this processor; this file is not that enforcement.
"""
import base64
import hashlib
import json
import os
import pathlib
import re
import resource
import subprocess
import sys

MAX_INPUT = 8 * 1024 * 1024
MAX_OUTPUT = 10 * 1024 * 1024


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate_project_key")
        result[key] = value
    return result


def main():
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT, MAX_OUTPUT))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
    data = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(data) > MAX_INPUT:
        raise ValueError("project_too_large")
    request = json.loads(data, object_pairs_hook=pairs)
    if (type(request) is not dict or set(request) != {"protocol", "main", "files"}
            or request.get("protocol") != "hirewiz-tex-v1" or type(request["files"]) is not list
            or not 0 < len(request["files"]) <= 64):
        raise ValueError("unsupported_protocol")
    # Cloud Run's child image root is read-only. Only its request-owned /tmp
    # bind is writable; local Docker tests use their existing private /tmp.
    root = pathlib.Path("/tmp/project")
    root.mkdir()
    names = set()
    total = 0
    for entry in request["files"]:
        if type(entry) is not dict or set(entry) != {"path", "data"} or type(entry["path"]) is not str or type(entry["data"]) is not str:
            raise ValueError("unsafe_project_path")
        name = entry["path"]
        path = pathlib.PurePosixPath(name)
        if (re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,159}", name) is None
                or path.is_absolute() or any(p in {".", ".."} or p.startswith(".") for p in path.parts)
                or "\\" in name or str(path) != name or name in names or len(names) >= 64
                or path.suffix not in {".tex", ".sty", ".cls", ".png", ".jpg", ".jpeg", ".pdf"}):
            raise ValueError("unsafe_project_path")
        names.add(name)
        content = base64.b64decode(entry["data"], validate=True)
        total += len(content)
        if total > 5 * 1024 * 1024:
            raise ValueError("project_too_large")
        dest = root / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
    main_file = request["main"]
    if type(main_file) is not str or main_file not in names or not main_file.endswith(".tex"):
        raise ValueError("missing_main")
    # All process state is disposable, and no application credentials are copied.
    environment = {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "TMPDIR": "/tmp",
        "openin_any": "p", "openout_any": "p", "shell_escape": "f",
        "SOURCE_DATE_EPOCH": "946684800", "FORCE_SOURCE_DATE": "1",
        "TEXMFOUTPUT": "/tmp/project"}
    command = ["pdflatex", "-no-shell-escape", "-halt-on-error", "-interaction=batchmode",
        "-jobname=hirewiz-output", main_file]
    for _ in range(2):
        result = subprocess.run(command, cwd=root, env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=12)
        if result.returncode:
            raise ValueError("compile_failed")
    log = (root / "hirewiz-output.log").read_text(errors="replace")
    if any(word in log for word in ("Overfull \\hbox", "Overfull \\vbox", "Missing character:")):
        raise ValueError("overflow_or_missing_glyph")
    pdf = (root / "hirewiz-output.pdf").read_bytes()
    if not pdf.startswith(b"%PDF-") or not 0 < len(pdf) <= MAX_OUTPUT:
        raise ValueError("invalid_output")
    print(json.dumps({"protocol": "hirewiz-tex-v1", "pdf": base64.b64encode(pdf).decode(),
        "sha256": hashlib.sha256(pdf).hexdigest(), "size": len(pdf),
        "renderer": "pdftex-no-shell-escape-v1"}))


if __name__ == "__main__":
    if sys.argv[1:] == ["serve"]:
        os.execv("/usr/bin/python3", ["python3", "-I", "/service.py"])
    try:
        main()
    except Exception:
        # Never expose untrusted TeX, logs, filenames, filesystem paths or secrets.
        print(json.dumps({"error": "compile_refused"}))
        sys.exit(1)
