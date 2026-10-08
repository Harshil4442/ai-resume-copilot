from __future__ import annotations

import copy
from datetime import UTC, datetime

import httpx
import pytest
from backend.app.database import Base
from backend.app.domains.employer import connectors, models, schemas, tasks
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

SOURCE = connectors.SourceContract(
    id="source-public", employer="Example Engineering", platform="smartrecruiters",
    board_token="ExampleEngineering", region="global", allowed_hosts=("careers.example.com",),
    careers_url="https://careers.example.com/jobs",
)


def summary(identity: str = "100") -> dict:
    return {
        "id": identity, "uuid": f"publication-{identity}", "name": "Software Engineer",
        "company": {"identifier": SOURCE.board_token, "name": SOURCE.employer},
        "releasedDate": "2026-10-07T10:20:30+05:30",
        "location": {"city": "Bengaluru", "region": "Karnataka", "country": "in", "remote": False},
        "language": "en-GB", "refNumber": f"REF-{identity}",
        "ref": "https://untrusted.example/internal-credentials",
    }


def detail(identity: str = "100", *, active: bool = True) -> dict:
    # Mirrors the documented PostingDetails shape, with synthetic employer data.
    return {
        "id": identity, "uuid": f"publication-{identity}", "name": "Software Engineer",
        "company": {"identifier": SOURCE.board_token, "name": SOURCE.employer},
        "jobId": f"requisition-{identity}", "active": active,
        "postingUrl": f"https://jobs.smartrecruiters.com/{SOURCE.board_token}/{identity}-software-engineer",
        "applyUrl": f"https://www.smartrecruiters.com/{SOURCE.board_token}/{identity}-software-engineer?oga=true",
        "referralUrl": "https://untrusted.example/referral",
        "jobAd": {"sections": {
            "companyDescription": {"title": "Company Description", "text": "<p>Employer team</p>"},
            "jobDescription": {"title": "Job Description", "text": "<p>Build Python services.</p><script>bad()</script>"},
            "qualifications": {"title": "Qualifications", "text": "<ul><li>Python &amp; SQL</li></ul>"},
            "additionalInformation": {"title": "Additional Information", "text": "Hybrid office."},
            "videos": {"title": "Videos", "urls": ["https://untrusted.example/video"]},
        }},
    }


def page(rows: list[dict], *, offset: int = 0, limit: int = 100, total: int | None = None) -> dict:
    return {"limit": limit, "offset": offset, "totalFound": len(rows) if total is None else total, "content": rows}


@pytest.fixture(autouse=True)
def fast_reads(monkeypatch):
    monkeypatch.setattr(connectors, "SMARTRECRUITERS_READ_INTERVAL", 0)
    monkeypatch.setattr(connectors.time, "sleep", lambda _: None)


def scan(handler, source=SOURCE):
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return connectors.fetch_postings(source, client)


def test_complete_pages_and_details_preserve_origin_dates_location_and_inert_content():
    requested = []

    def handler(request):
        requested.append(request)
        assert request.method == "GET"
        assert request.url.host == "api.smartrecruiters.com"
        assert request.url.path.startswith(f"/v1/companies/{SOURCE.board_token}/postings")
        if request.url.path.endswith("/postings"):
            assert request.url.params["destination"] == "PUBLIC"
            assert request.url.params["limit"] == "100"
            offset = int(request.url.params["offset"])
            rows = [summary("100"), summary("101")] if offset == 0 else [summary("102")]
            return httpx.Response(200, json=page(rows, offset=offset, limit=2, total=3))
        return httpx.Response(200, json=detail(request.url.path.rsplit("/", 1)[1]))

    results = scan(handler)
    assert [job["external_id"] for job in results] == ["100", "101", "102"]
    assert len(requested) == 7
    first = results[0]
    assert first["requisition_id"] == "requisition-100"
    assert first["location"] == "Bengaluru, Karnataka, in"
    assert first["country"] == "IN" and first["remote"] is False
    assert first["publication_at"] == datetime(2026, 10, 7, 4, 50, 30, tzinfo=UTC)
    assert first["source_updated_at"] is None and first["language"] == "en-GB"
    assert first["canonical_url"].startswith("https://jobs.smartrecruiters.com/ExampleEngineering/100-")
    assert first["apply_url"].endswith("?oga=true")
    assert "Python & SQL" in first["description"]
    assert "bad()" not in first["description"] and "untrusted.example" not in first["description"]
    assert len(first["content_sha256"]) == 64


def test_scan_strips_client_auth_token_and_cookies():
    def handler(request):
        assert "Authorization" not in request.headers
        assert "X-SmartToken" not in request.headers
        assert "Cookie" not in request.headers
        return httpx.Response(200, json=page([]))

    with httpx.Client(transport=httpx.MockTransport(handler), auth=("credential", "secret"),
                     headers={"Authorization": "Bearer secret", "X-SmartToken": "secret"},
                     cookies={"session": "secret"}) as client:
        assert connectors.fetch_postings(SOURCE, client) == []


@pytest.mark.parametrize("mutation,error", [
    ({"offset": 1}, "incomplete_scan"),
    ({"limit": 0}, "incomplete_scan"),
    ({"limit": 101}, "incomplete_scan"),
    ({"limit": True}, "incomplete_scan"),
    ({"totalFound": "1"}, "incomplete_scan"),
    ({"totalFound": -1}, "incomplete_scan"),
    ({"totalFound": 10_001}, "scan_limit_exceeded"),
    ({"totalFound": 2}, "incomplete_scan"),
    ({"content": {}}, "incomplete_scan"),
])
def test_invalid_or_truncated_scan_never_returns_partial_results(mutation, error):
    payload = {**page([summary()]), **mutation}
    with pytest.raises(connectors.ConnectorError, match=f"^{error}$"):
        scan(lambda _: httpx.Response(200, json=payload))


def test_changed_total_on_later_page_invalidates_entire_scan():
    def handler(request):
        if request.url.path.endswith("/postings"):
            offset = int(request.url.params["offset"])
            return httpx.Response(200, json=page([summary(str(100 + offset))], offset=offset,
                                               limit=1, total=2 if offset == 0 else 3))
        return httpx.Response(200, json=detail(request.url.path.rsplit("/", 1)[1]))

    with pytest.raises(connectors.ConnectorError, match="source_changed_during_scan"):
        scan(handler)


def test_repeated_page_identity_cannot_hide_missing_posting():
    def handler(request):
        if request.url.path.endswith("/postings"):
            offset = int(request.url.params["offset"])
            return httpx.Response(200, json=page([summary()], offset=offset, limit=1, total=2))
        return httpx.Response(200, json=detail())

    with pytest.raises(connectors.ConnectorError, match="duplicate_posting_identifiers"):
        scan(handler)


@pytest.mark.parametrize("mutate,error", [
    (lambda row: row.update(company={"identifier": "AnotherCompany"}), "posting_company_mismatch"),
    (lambda row: row.update(id="999", uuid="other-publication"), "posting_identity_changed"),
    (lambda row: row.update(uuid="replaced-publication"), "posting_identity_changed"),
    (lambda row: row.pop("active"), "posting_status_missing"),
    (lambda row: row.update(active="true"), "posting_status_missing"),
    (lambda row: row.update(jobAd={}), "incomplete_posting_payload"),
    (lambda row: row.update(location={"country": "India"}), "invalid_posting_country"),
    (lambda row: row.update(location={"remote": "true"}), "invalid_posting_remote_status"),
    (lambda row: row.update(postingUrl="https://jobs.smartrecruiters.com/AnotherCompany/100-job"), "job_destination_tenant_mismatch"),
    (lambda row: row.update(postingUrl="https://jobs.smartrecruiters.com/ExampleEngineering/999-job"), "job_destination_posting_mismatch"),
    (lambda row: row.update(applyUrl="https://job-board.example/apply/100"), "unverified_job_destination"),
    (lambda row: row.update(applyUrl="https://user:secret@jobs.smartrecruiters.com/ExampleEngineering/100-job"), "unverified_job_destination"),
    (lambda row: row.update(applyUrl="https://jobs.smartrecruiters.com:wrong/ExampleEngineering/100-job"), "unverified_job_destination"),
])
def test_details_fail_closed_on_status_identity_or_destination_change(mutate, error):
    row = detail()
    mutate(row)

    def handler(request):
        return httpx.Response(200, json=page([summary()]) if request.url.path.endswith("/postings") else row)

    with pytest.raises(connectors.ConnectorError, match=f"^{error}$"):
        scan(handler)


def test_unpublished_detail_is_excluded_only_after_complete_scan():
    def handler(request):
        return httpx.Response(200, json=page([summary()]) if request.url.path.endswith("/postings") else detail(active=False))

    assert scan(handler) == []


@pytest.mark.parametrize("date", [None, "not-a-date"])
def test_missing_or_invalid_release_date_stays_unknown(date):
    row = {**summary(), **detail(), "releasedDate": date}
    result = connectors._normalize(SOURCE, row)
    assert result["publication_at"] is None
    assert result["source_updated_at"] is None


def test_uuid_lookup_is_supported_and_detail_wins_over_summary_metadata():
    row = summary()
    row.pop("id")
    details = detail()
    details.update(releasedDate="2026-10-08T00:00:00Z",
                   location={"city": "Paris", "country": "fr", "remote": True})

    def handler(request):
        if request.url.path.endswith("/postings"):
            return httpx.Response(200, json=page([row]))
        assert request.url.path.endswith("/publication-100")
        return httpx.Response(200, json=details)

    result = scan(handler)[0]
    assert result["external_id"] == "100"
    assert result["country"] == "FR" and result["remote"] is True
    assert result["publication_at"] == datetime(2026, 10, 8, tzinfo=UTC)


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_transient_get_retries_are_bounded_and_can_recover(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status if len(calls) == 1 else 200,
                              headers={"Retry-After": "0"}, json=page([]))

    assert scan(handler) == []
    assert len(calls) == 3  # retry, recovered page, stable manifest verification


def test_exhausted_transient_retries_never_return_empty_success():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503, json={})

    with pytest.raises(connectors.ConnectorError, match="source_http_503"):
        scan(handler)
    assert len(calls) == connectors.SMARTRECRUITERS_READ_ATTEMPTS


@pytest.mark.parametrize("status,headers", [
    (404, {}), (403, {}), (301, {"Location": "https://untrusted.example/redirect"}),
    (429, {"Retry-After": "300"}), (429, {"Retry-After": "invalid"}),
])
def test_no_redirect_or_retry_before_long_provider_cooldown(status, headers):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers=headers, json={})

    with pytest.raises(connectors.ConnectorError):
        scan(handler)
    assert len(calls) == 1


def test_timeout_retries_are_bounded():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("Synthetic outage")

    with pytest.raises(connectors.ConnectorError, match="source_unavailable"):
        scan(handler)
    assert len(calls) == connectors.SMARTRECRUITERS_READ_ATTEMPTS


def test_scan_time_cap_and_response_byte_cap(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=page([]))

    monkeypatch.setattr(connectors, "SMARTRECRUITERS_SCAN_SECONDS", 0)
    with pytest.raises(connectors.ConnectorError, match="scan_time_limit_exceeded"):
        scan(handler)
    assert calls == []
    monkeypatch.setattr(connectors, "SMARTRECRUITERS_SCAN_SECONDS", 240)
    monkeypatch.setattr(connectors, "MAX_RESPONSE_BYTES", 10)
    with pytest.raises(connectors.ConnectorError, match="source_response_too_large"):
        scan(handler)
    assert len(calls) == 1


def test_source_registration_does_not_grant_smartrecruiters_submission():
    payload = {
        "employer": SOURCE.employer, "platform": SOURCE.platform, "board_token": SOURCE.board_token,
        "careers_url": SOURCE.careers_url, "allowed_hosts": list(SOURCE.allowed_hosts),
        "verification_url": SOURCE.careers_url, "verification_note": "Employer careers page verifies the exact tenant.",
    }
    assert schemas.SourceCreate(**payload).submission_enabled is False
    with pytest.raises(ValidationError, match="Greenhouse only"):
        schemas.SourceCreate(**payload, submission_enabled=True)
    with pytest.raises(connectors.ConnectorError, match="submission_not_authorized"):
        connectors.submit_greenhouse(SOURCE, "100", credential="unused", answers={},
                                     content=b"unused", filename="resume.pdf", media_type="application/pdf")


def test_public_form_handoff_never_claims_complete_application_questions():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=page([summary()]) if request.url.path.endswith("/postings") else detail())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        form = connectors.load_form(SOURCE, "100", client)
    assert form["supported"] is False
    assert form["fields"] == [] and form["consents"] == []
    assert "hosted form" in form["handoff_reason"]
    assert len(calls) == 1 and calls[0].url.path.endswith("/100")
    assert len(form["job_content_sha256"]) == 64


def test_equal_count_manifest_replacement_invalidates_entire_scan():
    lists = []
    def handler(request):
        if request.url.path.endswith("/postings"):
            lists.append(request)
            return httpx.Response(200, json=page([summary("100" if len(lists) == 1 else "101")]))
        return httpx.Response(200, json=detail())
    with pytest.raises(connectors.ConnectorError, match="source_changed_during_scan"):
        scan(handler)


def test_second_manifest_outage_cannot_establish_a_closed_board():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=page([])) if len(calls) == 1 else httpx.Response(503, json={})
    with pytest.raises(connectors.ConnectorError, match="source_http_503"):
        scan(handler)
    assert len(calls) == 1 + connectors.SMARTRECRUITERS_READ_ATTEMPTS


def test_partial_detail_outage_keeps_existing_jobs_open_and_records_unavailable(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(tasks.config, "discovery_enabled", lambda: True)
    with factory() as db:
        db.add(models.EmployerSource(**{
            **SOURCE.__dict__, "allowed_hosts": list(SOURCE.allowed_hosts),
            "verification_url": SOURCE.careers_url, "verification_note": "Verified employer-origin synthetic fixture.",
        }))
        for identity in ("100", "101"):
            db.add(models.EmployerPosting(id=f"job-{identity}", source_id=SOURCE.id,
                                          **connectors._normalize(SOURCE, {**summary(identity), **detail(identity)})))
        db.commit()

    def handler(request):
        if request.url.path.endswith("/postings"):
            return httpx.Response(200, json=page([summary("100"), summary("101")]))
        if request.url.path.endswith("/101"):
            return httpx.Response(503, headers={"Retry-After": "0"}, json={})
        changed = detail("100")
        changed["name"] = "Changed title that must not partially persist"
        return httpx.Response(200, json=changed)

    original_fetch = connectors.fetch_postings
    def failed_scan(source):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return original_fetch(source, client)
    monkeypatch.setattr(connectors, "fetch_postings", failed_scan)
    with pytest.raises(connectors.ConnectorError, match="source_http_503"):
        tasks.refresh_source(SOURCE.id)
    with factory() as db:
        jobs = db.query(models.EmployerPosting).all()
        assert len(jobs) == 2 and all(job.is_open and job.closed_at is None for job in jobs)
        assert all(job.title == "Software Engineer" for job in jobs)
        source = db.get(models.EmployerSource, SOURCE.id)
        assert source.status == "unavailable" and source.last_error_code == "source_http_503"
        assert source.scan_token is None and source.last_success_at is None
    # A later fully completed empty scan can establish that the jobs closed.
    monkeypatch.setattr(connectors, "fetch_postings", lambda _: [])
    assert tasks.refresh_source(SOURCE.id) == "completed"
    with factory() as db:
        assert all(not job.is_open and job.closed_at is not None for job in db.query(models.EmployerPosting).all())
    monkeypatch.setattr(connectors, "fetch_postings", original_fetch)
    engine.dispose()


def test_content_fingerprint_ignores_release_date_but_tracks_job_changes():
    row = {**summary(), **detail()}
    original = connectors._normalize(SOURCE, row)
    republished = copy.deepcopy(row)
    republished["releasedDate"] = "2026-10-08T00:00:00Z"
    assert connectors._normalize(SOURCE, republished)["content_sha256"] == original["content_sha256"]
    republished["jobAd"]["sections"]["qualifications"]["text"] = "Java and SQL"
    assert connectors._normalize(SOURCE, republished)["content_sha256"] != original["content_sha256"]
