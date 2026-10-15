from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest
from backend.app.database import Base
from backend.app.domains.employer import connectors, models, schemas, tasks
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def source(platform: str) -> connectors.SourceContract:
    host = "example.jobs.personio.com" if platform == "personio" else "example.pinpointhq.com" if platform == "pinpoint" else "apply.workable.com"
    url = f"https://{host}/?display=fr" if platform == "personio" else f"https://{host}/pt-BR/" if platform == "pinpoint" else f"https://{host}/example/"
    return connectors.SourceContract(id=f"source-{platform}", employer="Example Employer", platform=platform,
        board_token="example", region="global", allowed_hosts=(host,), careers_url=url)


def workable() -> dict:
    return {"jobs": [{
        "shortcode": "ABC123", "code": "ENG-01", "title": "Python Engineer",
        "city": "Pune", "state": "Maharashtra", "country": "India",
        "telecommuting": False, "workplace_type": "hybrid",
        "published_on": "2026-10-07", "created_at": "2026-09-01T00:00:00Z",
        "url": "https://apply.workable.com/example/j/ABC123/",
        "application_url": "https://apply.workable.com/j/ABC123/",
        "description": "<p>Build Python services.</p><script>bad()</script>",
    }]}


def pinpoint() -> dict:
    return {"data": [{
        "id": "posting-01", "title": "Desenvolvedor Python", "deadline_at": None,
        "url": "https://example.pinpointhq.com/pt-BR/postings/publication-01",
        "path": "/pt-BR/postings/publication-01", "location": {"id": "loc-01", "name": "Lisboa"},
        "job": {"id": "job-01", "requisition_id": "ENG-01"},
        "workplace_type": "remote", "description": "<p>Build Python APIs.</p>",
        "key_responsibilities": "<ul><li>Review services.</li></ul>",
        "skills_knowledge_expertise": "SQL experience.", "benefits": "Learning support.",
        "published_at": "2026-10-07T00:00:00Z",  # Not part of this documented public contract.
    }]}


def personio(identity="123", *, description=True) -> bytes:
    text = "<jobDescriptions><jobDescription><name>Vos missions</name><value><![CDATA[<p>Build Python APIs &amp; SQL.</p><script>bad()</script>]]></value></jobDescription></jobDescriptions>" if description else "<jobDescriptions/>"
    return f"<workzag-jobs><position><id>{identity}</id><name>Ingénieur Python</name><office>Paris</office>{text}<createdAt>2026-01-01T00:00:00Z</createdAt></position></workzag-jobs>".encode()


@pytest.fixture(autouse=True)
def fast_reads(monkeypatch):
    monkeypatch.setattr(connectors, "SMARTRECRUITERS_READ_INTERVAL", 0)
    monkeypatch.setattr(connectors.time, "sleep", lambda _: None)


def scan(platform: str, data=None, *, handler=None, contract=None):
    data = data if data is not None else {"workable": workable, "pinpoint": pinpoint, "personio": personio}[platform]()
    if handler is None:
        def handler(request):
            return httpx.Response(200, content=data) if isinstance(data, bytes) else httpx.Response(200, json=data)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return connectors.fetch_postings(contract or source(platform), client)


def test_workable_public_collection_has_scoped_get_details_and_publication():
    calls = []
    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://www.workable.com/api/accounts/example?details=true"
        return httpx.Response(200, json=workable())
    row = scan("workable", handler=handler)[0]
    assert len(calls) == 2 and all(call.method == "GET" for call in calls)
    assert row["external_id"] == "ABC123" and row["requisition_id"] == "ENG-01"
    # Public Workable state is a geographical state, not a requisition status.
    assert row["location"] == "Pune, Maharashtra, India"
    assert row["publication_at"] == datetime(2026, 10, 7, tzinfo=UTC)
    assert row["source_updated_at"] is None and row["remote"] is False
    assert row["language"] == "und"
    assert row["apply_url"] == "https://apply.workable.com/j/ABC123/"
    assert "Python services" in row["description"] and "bad()" not in row["description"]


def test_workable_exact_public_redirect_closes_before_sanitized_target_read():
    calls, closed = [], []
    legacy = "https://www.workable.com/api/accounts/example?details=true"
    target = "https://apply.workable.com/api/v1/widget/accounts/example?details=true"

    class RedirectStream(httpx.SyncByteStream):
        def __iter__(self):
            pytest.fail("Redirect bodies must not be read")
            yield b""

        def close(self):
            closed.append(True)

    def handler(request):
        calls.append(request)
        assert not any(h in request.headers for h in ("Authorization", "Cookie", "X-SmartToken"))
        if str(request.url) == legacy:
            return httpx.Response(302, headers={"Location": target}, stream=RedirectStream())
        assert str(request.url) == target and len(closed) == len(calls) // 2
        return httpx.Response(200, json=workable())

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True,
                      headers={"Authorization": "Bearer synthetic", "Cookie": "synthetic=1",
                               "X-SmartToken": "synthetic"}, auth=("synthetic", "synthetic")) as client:
        row = connectors.fetch_postings(source("workable"), client)[0]
    assert [str(r.url) for r in calls] == [legacy, target, legacy, target]
    assert row["external_id"] == "ABC123" and len(closed) == 2


def test_workable_redirect_never_inherits_client_query_or_header_credentials():
    legacy = "https://www.workable.com/api/accounts/example?details=true"
    target = "https://apply.workable.com/api/v1/widget/accounts/example?details=true"
    calls = []

    def handler(request):
        calls.append(str(request.url))
        assert str(request.url) in {legacy, target}
        assert request.headers["Host"] == request.url.host
        assert not any(key in request.headers for key in (
            "Authorization", "Cookie", "X-SmartToken", "X-Api-Key"))
        if str(request.url) == legacy:
            return httpx.Response(302, headers={"Location": target})
        return httpx.Response(200, json=workable())

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True,
            params={"api_key": "synthetic-only", "details": "false"},
            headers={"X-Api-Key": "synthetic-only", "Host": "untrusted.example"},
            auth=("synthetic", "synthetic"), cookies={"session": "synthetic-only"}) as client:
        assert connectors.fetch_postings(source("workable"), client)[0]["external_id"] == "ABC123"
    assert calls == [legacy, target, legacy, target]


@pytest.mark.parametrize("platform", ["workable", "personio", "pinpoint"])
def test_public_feed_request_uses_only_declared_query_and_public_headers(platform):
    expected = {
        "workable": "https://www.workable.com/api/accounts/example?details=true",
        "personio": "https://example.jobs.personio.com/xml?language=fr",
        "pinpoint": "https://example.pinpointhq.com/pt-BR/postings.json",
    }[platform]
    calls = []
    data = {"workable": workable, "personio": personio, "pinpoint": pinpoint}[platform]()

    def handler(request):
        calls.append(request)
        assert str(request.url) == expected
        assert request.headers["Host"] == request.url.host
        assert request.headers["Accept"] == "application/json"
        assert request.headers["User-Agent"] == "HireWiz-EmployerConnector/1.0"
        assert not any(key in request.headers for key in (
            "Authorization", "Cookie", "X-SmartToken", "X-Api-Key"))
        return httpx.Response(200, content=data) if isinstance(data, bytes) else httpx.Response(200, json=data)

    with httpx.Client(transport=httpx.MockTransport(handler),
            params={"api_key": "synthetic-only", "language": "wrong", "details": "false"},
            headers={"X-SmartToken": "synthetic-only", "X-Api-Key": "synthetic-only", "Host": "untrusted.example"},
            auth=("synthetic", "synthetic"), cookies={"session": "synthetic-only"}) as client:
        assert len(connectors.fetch_postings(source(platform), client)) == 1
    assert len(calls) == 2


@pytest.mark.parametrize("location", [
    "https://untrusted.example/api/v1/widget/accounts/example?details=true",
    "http://apply.workable.com/api/v1/widget/accounts/example?details=true",
    "https://apply.workable.com/api/v1/widget/accounts/another?details=true",
    "https://apply.workable.com/api/v1/widget/accounts/example?details=false",
    "https://apply.workable.com/api/v1/widget/accounts/example?details=true&extra=1",
    "https://apply.workable.com/api/v1/widget/accounts/example?details=true#fragment",
    "https://apply.workable.com:444/api/v1/widget/accounts/example?details=true",
    "https://user@apply.workable.com/api/v1/widget/accounts/example?details=true",
    "https://example.workable.com/spi/v3/jobs",
    "/api/v1/widget/accounts/example?details=true",
    "https://apply.workable.com/api/v1/widget/accounts/%65xample?details=true",
    "https://apply.workable.com/api/v1/widget/accounts/example/../another?details=true",
])
def test_workable_redirect_cannot_change_account_host_path_query_or_credentials(location):
    calls = []

    def handler(request):
        calls.append(request)
        assert len(calls) == 1
        return httpx.Response(302, headers={"Location": location})

    with pytest.raises(connectors.ConnectorError, match="source_http_302"):
        scan("workable", handler=handler)
    assert len(calls) == 1


def test_workable_public_redirect_cannot_follow_a_second_hop():
    calls = []
    target = "https://apply.workable.com/api/v1/widget/accounts/example?details=true"

    def handler(request):
        calls.append(request)
        assert len(calls) <= 2
        return httpx.Response(302, headers={"Location": target})

    with pytest.raises(connectors.ConnectorError, match="source_http_302"):
        scan("workable", handler=handler)
    assert len(calls) == 2


def test_workable_redirected_second_snapshot_change_invalidates_whole_scan():
    reads = []
    target = "https://apply.workable.com/api/v1/widget/accounts/example?details=true"

    def handler(request):
        if request.url.host == "www.workable.com":
            return httpx.Response(302, headers={"Location": target})
        reads.append(request)
        data = workable()
        if len(reads) == 2:
            data["jobs"][0]["description"] = "Changed requirements."
        return httpx.Response(200, json=data)

    with pytest.raises(connectors.ConnectorError, match="source_changed_during_scan"):
        scan("workable", handler=handler)
    assert len(reads) == 2


def test_personio_com_locale_native_text_identity_and_unknown_publication():
    calls = []
    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://example.jobs.personio.com/xml?language=fr"
        return httpx.Response(200, content=personio())
    row = scan("personio", handler=handler)[0]
    assert len(calls) == 2
    assert row["external_id"] == "123" and row["language"] == "fr"
    assert row["title"] == "Ingénieur Python" and row["location"] == "Paris"
    assert row["canonical_url"] == "https://example.jobs.personio.com/job/123?display=fr"
    assert row["publication_at"] is None and row["source_updated_at"] is None
    assert "Python APIs & SQL" in row["description"] and "bad()" not in row["description"]


def test_personio_de_route_uses_verified_host_without_fallback_probe():
    contract = replace(source("personio"), careers_url="https://example.jobs.personio.de/",
                       allowed_hosts=("example.jobs.personio.de",))
    calls = []
    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://example.jobs.personio.de/xml?language=de"
        return httpx.Response(200, content=personio())
    assert scan("personio", handler=handler, contract=contract)[0]["language"] == "de"
    assert len(calls) == 2


def test_pinpoint_posting_variants_stay_distinct_for_same_requisition():
    data = pinpoint()
    second = {**data["data"][0], "id": "posting-02", "title": "Python Engineer",
              "url": "https://example.pinpointhq.com/pt-BR/postings/publication-02",
              "path": "/pt-BR/postings/publication-02"}
    data["data"].append(second)
    calls = []
    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://example.pinpointhq.com/pt-BR/postings.json"
        return httpx.Response(200, json=data)
    rows = scan("pinpoint", handler=handler)
    assert len(calls) == 2 and len(rows) == 2
    assert {row["external_id"] for row in rows} == {"posting-01", "posting-02"}
    assert {row["requisition_id"] for row in rows} == {"ENG-01"}
    assert rows[0]["publication_at"] is None and rows[0]["language"] == "pt-BR"
    assert rows[0]["location"] == "Lisboa" and rows[0]["remote"] is True
    assert "SQL experience" in rows[0]["description"] and "Learning support" in rows[0]["description"]


@pytest.mark.parametrize("platform", ["workable", "personio", "pinpoint"])
def test_empty_completed_collections_are_valid_but_missing_collection_is_not(platform):
    data = b"<workzag-jobs/>" if platform == "personio" else {"jobs" if platform == "workable" else "data": []}
    assert scan(platform, data) == []
    with pytest.raises(connectors.ConnectorError):
        scan(platform, b"<html/>" if platform == "personio" else {})


@pytest.mark.parametrize("platform", ["workable", "pinpoint"])
@pytest.mark.parametrize("metadata", [{"next": "https://untrusted.example/next"}, {"meta": {"total": 2}}, {"total": 2}])
def test_undocumented_pagination_or_truncation_is_not_silently_complete(platform, metadata):
    data = workable() if platform == "workable" else pinpoint()
    with pytest.raises(connectors.ConnectorError):
        scan(platform, {**data, **metadata})


@pytest.mark.parametrize("platform", ["workable", "personio", "pinpoint"])
def test_duplicates_and_late_malformed_rows_invalidate_whole_feed(platform):
    if platform == "personio":
        data = personio().replace(b"</workzag-jobs>", personio().split(b"<workzag-jobs>")[1])
    else:
        data = workable() if platform == "workable" else pinpoint()
        key = "jobs" if platform == "workable" else "data"
        data[key] += list(data[key])
    with pytest.raises(connectors.ConnectorError, match="duplicate_posting_identifiers"):
        scan(platform, data)
    invalid = personio().replace(b"</workzag-jobs>", b"<position><id>124</id></position></workzag-jobs>") if platform == "personio" else {"jobs" if platform == "workable" else "data": [{}]}
    with pytest.raises(connectors.ConnectorError):
        scan(platform, invalid)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
@pytest.mark.parametrize("doctype", [
    '<!DOCTYPE workzag-jobs SYSTEM "https://untrusted.example/external.dtd">',
    '<!DOCTYPE workzag-jobs [<!ENTITY secret SYSTEM "file:///etc/passwd">]>',
    '<!DOCTYPE workzag-jobs [<!ENTITY a "boom"><!ENTITY b "&a;&a;&a;&a;">]>',
])
def test_personio_rejects_all_dtds_before_entity_resolution(encoding, doctype):
    xml = f'<?xml version="1.0" encoding="{encoding}"?>{doctype}<workzag-jobs/>'
    with pytest.raises(connectors.ConnectorError, match="source_xml_dtd_forbidden"):
        scan("personio", xml.encode(encoding))


@pytest.mark.parametrize("body", [
    b"<workzag-jobs><position>", b"<workzag-jobs><unrecognized/></workzag-jobs>",
    b'<workzag-jobs xmlns:xi="http://www.w3.org/2001/XInclude"><xi:include href="file:///etc/passwd"/></workzag-jobs>',
    b"<workzag-jobs><position><id>1</id><id>2</id></position></workzag-jobs>",
])
def test_personio_rejects_truncation_unknown_root_records_xinclude_and_ambiguous_fields(body):
    with pytest.raises(connectors.ConnectorError):
        scan("personio", body)


def test_personio_bounds_depth_nodes_and_bytes_without_expansion(monkeypatch):
    monkeypatch.setattr(connectors, "XML_MAX_DEPTH", 3)
    with pytest.raises(connectors.ConnectorError, match="source_xml_limit_exceeded"):
        scan("personio", personio())
    monkeypatch.setattr(connectors, "XML_MAX_DEPTH", 32)
    monkeypatch.setattr(connectors, "XML_MAX_NODES", 3)
    with pytest.raises(connectors.ConnectorError, match="source_xml_limit_exceeded"):
        scan("personio", personio())
    monkeypatch.setattr(connectors, "MAX_RESPONSE_BYTES", 20)
    with pytest.raises(connectors.ConnectorError, match="source_response_too_large"):
        scan("personio", personio())


def test_personio_empty_description_cannot_be_replaced_with_invented_text():
    with pytest.raises(connectors.ConnectorError, match="incomplete_posting_payload"):
        scan("personio", personio(description=False))
    header_only = personio().replace(b"<p>Build Python APIs &amp; SQL.</p><script>bad()</script>", b"")
    with pytest.raises(connectors.ConnectorError, match="incomplete_posting_payload"):
        scan("personio", header_only)


@pytest.mark.parametrize("platform", ["workable", "personio", "pinpoint"])
def test_feed_limits_fail_without_partial_results(platform, monkeypatch):
    monkeypatch.setattr(connectors, "MAX_POSTINGS", 0)
    with pytest.raises(connectors.ConnectorError, match="scan_limit_exceeded"):
        scan(platform)


def test_feed_change_between_reads_cannot_establish_complete_snapshot():
    calls = []
    def handler(request):
        calls.append(request)
        data = workable()
        if len(calls) > 1:
            data["jobs"][0]["description"] = "Changed requirements."
        return httpx.Response(200, json=data)
    with pytest.raises(connectors.ConnectorError, match="source_changed_during_scan"):
        scan("workable", handler=handler)


@pytest.mark.parametrize("contract,error", [
    (replace(source("personio"), careers_url="https://another.jobs.personio.com/"), "unverified_feed_tenant"),
    (replace(source("personio"), allowed_hosts=("careers.example.com",)), "unverified_feed_host"),
    (replace(source("personio"), careers_url="https://example.jobs.personio.com/?language=xx"), "unsupported_feed_language"),
    (replace(source("pinpoint"), board_token="example_evil"), "invalid_board_token"),
    (replace(source("workable"), board_token="Example"), "invalid_board_token"),
])
def test_feed_routes_reject_unverified_tenants_and_locales_before_network(contract, error):
    with pytest.raises(connectors.ConnectorError, match=error):
        scan(contract.platform, handler=lambda _: pytest.fail("No invalid route may reach network"), contract=contract)


@pytest.mark.parametrize("platform,value", [
    ("workable", "https://apply.workable.com/another/j/ABC123/"),
    ("workable", "https://apply.workable.com/j/OTHER/"),
    ("workable", "https://apply.workable.com/j/ABC123/../../another/j/OTHER/"),
    ("workable", "https://another.workable.com/jobs/ABC123"),
    ("pinpoint", "https://another.pinpointhq.com/pt-BR/postings/publication-01"),
    ("pinpoint", "https://example.pinpointhq.com/pt-BR/jobs/job-01"),
    ("pinpoint", "https://example.pinpointhq.com/pt-BR/postings/OTHER"),
])
def test_provider_destinations_keep_exact_tenant_and_posting_provenance(platform, value):
    data = workable() if platform == "workable" else pinpoint()
    data["jobs" if platform == "workable" else "data"][0]["url"] = value
    with pytest.raises(connectors.ConnectorError):
        scan(platform, data)


def test_pinpoint_deadline_excludes_closed_posting_but_invalid_date_fails():
    data = pinpoint()
    data["data"][0]["deadline_at"] = "2020-01-01T00:00:00Z"
    assert scan("pinpoint", data) == []
    data["data"][0]["deadline_at"] = "unknown"
    with pytest.raises(connectors.ConnectorError, match="invalid_posting_deadline"):
        scan("pinpoint", data)


@pytest.mark.parametrize("platform", ["workable", "personio", "pinpoint"])
def test_auth_cookies_and_redirects_are_never_used(platform):
    calls = []
    data = b"<workzag-jobs/>" if platform == "personio" else {"jobs" if platform == "workable" else "data": []}
    def handler(request):
        calls.append(request)
        assert not any(key in request.headers for key in ("authorization", "cookie", "x-smarttoken"))
        return httpx.Response(200, content=data) if isinstance(data, bytes) else httpx.Response(200, json=data)
    with httpx.Client(transport=httpx.MockTransport(handler), auth=("name", "secret"),
                     headers={"X-SmartToken": "secret"}, cookies={"session": "secret"}) as client:
        assert connectors.fetch_postings(source(platform), client) == []
    assert len(calls) == 2
    with pytest.raises(connectors.ConnectorError, match="source_http_302"):
        scan(platform, handler=lambda _: httpx.Response(302, headers={"Location": "https://untrusted.example"}))


@pytest.mark.parametrize("platform", ["workable", "personio", "pinpoint"])
def test_new_public_source_registration_never_enables_submission(platform):
    contract = source(platform)
    payload = {"platform": platform, "employer": contract.employer, "board_token": contract.board_token,
        "careers_url": contract.careers_url, "allowed_hosts": list(contract.allowed_hosts),
        "verification_url": contract.careers_url, "verification_note": "Verified employer-to-tenant public feed."}
    assert schemas.SourceCreate(**payload).submission_enabled is False
    with pytest.raises(ValidationError, match="Greenhouse only"):
        schemas.SourceCreate(**payload, submission_enabled=True)


@pytest.mark.parametrize("platform", ["workable", "personio", "pinpoint"])
def test_second_read_outage_preserves_existing_postings_in_database(platform, monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(tasks.config, "discovery_enabled", lambda: True)
    contract = source(platform)
    row = scan(platform)[0]
    with factory() as db:
        db.add(models.EmployerSource(**{**contract.__dict__, "allowed_hosts": list(contract.allowed_hosts),
            "verification_url": contract.careers_url, "verification_note": "Verified synthetic employer feed."}))
        db.add(models.EmployerPosting(id="existing", source_id=contract.id, **row))
        db.commit()
    original_fetch = connectors.fetch_postings
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) > 1:
            return httpx.Response(503, headers={"Retry-After": "0"})
        return httpx.Response(200, content=b"<workzag-jobs/>") if platform == "personio" else httpx.Response(200, json={"jobs" if platform == "workable" else "data": []})
    def failed_scan(source):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return original_fetch(source, client)
    monkeypatch.setattr(connectors, "fetch_postings", failed_scan)
    with pytest.raises(connectors.ConnectorError, match="source_http_503"):
        tasks.refresh_source(contract.id)
    with factory() as db:
        posting = db.get(models.EmployerPosting, "existing")
        assert posting.is_open and posting.closed_at is None
        assert posting.content_sha256 == row["content_sha256"]
        assert db.get(models.EmployerSource, contract.id).status == "unavailable"
    engine.dispose()
