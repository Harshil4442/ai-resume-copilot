"""Only an authenticated clock rejection may expose a numeric synchronization hint."""
import pytest
from fastapi import HTTPException, Request

from app.domains.candidate_ingress.contracts import IngressClockSynchronizationRequired
from app.routers import candidate_ingress


@pytest.mark.parametrize("method,path,exposes_hint", [
    ("GET", "/api/auth/candidate/v1/availability", True),
    ("POST", "/api/auth/candidate/v1/login", False),
])
def test_clock_hint_is_finite_and_never_turns_a_rejected_request_into_admission(monkeypatch, method, path, exposes_hint):
    class Rejected:
        def authorize(self, *args, **kwargs):
            raise IngressClockSynchronizationRequired(1_800_000_000_000, 1_800_000_000_005)

    monkeypatch.setattr(candidate_ingress.replay, "production_candidate_ingress", lambda: Rejected())
    request = Request({"type": "http", "method": method, "scheme": "https", "path": path,
        "raw_path": path.encode(), "query_string": b"", "root_path": "",
        "headers": [(b"x-hirewiz-candidate-ingress", b"synthetic-header")]})
    request.state._bounded_sensitive_body = b""
    with pytest.raises(HTTPException) as failure:
        candidate_ingress.require_candidate_ingress(request)
    assert failure.value.status_code == 503
    headers = failure.value.headers
    assert headers["Cache-Control"] == "private, no-store"
    assert ("X-Hirewiz-Ingress-Time-Lower" in headers) is exposes_hint
    if exposes_hint:
        assert headers["X-Hirewiz-Ingress-Time-Lower"] == "1800000000000"
        assert headers["X-Hirewiz-Ingress-Time-Upper"] == "1800000000005"
    assert getattr(request.state, "_private_candidate_ingress", None) is None
