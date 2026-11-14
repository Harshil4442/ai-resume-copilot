"""Forward one Razorpay Test webhook route to a disposable loopback API."""

from __future__ import annotations

import argparse
import http.client
import socket
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Timer

LOOPBACK = "127.0.0.1"
WEBHOOK_PATH = "/api/billing/webhooks/razorpay"
WEBHOOK_HEADERS = ("X-Razorpay-Signature", "X-Razorpay-Event-Id")
MAX_BODY_BYTES = 1_000_000
REQUEST_TIMEOUT_SECONDS = 2.0
UPSTREAM_TIMEOUT_SECONDS = 2.0


def close_stream(stream: socket.socket) -> None:
    try:
        stream.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


class WebhookIngress(HTTPServer):
    request_queue_size = 8

    def __init__(self, listen_port: int, upstream_port: int):
        if not 1 <= upstream_port <= 65535 or listen_port == upstream_port:
            raise ValueError("Expected two distinct loopback ports")
        self.upstream_port = upstream_port
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
        self.send_error(forward_webhook(self.server.upstream_port, body, headers))


def forward_webhook(port: int, body: bytes, headers: dict[str, str]) -> int:
    """Relay a single attempt and return only the upstream response status."""
    deadline = time.monotonic() + UPSTREAM_TIMEOUT_SECONDS
    connection = http.client.HTTPConnection(LOOPBACK, port, timeout=UPSTREAM_TIMEOUT_SECONDS)
    timer = None
    try:
        connection.connect()
        stream = connection.sock
        remaining = deadline - time.monotonic()
        if stream is None or remaining <= 0:
            return 504
        timer = Timer(remaining, close_stream, (stream,))
        timer.daemon = True
        timer.start()
        connection.request("POST", WEBHOOK_PATH, body=body, headers=headers)
        status = connection.getresponse().status
        return status if 200 <= status <= 599 else 502
    except TimeoutError:
        return 504
    except (ValueError, OSError, http.client.HTTPException):
        return 504 if time.monotonic() >= deadline else 502
    finally:
        if timer is not None:
            timer.cancel()
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-port", required=True, type=int)
    parser.add_argument("--upstream-port", required=True, type=int)
    args = parser.parse_args()
    try:
        with WebhookIngress(args.listen_port, args.upstream_port) as ingress:
            print(f"Razorpay Test receiver listening on {LOOPBACK}:{ingress.server_port}", flush=True)
            ingress.serve_forever()
    except KeyboardInterrupt:
        pass
    except (OSError, OverflowError, ValueError):
        parser.exit(1, "Razorpay Test receiver could not start.\n")


if __name__ == "__main__":
    main()
