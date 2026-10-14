"""Synthetic-only Cloud Run Job acceptance; never used for candidate execution.

Run this trusted code against the immutable worker with sandboxLauncher true,
the same secretless identity/resources, one task and zero retries. It never
submits a job or claims production caller/IAM/HTTP acceptance by itself.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import time
import uuid
from pathlib import Path


def load_worker():
    spec = importlib.util.spec_from_file_location("native_tex_acceptance_worker", "/opt/tex/cloud_service.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def payload(text):
    return json.dumps({"protocol": "hirewiz-tex-v1", "main": "resume.tex", "files": [
        {"path": "resume.tex", "data": base64.b64encode(text.encode()).decode()}]}).encode()


def clean_source(text="Built Python reports."):
    return r"\documentclass{article}\usepackage[T1]{fontenc}\usepackage{lmodern}\pagestyle{empty}\begin{document}" + text + r"\end{document}"


ISOLATION = r'''import errno,json,os,pathlib,socket
denied=[]
for host in ("169.254.169.254","1.1.1.1"):
    try:
        connection=socket.create_connection((host,80),timeout=1)
        connection.close()
        denied.append(False)
    except OSError as error:
        denied.append(error.errno in (errno.ENETUNREACH,errno.EACCES,errno.EPERM,errno.EAFNOSUPPORT))
root_write_denied=False
try:
    pathlib.Path("/tex-native-root-probe").write_bytes(b"synthetic")
except OSError as error:
    root_write_denied=error.errno in (errno.EROFS,errno.EACCES,errno.EPERM)
# A permission error alone is insufficient: require the native root mount to
# be read-only as well as the actual denied write.
root_mounts=[line.split() for line in pathlib.Path("/proc/self/mountinfo").read_text().splitlines()
             if line.split()[4]=="/"]
root_readonly=bool(root_mounts) and all("ro" in mount[5].split(",") for mount in root_mounts) and root_write_denied
tmp=pathlib.Path("/tmp/tex-probe")
tmp.write_bytes(b"synthetic")
result={"network_denied":all(denied),"root_readonly":root_readonly,"tmp_writable":tmp.read_bytes()==b"synthetic","ambient_env_absent":"HIREWIZ_TEX_SYNTHETIC_SENTINEL" not in os.environ}
print(json.dumps(result,sort_keys=True))
'''

SHELL_ESCAPE = r'''import contextlib,importlib.util,io,json,pathlib
spec=importlib.util.spec_from_file_location("native_probe_processor","/processor.py")
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
output=io.StringIO()
with contextlib.redirect_stdout(output):
    module.main()
result=json.loads(output.getvalue())
print(json.dumps({"compiled":result.get("protocol")=="hirewiz-tex-v1","shell_escape_disabled":not pathlib.Path("/tmp/project/shell-escape-marker").exists()},sort_keys=True))
'''


def direct_sandbox(worker, code, timeout, input_bytes=None):
    owned = worker.launcher()
    name = "document-" + uuid.uuid4().hex
    directory = Path("/tmp") / name
    directory.mkdir(mode=0o700)
    os.chown(directory, -1, os.getgid(), follow_symlinks=False)
    info = directory.lstat()
    identity = (info.st_dev, info.st_ino)
    try:
        return owned._run([owned.SANDBOX, "do", "--sandbox-name=" + name,
            "--mount", "type=bind,source=" + str(directory) + ",destination=/tmp",
            "--", "/usr/bin/python3", "-I", "-c", code], timeout=timeout, limit=65536, input_bytes=input_bytes)
    finally:
        with owned._absolute_deadline(7):
            owned._run([owned.SANDBOX, "delete", name, "--force"], timeout=5, limit=65536, cleanup=True)
            owned._remove_request_tmp(directory, identity)


def run():
    worker = load_worker()
    phase = "configuration"
    started = time.monotonic()
    outcomes = {}
    try:
        worker.configuration()
        phase = "clean_compile"
        response = worker.compile_payload(payload(clean_source()))
        outcomes[phase] = response["protocol"] == "hirewiz-tex-v1" and response["size"] > 0
        phase = "absolute_file_refusal"
        try:
            worker.compile_payload(payload(clean_source(r"\input{/etc/passwd}")))
        except worker.CompileRefused:
            outcomes[phase] = True
        else:
            outcomes[phase] = False
        phase = "unsupported_package_refusal"
        try:
            worker.compile_payload(payload(r"\documentclass{article}\usepackage{hirewizmissingpackage}\begin{document}Synthetic\end{document}"))
        except worker.CompileRefused:
            outcomes[phase] = True
        else:
            outcomes[phase] = False
        phase = "shell_escape_disabled"
        code, output = direct_sandbox(worker, SHELL_ESCAPE, 28,
            payload(clean_source(r"\immediate\write18{touch /tmp/project/shell-escape-marker} Synthetic shell escape probe.")))
        outcomes[phase] = code == 0 and json.loads(output) == {"compiled": True, "shell_escape_disabled": True}
        phase = "sandbox_network_filesystem_environment"
        os.environ["HIREWIZ_TEX_SYNTHETIC_SENTINEL"] = "synthetic_not_a_secret"
        code, output = direct_sandbox(worker, ISOLATION, 5)
        isolation = json.loads(output)
        outcomes[phase] = code == 0 and isolation == {
            "network_denied": True, "root_readonly": True, "tmp_writable": True, "ambient_env_absent": True}
        phase = "launcher_timeout_cleanup"
        timeout_started = time.monotonic()
        try:
            direct_sandbox(worker, "import time; time.sleep(90)", 2)
        except worker.launcher()._LauncherRefused as error:
            # Either timer expiry or the synchronous remaining-time check can
            # end the same two-second deadline. Native deletion is in finally.
            outcomes[phase] = (error.authorized and error.outcome in {"deadline", "failed"}
                               and 1.8 <= time.monotonic() - timeout_started <= 11)
        else:
            outcomes[phase] = False
        phase = "compiler_resource_refusal"
        try:
            worker.compile_payload(payload(clean_source(r"\loop\iftrue\repeat")))
        except worker.CompileRefused:
            outcomes[phase] = True
        else:
            outcomes[phase] = False
        phase = "clean_after_refusals"
        response = worker.compile_payload(payload(clean_source("Created Python reports.")))
        outcomes[phase] = response["size"] > 0 and not worker.QUARANTINED.is_set()
        success = len(outcomes) == 8 and all(outcomes.values())
        runtime_paths = ("/processor.py", "/opt/tex/cloud_service.py", "/opt/tex/owned_launcher.py",
                         "/opt/tex/policy.json", "/opt/tex/native_acceptance.py")
        runtime_hashes = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in runtime_paths}
        print(json.dumps({"version": 1, "suite": "hirewiz-native-tex-cloud-v1", "success": success,
                          "cases": outcomes, "elapsed_ms": int((time.monotonic() - started) * 1000),
                          "runtime_sha256": runtime_hashes,
                          "production_http_acceptance": False}, sort_keys=True))
        return 0 if success else 1
    except BaseException as error:
        # Synthetic-only bounded categories, never exception messages, TeX,
        # filenames or processor output. Preserve the failure's actual phase.
        failure = {"exception_class": type(error).__name__[:64]}
        context = error
        for _ in range(3):
            if type(getattr(context, "authorized", None)) is bool:
                failure["authorized"] = context.authorized
            if getattr(context, "outcome", None) in {"deadline", "failed", "reap_failed", "unexpected_exit"}:
                failure["outcome"] = context.outcome
            code = getattr(context, "code", None)
            if type(code) is int and -255 <= code <= 255:
                failure["observed_code"] = code
            context = getattr(context, "__context__", None)
            if context is None:
                break
        print(json.dumps({"version": 1, "suite": "hirewiz-native-tex-cloud-v1", "success": False,
                          "failed_phase": phase, "failure": failure, "completed_cases": outcomes,
                          "production_http_acceptance": False}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
