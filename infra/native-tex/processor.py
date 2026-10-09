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
import resource
import subprocess
import sys

MAX_INPUT = 8 * 1024 * 1024
MAX_OUTPUT = 10 * 1024 * 1024


def main():
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT, MAX_OUTPUT))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
    data = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(data) > MAX_INPUT:
        raise ValueError("project_too_large")
    request = json.loads(data)
    if request.get("protocol") != "hirewiz-tex-v1":
        raise ValueError("unsupported_protocol")
    root = pathlib.Path("/work/project")
    root.mkdir()
    names = set()
    total = 0
    for entry in request["files"]:
        name = entry["path"]
        path = pathlib.PurePosixPath(name)
        if (not name or path.is_absolute() or any(p in {".", ".."} or p.startswith(".") for p in path.parts)
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
    if main_file not in names or not main_file.endswith(".tex"):
        raise ValueError("missing_main")
    # All process state is disposable, and no application credentials are copied.
    environment = {"PATH": "/usr/bin:/bin", "HOME": "/work", "TMPDIR": "/tmp",
        "openin_any": "p", "openout_any": "p", "shell_escape": "f",
        "SOURCE_DATE_EPOCH": "946684800", "FORCE_SOURCE_DATE": "1",
        "TEXMFOUTPUT": "/work/project"}
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
