"""Local Test receiver boundary with no Razorpay or other external network calls."""

import hashlib
import hmac
import json
import os
import socket
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread

import pytest
from backend.app.models import EntitlementLedger, PaymentEvent, PaymentOrder
from test_razorpay_billing import (
    _accepted_legacy_order,
    _capture_payload,
    _signature,
    client,
    enabled_checkout,
    session_factory,
)

from scripts import razorpay_test_webhook_ingress as ingress

assert client and session_factory and enabled_checkout


class UpstreamHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        server = self.server
        body = self.rfile.read(int(self.headers["Content-Length"]))
        server.received.append((self.path, body, dict(self.headers.items())))
        if server.release_response is not None:
            server.release_response.wait(2)
            return
        if server.application is None:
            status = server.response_status
        else:
            reply = server.application.post(self.path, content=body, headers=dict(self.headers))
            status = reply.status_code
        self.send_response(status)
        self.send_header("Location", "/must-not-follow")
        self.send_header("Set-Cookie", "must-not-relay=private")
        self.end_headers()
        self.wfile.write(b"private upstream response")

    def log_message(self, format, *args):
        pass


@contextmanager
def running(server):
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
        assert not thread.is_alive()


@pytest.fixture
def relay():
    upstream = ThreadingHTTPServer((ingress.LOOPBACK, 0), UpstreamHandler)
    upstream.received = []
    upstream.application = None
    upstream.release_response = None
    upstream.response_status = 200
    receiver = ingress.WebhookIngress(0, upstream.server_port)
    with running(upstream), running(receiver):
        yield receiver, upstream
        if upstream.release_response is not None:
            upstream.release_response.set()


def send(receiver, *, method="POST", path=ingress.WEBHOOK_PATH, body=b"{}", headers=None):
    connection = HTTPConnection(ingress.LOOPBACK, receiver.server_port, timeout=2)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_preserves_paid_webhook_raw_body_and_headers_for_actual_app(
    relay, client, enabled_checkout, session_factory, capsys,
):
    receiver, upstream = relay
    upstream.application = client
    created = _accepted_legacy_order(client)
    with session_factory() as db:
        order = db.query(PaymentOrder).filter_by(public_id=created["order_id"]).one()
        payload = _capture_payload(order) | {"synthetic": "Résumé"}
    body = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()
    headers = {
        "X-Razorpay-Event-Id": "evt_owned_test_relay",
        "X-Razorpay-Signature": _signature(body),
        "Cookie": "must-not-forward=private",
        "Authorization": "must-not-forward",
    }

    for _ in range(2):
        status, response_headers, response_body = send(receiver, body=body, headers=headers)
        assert status == 200
        assert response_headers == {"Connection": "close", "Content-Length": "0"}
        assert response_body == b""

    assert len(upstream.received) == 2
    for path, observed, forwarded in upstream.received:
        assert path == ingress.WEBHOOK_PATH and observed == body
        assert {name: forwarded[name] for name in ingress.WEBHOOK_HEADERS} == {
            name: headers[name] for name in ingress.WEBHOOK_HEADERS
        }
        assert {"Cookie", "Authorization"}.isdisjoint(forwarded)
    with session_factory() as db:
        assert db.query(PaymentEvent).one().provider_event_id == "evt_owned_test_relay"
        assert db.query(EntitlementLedger).count() == 1
    assert enabled_checkout == []
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("method", ["GET", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS", "CONNECT"])
def test_refuses_other_methods_without_forwarding(relay, method):
    receiver, upstream = relay
    assert send(receiver, method=method)[0] == 405
    assert upstream.received == []


@pytest.mark.parametrize("path", [
    "/", "/api/health", ingress.WEBHOOK_PATH + "?q=1", ingress.WEBHOOK_PATH + "/",
    "/" + ingress.WEBHOOK_PATH, ingress.WEBHOOK_PATH.replace("razorpay", "%72azorpay"),
    "http://127.0.0.1" + ingress.WEBHOOK_PATH,
])
def test_refuses_nonexact_request_targets(relay, path):
    receiver, upstream = relay
    assert send(receiver, path=path)[0] == 404
    assert upstream.received == []


@pytest.mark.parametrize(("body", "headers", "status"), [
    (b"", {}, 400),
    (b"{}", {"Content-Length": str(ingress.MAX_BODY_BYTES + 1)}, 413),
    (b"{}", {"Transfer-Encoding": "chunked"}, 400),
    (b"{}", {"Content-Encoding": "gzip"}, 400),
])
def test_refuses_unbounded_or_transformed_body(relay, body, headers, status):
    receiver, upstream = relay
    assert send(receiver, body=body, headers=headers)[0] == status
    assert upstream.received == []


@pytest.mark.parametrize("status", [204, 302, 400, 429, 503])
def test_returns_only_upstream_status_without_redirect_or_response_body(relay, status):
    receiver, upstream = relay
    upstream.response_status = status
    observed, headers, body = send(receiver)
    assert observed == status
    assert {"Location", "Set-Cookie"}.isdisjoint(headers)
    assert body == b""
    assert len(upstream.received) == 1


def test_times_out_without_retry(relay, monkeypatch):
    receiver, upstream = relay
    monkeypatch.setattr(ingress, "UPSTREAM_TIMEOUT_SECONDS", 0.1)
    upstream.release_response = Event()
    assert send(receiver)[0] == 504
    assert len(upstream.received) == 1


def test_closes_stalled_client_without_forwarding(relay, monkeypatch):
    receiver, upstream = relay
    monkeypatch.setattr(ingress, "REQUEST_TIMEOUT_SECONDS", 0.1)
    with socket.create_connection((ingress.LOOPBACK, receiver.server_port), timeout=1) as stream:
        stream.sendall(f"POST {ingress.WEBHOOK_PATH} HTTP/1.1\r\n".encode())
        assert stream.recv(128) == b""
    assert upstream.received == []


def test_refuses_duplicate_authentication_headers_without_forwarding(relay):
    receiver, upstream = relay
    wire = (
        f"POST {ingress.WEBHOOK_PATH} HTTP/1.1\r\n"
        "Content-Length: 2\r\nX-Razorpay-Event-Id: first\r\nX-Razorpay-Event-Id: second\r\n\r\n{}"
    ).encode()
    with socket.create_connection((ingress.LOOPBACK, receiver.server_port), timeout=1) as stream:
        stream.sendall(wire)
        with stream.makefile("rb") as reply:
            assert reply.readline().split()[1] == b"400"
    assert upstream.received == []


def test_holds_only_the_selected_signed_order_and_payment_and_releases_on_removal(relay, tmp_path):
    receiver, upstream = relay
    tmp_path.chmod(0o700)
    secret = "private-synthetic-test-webhook-secret"
    receiver.hold = ingress.TestDeliveryHold(tmp_path, secret)
    rule = tmp_path / "hold.json"
    rule.write_text('{"order":"order_hold123","payment":"pay_hold123"}')
    rule.chmod(0o600)

    def event(name, payload):
        body = json.dumps({"event": name, "payload": payload}).encode()
        signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return body, {"X-Razorpay-Signature": signature, "X-Razorpay-Event-Id": "evt_synthetic_delivery"}

    def deliver(packet):
        body, headers = packet
        return send(receiver, body=body, headers=headers)[0]

    capture = event("payment.captured", {"payment": {"entity": {"id": "pay_hold123", "order_id": "order_hold123"}}})
    paid = event("order.paid", {"order": {"entity": {"id": "order_hold123"}}})
    refund = event("refund.processed", {"refund": {"entity": {"payment_id": "pay_hold123"}}})
    foreign = event("payment.captured", {"payment": {"entity": {"id": "pay_other123", "order_id": "order_other123"}}})
    unsigned = (capture[0], capture[1] | {"X-Razorpay-Signature": "0" * 64})
    assert deliver(capture) == deliver(paid) == deliver(refund) == 503
    assert upstream.received == []
    assert deliver(foreign) == deliver(unsigned) == 200
    rule.unlink()
    for packet in (capture, paid, refund):
        assert deliver(packet) == 200
    assert [row[1] for row in upstream.received] == [foreign[0], unsigned[0], capture[0], paid[0], refund[0]]
    assert [row[2]["X-Razorpay-Event-Id"] for row in upstream.received[-3:]] == ["evt_synthetic_delivery"] * 3


def test_rejects_invalid_test_control_without_reading_links_or_named_pipes(relay, tmp_path):
    receiver, upstream = relay
    tmp_path.chmod(0o700)
    receiver.hold = ingress.TestDeliveryHold(tmp_path, "private-synthetic-test-webhook-secret")
    path = tmp_path / "hold.json"
    other = tmp_path / "other"
    other.write_text('{"order":"order_other123"}')
    path.symlink_to(other)
    assert send(receiver)[0] == 503
    path.unlink()
    os.mkfifo(path, 0o600)
    assert send(receiver)[0] == 503
    path.unlink()
    path.write_text('{"all":"unbounded"}')
    path.chmod(0o600)
    assert send(receiver)[0] == 503
    assert upstream.received == []
