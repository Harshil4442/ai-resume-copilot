"""Synthetic Linux kernel regression; not a production image input.

Run in the existing compiler image with this probe, the TeX service and the
unchanged document launcher mounted read-only. It uses no provider or payload.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if os.getpid() == 1:
    # Prove adoption by the actual non-PID1 request controller, rather than
    # relying on an outer container init's orphan ownership.
    raise SystemExit(subprocess.run([sys.executable, "-I", __file__, *sys.argv[1:]],
                                   stdin=subprocess.DEVNULL).returncode)


root = Path(__file__).resolve().parent.parent.parent
parser = argparse.ArgumentParser()
parser.add_argument("--worker", default="/worker.py" if Path("/worker.py").is_file()
                    else str(root / "infra/native-tex/cloud_service.py"))
parser.add_argument("--owned", default="/owned.py" if Path("/owned.py").is_file()
                    else str(root / "infra/document-processing/service.py"))
options = parser.parse_args()
worker = load("tex_worker_probe", options.worker)
owned = load("tex_owned_probe", options.owned)
worker._initialize_subreaper()
shim = Path("/tmp/synthetic-sandbox")
shim.write_text("#!" + sys.executable + "\n" + """
import os,subprocess,sys,time
from pathlib import Path
if sys.argv[1]=='delete':raise SystemExit(0)
name=sys.argv[2].split('=',1)[1]
child=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(90)'],
                       stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
Path('/tmp/'+name).write_text(str(os.getpid())+' '+str(child.pid))
if sys.argv[3]=='timeout':time.sleep(90)
raise SystemExit(int(sys.argv[3]))
""")
shim.chmod(0o700)
owned.SANDBOX = str(shim)
worker._controller_cleanup_adapter(owned)
outcomes = {}
unrelated = subprocess.Popen([sys.executable, "-I", "-c", "raise SystemExit(23)"],
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    start_new_session=True)
observed = None
deadline = time.monotonic() + 2
while observed is None and time.monotonic() < deadline:
    observed = os.waitid(os.P_PID, unrelated.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    if observed is None:
        time.sleep(0.01)
assert observed is not None and observed.si_code == os.CLD_EXITED and observed.si_status == 23
for case in ("0", "1", "timeout"):
    name = "document-" + uuid.uuid4().hex
    arguments = [str(shim), "do", "--sandbox-name=" + name, case]
    try:
        code, output = owned._run(arguments, timeout=0.7, limit=65536, allow_outcome=True)
        assert case != "timeout" and code == int(case) and output == b"", (case, code, len(output))
    except owned._LauncherRefused as error:
        assert case == "timeout" and error.authorized and error.outcome in {"deadline", "failed"}
    assert owned._run([str(shim), "delete", name, "--force"],
                      timeout=5, limit=65536, cleanup=True) == (0, b"")
    group, descendant = map(int, (Path("/tmp") / name).read_text().split())
    assert not (Path("/proc") / str(descendant)).exists()
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError("owned group still exists")
    assert not owned._CREATION_FAILED.is_set() and not owned._CREATION_PENDING.is_set()
    # This unrelated group's terminal PID remains anchored and unreaped during
    # every owned-group cleanup. A child sweep would make this native read fail.
    untouched = os.waitid(os.P_PID, unrelated.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    assert untouched is not None and untouched.si_code == os.CLD_EXITED and untouched.si_status == 23
    assert unrelated.returncode is None and os.getpgid(unrelated.pid) == unrelated.pid
    outcomes[case] = True
assert unrelated.wait(timeout=1) == 23
print(json.dumps({"linux_adopted_child_regression": outcomes,
                  "final_native_group_absence": True,
                  "actual_non_pid1_controller": os.getpid() != 1,
                  "no_unrelated_child_wait": True}, sort_keys=True))
