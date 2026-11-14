"""Forward one Razorpay Test webhook route to a disposable loopback API."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
import json
import os
import re
import socket
import stat
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Timer
from typing import Literal

LOOPBACK = "127.0.0.1"
WEBHOOK_PATH = "/api/billing/webhooks/razorpay"
WEBHOOK_HEADERS = ("X-Razorpay-Signature", "X-Razorpay-Event-Id")
MAX_BODY_BYTES = 1_000_000
REQUEST_TIMEOUT_SECONDS = 2.0
UPSTREAM_TIMEOUT_SECONDS = 2.0
HELD_EVENTS = frozenset({"order.paid", "payment.captured", "refund.processed"})
SettlementReply = Literal["processed", "duplicate"]


@dataclass(frozen=True)
class AckLossAttempt:
    order_digest: str
    event_digest: str


class TestDeliveryHold:
    """Control delivery of an exact signed event in the disposable Test lab."""

    def __init__(self, directory: Path, secret: str):
        if len(secret) < 32 or directory.is_symlink() or not directory.is_dir() or directory.stat().st_mode & 0o077:
            raise ValueError("Test delivery control requires a private directory and webhook secret")
        self.directory = directory
        self.secret = secret.encode()

    def check(self, body: bytes, headers: dict[str, str]) -> tuple[int | None, AckLossAttempt | None]:
        path = self.directory / "hold.json"
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as file:
                metadata = os.fstat(file.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
                    return 503, None
                raw = file.read(513)
        except FileNotFoundError:
            return None, None
        except OSError:
            return 503, None
        try:
            rules = json.loads(raw)
            if len(raw) > 512 or type(rules) is not dict or not rules or any(
                key not in {"order", "payment", "ack_loss_order"} or type(value) is not str or not re.fullmatch(
                    ("pay_" if key == "payment" else "order_") + r"[A-Za-z0-9]{1,114}", value
                ) for key, value in rules.items()
            ) or ("ack_loss_order" in rules and len(rules) != 1):
                return 503, None
        except (ValueError, RecursionError):
            return 503, None

        signature = headers.get("X-Razorpay-Signature", "")
        expected = hmac.new(self.secret, body, hashlib.sha256).hexdigest()
        if not re.fullmatch(r"[0-9a-f]{64}", signature) or not hmac.compare_digest(signature, expected):
            return None, None
        try:
            event = json.loads(body)
            if not isinstance(event, dict) or event.get("event") not in HELD_EVENTS:
                return None, None
            payload = event["payload"]
            order = payload.get("order", {}).get("entity", {})
            if "ack_loss_order" in rules:
                event_id = headers.get("X-Razorpay-Event-Id", "")
                if (event["event"] == "order.paid" and order.get("id") == rules["ack_loss_order"]
                        and 1 <= len(event_id) <= 160 and event_id == event_id.strip()):
                    return None, AckLossAttempt(
                        hashlib.sha256(rules["ack_loss_order"].encode()).hexdigest(),
                        hashlib.sha256(event_id.encode()).hexdigest(),
                    )
                return None, None

            payment = payload.get("payment", {}).get("entity", {})
            refund = payload.get("refund", {}).get("entity", {})
            if ("order" in rules and rules["order"] in (order.get("id"), payment.get("order_id"))
                    or "payment" in rules and rules["payment"] in (payment.get("id"), refund.get("payment_id"))):
                return 503, None
        except (AttributeError, KeyError, TypeError, ValueError, RecursionError):
            # Let the application validate malformed or unknown signed events.
            return None, None
        return None, None

    def _write_receipt(self, marker: Path, digest: bytes) -> bool:
        # Exclusive link makes the complete receipt visible atomically and keeps
        # the rule one-shot across restarts. The original empty marker still wins.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as file:
                temporary = Path(file.name)
                file.write(digest)
            os.link(temporary, marker, follow_symlinks=False)
        except OSError:
            return False
        finally:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink()
        return True

    def note_settlement(self, attempt: AckLossAttempt, outcome: SettlementReply) -> bool:
        marker = self.directory / f"ack-loss-{attempt.order_digest}.used"
        digest = attempt.event_digest.encode("ascii")
        if outcome == "processed":
            return self._write_receipt(marker, digest)
        try:
            descriptor = os.open(marker, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as file:
                metadata = os.fstat(file.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077 or file.read(65) != digest:
                    return False
        except OSError:
            return False
        self._write_receipt(marker.with_suffix(".replayed"), digest)
        return False


def close_stream(stream: socket.socket) -> None:
    try:
        stream.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


class WebhookIngress(HTTPServer):
    request_queue_size = 8

    def __init__(self, listen_port: int, upstream_port: int, *, hold: TestDeliveryHold | None = None):
        if not 1 <= upstream_port <= 65535 or listen_port == upstream_port:
            raise ValueError("Expected two distinct loopback ports")
        self.upstream_port = upstream_port
        self.hold = hold
        super().__init__((LOOPBACK, listen_port), WebhookHandler)
        if self.server_port == upstream_port:
            self.server_close()
            raise ValueError("Expected two distinct loopback ports")

    def handle_error(self, request, client_address):
        # Exceptions may contain request bytes. Close without diagnostics.
        pass


class WebhookHandler(BaseHTTPRequestHandler):
    server: WebhookIngress
    default_request_version = "HTTP/1.0"

    def handle(self) -> None:
        self.connection.settimeout(REQUEST_TIMEOUT_SECONDS)
        self.request_timer = Timer(REQUEST_TIMEOUT_SECONDS, close_stream, (self.connection,))
        self.request_timer.daemon = True
        self.request_timer.start()
        try:
            self.close_connection = True
            self.handle_one_request()
        finally:
            self.request_timer.cancel()

    def log_message(self, format, *args) -> None:
        pass

    def send_error(self, code, message=None, explain=None) -> None:
        self.close_connection = True
        self.send_response_only(code)
        self.send_header("Connection", "close")
        if code not in (204, 304):
            self.send_header("Content-Length", "0")
        self.end_headers()

    def parse_request(self) -> bool:
        if not super().parse_request():
            return False
        if self.command != "POST":
            self.send_error(405)
            return False
        # The base parser normalizes leading double slashes in self.path.
        if self.requestline.split()[1] != WEBHOOK_PATH:
            self.send_error(404)
            return False
        return True

    def do_POST(self) -> None:
        lengths = self.headers.get_all("Content-Length", [])
        if (len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdecimal()
                or len(lengths[0]) > 10 or self.headers.get("Transfer-Encoding") is not None
                or self.headers.get("Content-Encoding") is not None):
            self.send_error(400)
            return
        length = int(lengths[0])
        if not length or length > MAX_BODY_BYTES:
            self.send_error(413 if length else 400)
            return
        headers = {"Content-Type": "application/json"}
        for name in WEBHOOK_HEADERS:
            values = self.headers.get_all(name, [])
            if len(values) > 1 or any(len(value) > 256 or not value.isprintable()
                                       for value in values if value):
                self.send_error(400)
                return
            if values:
                headers[name] = values[0]
        body = self.rfile.read(length)
        if len(body) != length:
            self.send_error(400)
            return
        self.request_timer.cancel()
        hold = self.server.hold
        refused, acknowledgement = hold.check(body, headers) if hold is not None else (None, None)
        if refused is not None:
            self.send_error(refused)
            return
        status, outcome = forward_webhook(self.server.upstream_port, body, headers,
                                          inspect_settlement=acknowledgement is not None)
        if outcome and hold is not None and acknowledgement is not None and hold.note_settlement(acknowledgement, outcome):
            status = 503
        self.send_error(status)


def _settlement_reply(response: http.client.HTTPResponse) -> SettlementReply | None:
    try:
        raw = response.read(129)
        result = json.loads(raw) if len(raw) <= 128 else None
    except (ValueError, OSError, http.client.HTTPException, RecursionError):
        return None
    if isinstance(result, dict) and result.get("ok") is True:
        if result.get("status") == "processed":
            return "processed"
        if result.get("status") == "duplicate":
            return "duplicate"
    return None


def forward_webhook(port: int, body: bytes, headers: dict[str, str], *,
                    inspect_settlement: bool = False) -> tuple[int, SettlementReply | None]:
    """Relay once and check the app's tiny processing result only when armed."""
    deadline = time.monotonic() + UPSTREAM_TIMEOUT_SECONDS
    connection = http.client.HTTPConnection(LOOPBACK, port, timeout=UPSTREAM_TIMEOUT_SECONDS)
    timer = None
    try:
        connection.connect()
        stream = connection.sock
        remaining = deadline - time.monotonic()
        if stream is None or remaining <= 0:
            return 504, None
        timer = Timer(remaining, close_stream, (stream,))
        timer.daemon = True
        timer.start()
        connection.request("POST", WEBHOOK_PATH, body=body, headers=headers)
        response = connection.getresponse()
        status = response.status if 200 <= response.status <= 599 else 502
        return status, _settlement_reply(response) if inspect_settlement and 200 <= status < 300 else None
    except TimeoutError:
        return 504, None
    except (ValueError, OSError, http.client.HTTPException):
        return (504 if time.monotonic() >= deadline else 502), None
    finally:
        if timer is not None:
            timer.cancel()
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-port", required=True, type=int)
    parser.add_argument("--upstream-port", required=True, type=int)
    parser.add_argument("--test-control-dir", type=Path)
    args = parser.parse_args()
    try:
        hold = (TestDeliveryHold(args.test_control_dir, os.environ.get("HIREWIZ_TEST_WEBHOOK_SECRET", ""))
                if args.test_control_dir is not None else None)
        with WebhookIngress(args.listen_port, args.upstream_port, hold=hold) as ingress:
            print(f"Razorpay Test receiver listening on {LOOPBACK}:{ingress.server_port}", flush=True)
            ingress.serve_forever()
    except KeyboardInterrupt:
        pass
    except (OSError, OverflowError, ValueError):
        parser.exit(1, "Razorpay Test receiver could not start.\n")


if __name__ == "__main__":
    main()
