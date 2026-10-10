"""Private Cloud Run adapter for the processor; deploy only after isolation review.

Managed Cloud Run IAM/ingress and an egress-deny policy are prerequisites, not
implemented by this HTTP wrapper. Never expose it through a public grant.
"""
import hmac
import json
import os
import pathlib
import shutil
import signal
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

LOCK = threading.Lock()


def compile_payload(payload):
    # The processor and its TeX children share an owned process group. Killing
    # only Python on timeout can leave a compiler running in a warm instance.
    child = subprocess.Popen(["python3", "-I", "/processor.py"], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env={"PATH": "/usr/bin:/bin"},
        start_new_session=True)
    try:
        output, _ = child.communicate(payload, timeout=25)
        if child.returncode or len(output) > 15 * 1024 * 1024:
            raise ValueError("compile_refused")
        return json.loads(output)
    finally:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.communicate()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.connection.settimeout(10)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(422)
            return
        policy = os.environ.get("NATIVE_TEX_ISOLATION_POLICY_SHA256", "")
        image = os.environ.get("NATIVE_TEX_IMAGE_DIGEST", "")
        if (self.path != "/compile" or not policy or "@sha256:" not in image
                or not hmac.compare_digest(self.headers.get("X-HireWiz-TeX-Policy", ""), policy)
                or not 0 < length <= 8 * 1024 * 1024 or not LOCK.acquire(blocking=False)):
            self.send_error(503)
            return
        try:
            payload = self.rfile.read(length)
            response = compile_payload(payload)
            response["renderer_image"] = image
            response["isolation_policy_sha256"] = policy
            output = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(output)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(output)
        except Exception:
            self.send_error(422, "Compilation refused; use original/custom PDF")
        finally:
            shutil.rmtree(pathlib.Path("/work/project"), ignore_errors=True)
            LOCK.release()


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()
