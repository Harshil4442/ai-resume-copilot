"""Credential-free, fixed-target loopback relay for an owned internal emulator.

No DNS, user-selected destination, HTTP proxy control, retries or payload logs.
The private configuration is derived from actual matching Docker observations.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import select
import socket
import socketserver
import sys
import threading
import time
from pathlib import Path

PRIVATE = tuple(ipaddress.IPv4Network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
MAX_BYTES = 128 * 1024 * 1024
LIFETIME_SECONDS = 900


def validate_target(ip: str, subnet: str, port: int) -> tuple[str, int]:
    address, network = ipaddress.IPv4Address(ip), ipaddress.IPv4Network(subnet, strict=True)
    if (type(port) is not int or port != 8080 or address.version != 4 or network.version != 4
            or not any(network.subnet_of(allowed) for allowed in PRIVATE)
            or address not in network or address in (network.network_address, network.broadcast_address)):
        raise ValueError("Owned emulator target is outside its exact private network")
    return str(address), port


def forward_pair(left, right, *, deadline, account):
    """Byte-preserving bounded stream; no interpretation or changed destination."""
    left.settimeout(2)
    right.settimeout(2)
    while time.monotonic() < deadline:
        ready, _, _ = select.select([left, right], [], [], min(1, max(0, deadline - time.monotonic())))
        for source in ready:
            data = source.recv(64 * 1024)
            if not data:
                return
            account(len(data))
            (right if source is left else left).sendall(data)


def serve(config, ready_path):
    if (set(config) != {"target_ip", "subnet", "target_port", "listen_port", "nonce", "network_id", "container_id"}
            or type(config["listen_port"]) is not int or not 1024 <= config["listen_port"] <= 65535
            or config["listen_port"] in {55433, 55434}
            or re.fullmatch(r"[a-f0-9]{32}", config["nonce"]) is None
            or any(re.fullmatch(r"[a-f0-9]{64}", config[key]) is None for key in ("network_id", "container_id"))):
        raise ValueError("Owned relay configuration differs")
    target = validate_target(config["target_ip"], config["subnet"], config["target_port"])
    deadline, total = time.monotonic() + LIFETIME_SECONDS, [0]
    budget_lock, slots = threading.Lock(), threading.BoundedSemaphore(8)
    def account(count):
        with budget_lock:
            total[0] += count
            if total[0] > MAX_BYTES:
                raise ValueError("Owned relay byte budget exceeded")
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            if self.client_address[0] != "127.0.0.1" or not slots.acquire(blocking=False):
                return
            try:
                # One connection attempt to the validated immutable endpoint.
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as backend:
                    backend.settimeout(2)
                    backend.connect(target)
                    forward_pair(self.request, backend, deadline=deadline, account=account)
            except Exception:
                pass  # Close the stream; never retry or log binary request data.
            finally:
                slots.release()
    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True
        allow_reuse_address = False
        request_queue_size = 8
        def handle_error(self, request, client_address):
            pass
    with Server(("127.0.0.1", config["listen_port"]), Handler) as server:
        pending = ready_path.with_name("relay-ready.pending")
        descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump({"nonce": config["nonce"], "pid": os.getpid(), "port": config["listen_port"]}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(pending, ready_path, follow_symlinks=False)
        pending.unlink()
        server.timeout = .25
        while time.monotonic() < deadline:
            server.handle_request()


def main():
    try:
        if len(sys.argv) != 4:
            raise ValueError
        path, expected, ready = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
        info = path.lstat()
        if (path.is_symlink() or not path.is_file() or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > 4096
                or ready.parent != path.parent):
            raise ValueError
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError
        serve(json.loads(raw), ready)
        return 0
    except BaseException:
        print('{"error":"Owned emulator relay failed"}', file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
