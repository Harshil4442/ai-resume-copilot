"""Local-only HTTP harness. Business/auth/SQL routes are not replaced.

Finite external transport fixtures are installed before importing the real app.
No fixture routes or dependency overrides are added to the shipping API.
"""
from __future__ import annotations

import atexit
import json
import os
import socket
import threading
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

COUNTS: Counter[str] = Counter()
LOCK = threading.Lock()
PROOF = Path(os.environ["COLD_BROWSER_PROVIDER_PROOF"])


def persist() -> None:
    with LOCK:
        temporary = PROOF.with_suffix(".tmp")
        temporary.write_text(json.dumps(dict(COUNTS), sort_keys=True) + "\n")
        temporary.replace(PROOF)


def count(name: str) -> None:
    with LOCK:
        COUNTS[name] += 1
    persist()


def forbidden(*_args, **_kwargs):
    count("forbidden_model_calls")
    raise AssertionError("Cold browser journey reached a forbidden model boundary")


def transport(request: httpx.Request) -> httpx.Response:
    count("synthetic_external_requests")
    if request.url.host == "boards-api.greenhouse.io":
        assert request.method == "GET", "Employer writes are forbidden"
        assert "authorization" not in request.headers and "cookie" not in request.headers
        base = "/v1/boards/syntheticbrowser/jobs"
        now = datetime.now(UTC)
        rows = [{
            "id": identity, "internal_job_id": identity + 1000, "title": title,
            "location": {"name": "Bengaluru, India"},
            "absolute_url": f"https://employer.example/jobs/{identity}",
            "content": "<p>Python PostgreSQL Docker services.</p>",
            "first_published": (now - timedelta(days=days)).isoformat(),
            "updated_at": now.isoformat(),
        } for identity, days, title in (
            (201, 1, "Python Engineer"), (202, 2, "Python Backend Engineer"),
            (203, 60, "Python Engineer"), (204, 1, "Product Designer"),
        )]
        if request.url.path == base:
            count("synthetic_feed_gets")
            assert COUNTS["synthetic_feed_gets"] <= 1
            return httpx.Response(200, json={"jobs": rows, "meta": {"total": 4}})
        selected = next((row for row in rows if request.url.path == f"{base}/{row['id']}"), None)
        assert selected is not None, "Unexpected employer fixture path"
        count("synthetic_form_gets")
        assert COUNTS["synthetic_form_gets"] <= 12
        return httpx.Response(200, json={**selected, "questions": [
            {"label": label, "required": True, "fields": [{"name": name, "type": kind}]}
            for label, name, kind in (
                ("First name", "first_name", "input_text"),
                ("Last name", "last_name", "input_text"),
                ("Email", "email", "input_text"),
                ("Resume", "resume", "input_file"),
            )
        ]})
    if request.url.host == "api.razorpay.com" and request.url.path == "/v1/orders":
        assert request.method == "POST"
        payload = json.loads(request.content)
        assert payload["amount"] == 64_900 and payload["currency"] == "INR"
        assert payload["partial_payment"] is False
        count("synthetic_checkout_orders")
        assert COUNTS["synthetic_checkout_orders"] == 1
        return httpx.Response(200, json={**payload, "entity": "order", "id": "order_synthetic_browser", "status": "created"})
    count("unexpected_external_requests")
    raise AssertionError("Unexpected external provider boundary")


_original_init = httpx.Client.__init__


def client_init(client, *args, **kwargs):
    assert kwargs.get("transport") is None, "Unexpected custom application transport"
    kwargs["transport"] = httpx.MockTransport(transport)
    _original_init(client, *args, **kwargs)


httpx.Client.__init__ = client_init
_original_connect = socket.socket.connect


def restricted_connect(sock, address):
    # Only the explicitly disposable SQL listener is available to this process.
    if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"} and address[1] == 55433:
        return _original_connect(sock, address)
    count("unexpected_socket_egress")
    raise AssertionError("Backend fixture attempted non-SQL socket egress")


socket.socket.connect = restricted_connect  # type: ignore[method-assign]
from backend.app.services import llm_client  # noqa: E402
from backend.app.services.generation_budget import GenerationBudget  # noqa: E402
from google import genai  # noqa: E402

for _name in (
    "_chat", "_chat_with_budget", "chat_json", "rewrite_bullets", "generate_interview_questions",
    "tailor_resume_from_evidence", "tailor_resume_mega_llm", "extract_skills_llm",
    "extract_jd_skills_llm", "analyze_job_match_mega_llm", "generate_learning_strategy_llm",
):
    setattr(llm_client, _name, forbidden)
genai.Client = forbidden
GenerationBudget.admit = forbidden

from backend.app.domains.dispatch import service as dispatch  # noqa: E402
from backend.app.main import app  # noqa: E402

assert not app.dependency_overrides
STOP = threading.Event()


def dispatch_loop():
    while not STOP.wait(0.1):
        # The real outbox/worker only processes synthetic feed GET refreshes.
        try:
            dispatch.dispatch_pending(limit=5, topics={"employer.refresh"})
        except Exception:
            count("dispatch_errors")
            raise


@app.middleware("http")
async def observe_routes(request, call_next):
    response = await call_next(request)
    route = request.scope.get("route")
    count(f"http:{request.method}:{getattr(route, 'path', 'unmatched')}:{response.status_code}")
    return response


atexit.register(STOP.set)
atexit.register(persist)
if __name__ == "__main__":
    import uvicorn
    worker = threading.Thread(target=dispatch_loop, daemon=True)
    worker.start()
    try:
        uvicorn.run(app, host="127.0.0.1", port=int(os.environ["COLD_BROWSER_BACKEND_PORT"]), access_log=False,
                    ssl_keyfile=os.environ["HIREWIZ_TEST_TLS_KEY"], ssl_certfile=os.environ["HIREWIZ_TEST_TLS_CERT"])
    finally:
        STOP.set()
        worker.join(timeout=5)
        persist()
