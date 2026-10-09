"""Bounded documented public ATS reads and permissioned exact-package writes.

No connector accepts an arbitrary API URL. Public posting access never implies
permission to submit. HTML is retained only as inert plain text.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import re
import time
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree as ET

import httpx

from ..common import payload_fingerprint
from . import host_pacing

READ_HOSTS = {
    "greenhouse": {"boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io"},
    "lever": {"jobs.lever.co", "jobs.eu.lever.co"},
    "ashby": {"jobs.ashbyhq.com"},
    "smartrecruiters": {"jobs.smartrecruiters.com", "www.smartrecruiters.com", "careers.smartrecruiters.com"},
    "workable": {"apply.workable.com"},
    "personio": set(),
    "pinpoint": set(),
}
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_POSTINGS = 10_000
SMARTRECRUITERS_PAGE_SIZE = 100
SMARTRECRUITERS_SCAN_SECONDS = 240
SMARTRECRUITERS_READ_INTERVAL = 0.15
SMARTRECRUITERS_READ_ATTEMPTS = 3
SMARTRECRUITERS_MAX_RETRY_DELAY = 2.0
LEVER_SCAN_SECONDS = 240.0
LEVER_READ_ATTEMPTS = 3
PERSONIO_LANGUAGES = {"de", "en", "fr", "es", "nl", "it", "pt"}
PINPOINT_LANGUAGES = {"ar", "bg", "cs", "cy", "de", "en", "es", "fr", "it", "ja", "pt", "pt-BR", "ru", "sv", "uk", "zh"}
XML_MAX_DEPTH = 32
XML_MAX_NODES = 250_000


class ConnectorError(ValueError):
    def __init__(self, code: str, *, safe_to_retry: bool = True):
        self.code = code
        self.safe_to_retry = safe_to_retry
        super().__init__(code)


@dataclass(frozen=True)
class SourceContract:
    id: str
    employer: str
    platform: str
    board_token: str
    region: str
    allowed_hosts: tuple[str, ...]
    careers_url: str
    submission_enabled: bool = False
    submission_grant: str | None = None
    credential_env: str | None = None
    form_parity_verified: bool = False
    receipt_contract: dict | None = None


def source_contract(source) -> SourceContract:
    return SourceContract(**{key: getattr(source, key) for key in SourceContract.__dataclass_fields__})


class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.skip += 1
        elif tag in {"br", "p", "div", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def plain_text(value: Any) -> str:
    parser = _TextParser()
    parser.feed(html.unescape(str(value or "")))
    return re.sub(r"[ \t]+", " ", "".join(parser.parts)).strip()[:100_000]


def parse_date(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        if isinstance(value, (float, int)):
            return datetime.fromtimestamp(value / 1000 if value > 10**11 else value, UTC)
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)
    except (ValueError, TypeError, OverflowError):
        return None


def safe_origin_url(url: str, source: SourceContract) -> str:
    try:
        parsed = urlsplit(str(url))
        port = parsed.port
    except ValueError as exc:
        raise ConnectorError("unverified_job_destination", safe_to_retry=False) from exc
    hosts = set(source.allowed_hosts) | READ_HOSTS[source.platform]
    if source.platform == "workable":
        hosts.add(f"{source.board_token}.workable.com")
    if source.platform in {"personio", "pinpoint"}:
        hosts.add(_feed_host(source))
    if (parsed.scheme != "https" or parsed.username or parsed.password or
            port not in (None, 443) or parsed.hostname not in hosts):
        raise ConnectorError("unverified_job_destination")
    if parsed.hostname in READ_HOSTS[source.platform] and source.platform != "workable":
        path_segments = parsed.path.strip("/").split("/")
        if not path_segments or path_segments[0] != source.board_token:
            raise ConnectorError("job_destination_tenant_mismatch")
    return str(url)


def _endpoint(source: SourceContract, external_id: str | None = None) -> str:
    token = source.board_token
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", token):
        raise ConnectorError("invalid_board_token")
    if external_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", external_id):
        raise ConnectorError("invalid_posting_identifier")
    if source.platform == "greenhouse":
        base = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
    elif source.platform == "lever":
        host = "api.eu.lever.co" if source.region == "eu" else "api.lever.co"
        base = f"https://{host}/v0/postings/{token}"
    elif source.platform == "ashby":
        base = f"https://api.ashbyhq.com/posting-api/job-board/{token}"
    elif source.platform == "smartrecruiters":
        base = f"https://api.smartrecruiters.com/v1/companies/{token}/postings"
    elif source.platform == "workable":
        _feed_host(source)
        base = f"https://www.workable.com/api/accounts/{token}"
    elif source.platform == "personio":
        base = f"https://{_feed_host(source)}/xml"
    elif source.platform == "pinpoint":
        locale = _feed_language(source)
        base = f"https://{_feed_host(source)}/{locale}/postings.json"
    else:
        raise ConnectorError("unsupported_source")
    return base + (f"/{external_id}" if external_id else "")


def _json_get(client: httpx.Client, url: str, params: dict | None = None):
    try:
        with client.stream("GET", url, params=params) as response:
            if response.status_code == 404:
                raise ConnectorError("posting_or_source_closed", safe_to_retry=False)
            if response.status_code != 200:
                raise ConnectorError(f"source_http_{response.status_code}")
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > MAX_RESPONSE_BYTES:
                    raise ConnectorError("source_response_too_large")
            return json.loads(content)
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise ConnectorError("source_unavailable") from exc


def _client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(20, connect=5), follow_redirects=False,
                        headers={"User-Agent": "HireWiz-EmployerConnector/1.0", "Accept": "application/json"})


class _VerifiedPublicRedirect(Exception):
    """A fixed public GET destination; the originating stream must close first."""


class _PublicRead:
    """Unauthenticated public reads, bounded below the source worker's lease."""

    def __init__(self, client: httpx.Client):
        self.client = client
        self.deadline = time.monotonic() + SMARTRECRUITERS_SCAN_SECONDS
        self.next_request_at = 0.0

    def _wait(self, delay: float = 0) -> None:
        delay = max(delay, self.next_request_at - time.monotonic(), 0)
        if time.monotonic() + delay >= self.deadline:
            raise ConnectorError("scan_time_limit_exceeded")
        if delay:
            time.sleep(delay)

    def body(self, url: str, params: dict | None = None, *, expected_redirect: str | None = None) -> bytes:
        for attempt in range(SMARTRECRUITERS_READ_ATTEMPTS):
            self._wait()
            self.next_request_at = time.monotonic() + SMARTRECRUITERS_READ_INTERVAL
            # Client defaults can include credential query parameters and headers.
            # Construct the public request independently; retain only its transport.
            request = httpx.Request("GET", url, params=params,
                headers={"User-Agent": "HireWiz-EmployerConnector/1.0", "Accept": "application/json"},
                extensions={"timeout": httpx.Timeout(min(20, self.deadline - time.monotonic())).as_dict()})
            delay = 0.5 * (attempt + 1)
            try:
                with closing(self.client.send(request, stream=True, auth=None, follow_redirects=False)) as response:
                    if (response.status_code == 302 and expected_redirect is not None
                            and response.headers.get("Location") == expected_redirect):
                        # Only the caller's fixed vendor/account path is accepted.
                        # Raising exits/closes this response before the next GET.
                        raise _VerifiedPublicRedirect
                    if response.status_code == 404:
                        raise ConnectorError("posting_or_source_closed", safe_to_retry=False)
                    if response.status_code != 200:
                        transient = response.status_code in {408, 429, 500, 502, 503, 504}
                        error = ConnectorError(f"source_http_{response.status_code}", safe_to_retry=transient)
                        if not transient or attempt + 1 == SMARTRECRUITERS_READ_ATTEMPTS:
                            raise error
                        retry_after = response.headers.get("Retry-After")
                        if retry_after:
                            try:
                                delay = float(retry_after)
                            except ValueError:
                                try:
                                    delay = (parsedate_to_datetime(retry_after) - datetime.now(UTC)).total_seconds()
                                except (ValueError, TypeError, OverflowError):
                                    raise error from None
                            # Let the durable scheduler wait longer; never retry
                            # earlier than an employer's requested cooldown.
                            if not 0 <= delay <= SMARTRECRUITERS_MAX_RETRY_DELAY:
                                raise error
                    else:
                        content = bytearray()
                        for chunk in response.iter_bytes():
                            if time.monotonic() >= self.deadline:
                                raise ConnectorError("scan_time_limit_exceeded")
                            content.extend(chunk)
                            if len(content) > MAX_RESPONSE_BYTES:
                                raise ConnectorError("source_response_too_large")
                        return bytes(content)
            except httpx.HTTPError as exc:
                if attempt + 1 == SMARTRECRUITERS_READ_ATTEMPTS:
                    raise ConnectorError("source_unavailable") from exc
            self._wait(delay)
        raise ConnectorError("source_unavailable")


    def get(self, url: str, params: dict | None = None, *, expected_redirect: str | None = None) -> dict:
        try:
            try:
                body = self.body(url, params, expected_redirect=expected_redirect)
            except _VerifiedPublicRedirect:
                assert expected_redirect is not None
                # Same reader/deadline/pacing and stripped credentials; no second hop.
                body = self.body(expected_redirect)
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ConnectorError("invalid_source_payload") from exc
        if not isinstance(data, dict):
            raise ConnectorError("invalid_source_payload")
        return data


class _SmartRecruitersRead(_PublicRead):
    pass


@asynccontextmanager
async def _lever_response(client: httpx.AsyncClient, request: httpx.Request, closed: list[bool]):
    # send() uses the already-sanitized request without merging cookies/default
    # credentials back in. Cancellation of real HTTPX async I/O closes the socket.
    closed[0] = False
    response = await client.send(request, stream=True, auth=None, follow_redirects=False)
    completed = False
    try:
        yield response
        completed = True
    except ConnectorError:
        # A deliberately rejected status/payload can release after its stream
        # closes successfully; transport/cleanup exceptions stay ambiguous.
        completed = True
        raise
    finally:
        async with asyncio.timeout(host_pacing.CLOSE_SECONDS):
            await response.aclose()
        # HTTPX may set is_closed before its stream's aclose() raises during
        # iteration. A second idempotent aclose alone cannot prove that cleanup.
        closed[0] = completed


class _LeverRead:
    """Public GETs with a real total HTTP deadline and a shared host lease."""

    def __init__(self, client: httpx.AsyncClient, coordinator: host_pacing.Coordinator):
        self.client = client
        self.coordinator = coordinator
        self.deadline = time.monotonic() + LEVER_SCAN_SECONDS

    async def get(self, url: str, params: dict) -> list:
        host = urlsplit(url).hostname or ""
        for attempt in range(LEVER_READ_ATTEMPTS):
            permit = self.coordinator.acquire(host, self.deadline)
            delay = 0.5 * (attempt + 1)
            uncertain_close = False
            closed = [True]
            try:
                remaining = min(host_pacing.REQUEST_SECONDS, self.deadline - time.monotonic())
                if remaining <= 0:
                    raise ConnectorError("scan_time_limit_exceeded")
                request = self.client.build_request("GET", url, params=params, timeout=remaining)
                for header in ("Authorization", "X-SmartToken", "Cookie"):
                    request.headers.pop(header, None)
                # Fresh ownership immediately before dispatch. A cached grant
                # cannot survive coordinator restart, loss, or lease expiry.
                self.coordinator.verify(permit)
                async with asyncio.timeout(remaining):
                    async with _lever_response(self.client, request, closed) as response:
                        if response.status_code == 404:
                            raise ConnectorError("posting_or_source_closed", safe_to_retry=False)
                        if response.status_code != 200:
                            transient = response.status_code in {408, 429, 500, 502, 503, 504}
                            error = ConnectorError(f"source_http_{response.status_code}", safe_to_retry=transient)
                            if not transient or attempt + 1 == LEVER_READ_ATTEMPTS:
                                raise error
                            retry_after = response.headers.get("Retry-After")
                            if retry_after:
                                try:
                                    delay = float(retry_after)
                                except ValueError:
                                    try:
                                        delay = (parsedate_to_datetime(retry_after) - datetime.now(UTC)).total_seconds()
                                    except (ValueError, TypeError, OverflowError):
                                        raise error from None
                                if not 0 <= delay <= SMARTRECRUITERS_MAX_RETRY_DELAY:
                                    raise error
                        else:
                            content = bytearray()
                            async for chunk in response.aiter_bytes():
                                content.extend(chunk)
                                if len(content) > MAX_RESPONSE_BYTES:
                                    raise ConnectorError("source_response_too_large")
                            try:
                                result = json.loads(content)
                            except (ValueError, UnicodeError) as exc:
                                raise ConnectorError("invalid_source_payload") from exc
                            if not isinstance(result, list):
                                raise ConnectorError("invalid_source_payload")
                            return result
            except TimeoutError as exc:
                uncertain_close = True
                # No retry after an absolute deadline: let the durable scheduler
                # recover a fresh scan, with the host cooldown still enforced.
                raise ConnectorError("source_request_time_limit_exceeded") from exc
            except httpx.HTTPError as exc:
                if not closed[0] or attempt + 1 == LEVER_READ_ATTEMPTS:
                    raise ConnectorError("source_unavailable") from exc
            finally:
                # The stream context closes before releasing the lease. If this
                # exchange is ambiguous, reject the whole scan; never close jobs.
                if not uncertain_close and closed[0]:
                    self.coordinator.release(permit)
            if time.monotonic() + delay >= self.deadline:
                raise ConnectorError("scan_time_limit_exceeded")
            await asyncio.sleep(delay)
        raise ConnectorError("source_unavailable")


async def _lever_rows(source: SourceContract, client: httpx.AsyncClient | None) -> list:
    owned_client = client is None
    coordinator = host_pacing.coordinator()
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5),
                                       follow_redirects=False,
                                       headers={"User-Agent": "HireWiz-EmployerConnector/1.0", "Accept": "application/json"})
    try:
        reader = _LeverRead(client, coordinator)
        rows: list = []
        for skip in range(0, MAX_POSTINGS + 1, 100):
            page = await reader.get(_endpoint(source), {"mode": "json", "limit": 100, "skip": skip})
            rows.extend(page)
            if len(page) < 100:
                return rows
        raise ConnectorError("scan_limit_exceeded")
    finally:
        coordinator.close()
        if owned_client:
            await client.aclose()


def _feed_host(source: SourceContract) -> str:
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", source.board_token):
        raise ConnectorError("invalid_board_token", safe_to_retry=False)
    if source.platform == "personio":
        host = urlsplit(source.careers_url).hostname
        if host not in {f"{source.board_token}.jobs.personio.de", f"{source.board_token}.jobs.personio.com"}:
            raise ConnectorError("unverified_feed_tenant", safe_to_retry=False)
        if host not in source.allowed_hosts:
            raise ConnectorError("unverified_feed_host", safe_to_retry=False)
        return host
    if source.platform == "pinpoint":
        return f"{source.board_token}.pinpointhq.com"
    if source.platform == "workable":
        return f"{source.board_token}.workable.com"
    raise ConnectorError("unsupported_source", safe_to_retry=False)


def _feed_language(source: SourceContract) -> str:
    parsed = urlsplit(source.careers_url)
    if source.platform == "personio":
        params = parse_qs(parsed.query)
        language = (params.get("language") or params.get("display") or ["de"])[0]
        valid = PERSONIO_LANGUAGES
    else:
        language = parsed.path.strip("/").split("/")[0] or "en"
        if language not in PINPOINT_LANGUAGES and language in {"jobs", "postings"}:
            language = "en"
        valid = PINPOINT_LANGUAGES
    if language not in valid:
        raise ConnectorError("unsupported_feed_language", safe_to_retry=False)
    return language


def _feed_destination(source: SourceContract, row: dict, value: str) -> str:
    url = safe_origin_url(value, source)
    parsed = urlsplit(url)
    if source.platform == "workable":
        if parsed.hostname == "apply.workable.com":
            shortcode = str(row.get("shortcode") or "")
            parts = parsed.path.strip("/").split("/")
            permitted = (["j", shortcode], [source.board_token, "j", shortcode])
            if not shortcode or not any(parts == path or parts == [*path, "apply"] for path in permitted):
                raise ConnectorError("job_destination_posting_mismatch", safe_to_retry=False)
        elif parsed.hostname and parsed.hostname.endswith(".workable.com") and parsed.hostname != _feed_host(source):
            raise ConnectorError("job_destination_tenant_mismatch", safe_to_retry=False)
        elif parsed.hostname == _feed_host(source) and not re.fullmatch(r"/(?:jobs|j)/[A-Za-z0-9_-]+(?:/candidates/new)?/?", parsed.path):
            raise ConnectorError("job_destination_posting_mismatch", safe_to_retry=False)
    elif source.platform == "pinpoint":
        if parsed.hostname and parsed.hostname.endswith(".pinpointhq.com") and parsed.hostname != _feed_host(source):
            raise ConnectorError("job_destination_tenant_mismatch", safe_to_retry=False)
        # The numeric posting ID and hosted URL UUID are different identifiers.
        # Preserve the feed's exact path instead of substituting the job ID.
        if parsed.path != row.get("path") or not re.fullmatch(r"/(?:[A-Za-z-]+/)?postings/[A-Za-z0-9-]+", parsed.path):
            raise ConnectorError("job_destination_posting_mismatch", safe_to_retry=False)
    return url


class _SafeXMLBuilder(ET.TreeBuilder):
    def __init__(self):
        super().__init__()
        self.depth, self.nodes = 0, 0

    def start(self, tag, attrs):
        self.depth += 1
        self.nodes += 1
        if self.depth > XML_MAX_DEPTH or self.nodes > XML_MAX_NODES:
            raise ConnectorError("source_xml_limit_exceeded")
        return super().start(tag, attrs)

    def end(self, tag):
        self.depth -= 1
        return super().end(tag)

    def doctype(self, name, pubid, system):
        # This callback works for UTF-16 as well as UTF-8. Never resolve any
        # external or internal DTD/entity; XInclude is never processed either.
        raise ConnectorError("source_xml_dtd_forbidden", safe_to_retry=False)


def _personio_rows(source: SourceContract, body: bytes) -> list[dict]:
    try:
        root = ET.fromstring(body, parser=ET.XMLParser(target=_SafeXMLBuilder()))
    except (ET.ParseError, LookupError) as exc:
        raise ConnectorError("invalid_source_xml") from exc
    if root.tag != "workzag-jobs" or any(node.tag != "position" for node in root):
        raise ConnectorError("invalid_source_xml")
    if len(root) > MAX_POSTINGS:
        raise ConnectorError("scan_limit_exceeded")
    language, host = _feed_language(source), _feed_host(source)
    rows = []
    for node in root:
        identity = node.findtext("id", "").strip()
        if not re.fullmatch(r"[0-9]{1,160}", identity):
            raise ConnectorError("invalid_posting_identifier")
        # Duplicate fields are ambiguous; do not silently accept a second ID.
        if len({child.tag for child in node}) != len(node):
            raise ConnectorError("invalid_source_xml")
        descriptions = []
        for item in node.findall("jobDescriptions/jobDescription"):
            text = plain_text(item.findtext("value", ""))
            if text:
                descriptions.append(plain_text(item.findtext("name", "")) + "\n" + text)
        rows.append({
            "id": identity, "title": node.findtext("name", ""),
            "location": node.findtext("office", ""),
            "description": "\n\n".join(descriptions).strip(),
            "employmentType": node.findtext("employmentType", ""), "schedule": node.findtext("schedule", ""),
            "url": f"https://{host}/job/{identity}?display={language}", "language": language,
        })
    return rows


def _public_feed_rows(source: SourceContract, client: httpx.Client) -> list[dict]:
    reader = _PublicRead(client)
    params = {"details": "true"} if source.platform == "workable" else {"language": _feed_language(source)} if source.platform == "personio" else None
    endpoint = _endpoint(source)
    # Workable's documented public URL now returns this exact account-scoped 302.
    # Never enable generic redirects, guess a tenant, or enter its credentialed SPI.
    redirect = (f"https://apply.workable.com/api/v1/widget/accounts/{source.board_token}?details=true"
                if source.platform == "workable" else None)
    if source.platform == "personio":
        rows = _personio_rows(source, reader.body(endpoint, params))
        verification = _personio_rows(source, reader.body(endpoint, params))
    else:
        data = reader.get(endpoint, params, expected_redirect=redirect)
        rows = _complete_feed_collection(data, "jobs" if source.platform == "workable" else "data")
        check = reader.get(endpoint, params, expected_redirect=redirect)
        verification = _complete_feed_collection(check, "jobs" if source.platform == "workable" else "data")
    # These public endpoints document complete collections, not page traversal.
    # Reject any pagination indicators instead of guessing an undocumented API.
    if payload_fingerprint(rows) != payload_fingerprint(verification):
        raise ConnectorError("source_changed_during_scan")
    if len(rows) > MAX_POSTINGS:
        raise ConnectorError("scan_limit_exceeded")
    return rows


def _complete_feed_collection(data: dict, key: str) -> list[dict]:
    rows = data.get(key)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ConnectorError("invalid_source_payload")
    if any(data.get(field) for field in ("next", "next_page", "pagination", "paging", "links", "meta")):
        raise ConnectorError("undocumented_feed_pagination")
    if "total" in data and (type(data["total"]) is not int or data["total"] != len(rows)):
        raise ConnectorError("incomplete_scan")
    if len(rows) > MAX_POSTINGS:
        raise ConnectorError("scan_limit_exceeded")
    return rows


def _smartrecruiters_identity(source: SourceContract, row: dict) -> str:
    if (row.get("company") or {}).get("identifier") != source.board_token:
        raise ConnectorError("posting_company_mismatch", safe_to_retry=False)
    identity = str(row.get("id") or row.get("uuid") or "")
    _endpoint(source, identity)
    if not identity:
        raise ConnectorError("incomplete_posting_payload")
    return identity


def _smartrecruiters_detail(source: SourceContract, reader: _SmartRecruitersRead,
                          identity: str, summary: dict | None = None) -> dict:
    row = reader.get(_endpoint(source, identity))
    _smartrecruiters_identity(source, row)
    if identity not in {str(row.get("id") or ""), str(row.get("uuid") or "")}:
        raise ConnectorError("posting_identity_changed", safe_to_retry=False)
    if summary and summary.get("uuid") and row.get("uuid") != summary["uuid"]:
        raise ConnectorError("posting_identity_changed", safe_to_retry=False)
    if type(row.get("active")) is not bool:
        raise ConnectorError("posting_status_missing")
    # List summaries can carry release/location/language metadata omitted from
    # details. Content and active status must always come from the detail read.
    return {**(summary or {}), **row}


def _smartrecruiters_destination(source: SourceContract, row: dict, url: str) -> str:
    safe = safe_origin_url(url, source)
    parsed = urlsplit(safe)
    if parsed.hostname in READ_HOSTS["smartrecruiters"]:
        parts = parsed.path.strip("/").split("/")
        identifiers = {str(row.get("id") or ""), str(row.get("uuid") or "")} - {""}
        if len(parts) != 2 or not any(parts[1] == value or parts[1].startswith(value + "-") for value in identifiers):
            raise ConnectorError("job_destination_posting_mismatch", safe_to_retry=False)
    return safe


def _smartrecruiters_list(source: SourceContract, reader: _SmartRecruitersRead) -> list[dict]:
    rows, seen = [], set()
    offset, total = 0, None
    while total is None or offset < total:
        data = reader.get(_endpoint(source), {
            "limit": SMARTRECRUITERS_PAGE_SIZE, "offset": offset, "destination": "PUBLIC",
        })
        limit, returned_offset, found = data.get("limit"), data.get("offset"), data.get("totalFound")
        page = data.get("content")
        if (type(limit) is not int or not 0 < limit <= SMARTRECRUITERS_PAGE_SIZE or
                type(returned_offset) is not int or returned_offset != offset or
                type(found) is not int or found < 0 or not isinstance(page, list)):
            raise ConnectorError("incomplete_scan")
        if found > MAX_POSTINGS:
            raise ConnectorError("scan_limit_exceeded")
        if total is not None and total != found:
            raise ConnectorError("source_changed_during_scan")
        total = found
        if len(page) != min(limit, total - offset):
            raise ConnectorError("incomplete_scan")
        for summary in page:
            identity = _smartrecruiters_identity(source, summary)
            if identity in seen:
                raise ConnectorError("duplicate_posting_identifiers")
            seen.add(identity)
            rows.append(summary)
        offset += len(page)
    return rows


def _smartrecruiters_rows(source: SourceContract, client: httpx.Client) -> list[dict]:
    reader = _SmartRecruitersRead(client)
    summaries = _smartrecruiters_list(source, reader)
    rows = [_smartrecruiters_detail(source, reader, _smartrecruiters_identity(source, row), row)
            for row in summaries]
    # Offset pagination has no snapshot token. Verify the manifest again after
    # details, including equal-count replacements that totalFound cannot detect.
    def manifest(items):
        return [(row.get("id"), row.get("uuid"), row.get("releasedDate")) for row in items]
    if manifest(summaries) != manifest(_smartrecruiters_list(source, reader)):
        raise ConnectorError("source_changed_during_scan")
    return rows


def _normalize(source: SourceContract, row: dict) -> dict | None:
    if source.platform == "greenhouse":
        if row.get("internal_job_id") is None:
            return None  # prospect pools are not live requisitions
        identity = str(row["id"])
        location = str((row.get("location") or {}).get("name") or "")
        url = row.get("absolute_url") or f"https://job-boards.greenhouse.io/{source.board_token}/jobs/{identity}"
        description = plain_text(row.get("content"))
        publication = parse_date(row.get("first_published"))
        updated = parse_date(row.get("updated_at"))
        remote = bool(re.search(r"\bremote\b", location, re.I))
        title = row.get("title")
        requisition = row.get("internal_job_id")
    elif source.platform == "lever":
        identity = str(row["id"])
        location = str((row.get("categories") or {}).get("location") or "")
        url = row.get("hostedUrl")
        description = plain_text(row.get("descriptionPlain") or row.get("description"))
        for item in row.get("lists") or []:
            description += "\n" + plain_text(item.get("text")) + "\n" + plain_text(item.get("content"))
        description += "\n" + plain_text(row.get("additionalPlain") or row.get("additional"))
        publication = parse_date(row.get("createdAt"))
        updated = None
        remote = row.get("workplaceType") == "remote"
        title = row.get("text")
        requisition = identity
    elif source.platform == "smartrecruiters":
        if row.get("active") is not True:
            return None
        identity = _smartrecruiters_identity(source, row)
        details = row.get("location") or {}
        if "remote" in details and type(details["remote"]) is not bool:
            raise ConnectorError("invalid_posting_remote_status")
        country = str(details.get("country") or "").upper()
        if country and not re.fullmatch(r"[A-Z]{2}", country):
            raise ConnectorError("invalid_posting_country")
        location = ", ".join(str(details[key]) for key in ("city", "region", "country") if details.get(key))
        url = row.get("postingUrl") or row.get("applyUrl")
        sections = (row.get("jobAd") or {}).get("sections") or {}
        description = "\n\n".join(plain_text(sections.get(key, {}).get("text")) for key in (
            "companyDescription", "jobDescription", "qualifications", "additionalInformation",
        )).strip()
        # This is the vendor's release/republication date, not an inferred
        # first publication date. Missing dates stay unknown.
        publication = parse_date(row.get("releasedDate"))
        updated = None
        remote = details.get("remote") is True
        title = row.get("name")
        requisition = row.get("jobId") or row.get("refNumber") or identity
    elif source.platform == "workable":
        identity = str(row.get("shortcode") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", identity):
            raise ConnectorError("invalid_posting_identifier")
        location = ", ".join(str(row[key]) for key in ("city", "state", "country") if row.get(key))
        if "telecommuting" in row and type(row["telecommuting"]) is not bool:
            raise ConnectorError("invalid_posting_remote_status")
        remote = row.get("workplace_type") == "remote" or row.get("telecommuting") is True
        url = row.get("url") or row.get("application_url")
        description = plain_text(row.get("description"))
        publication, updated = parse_date(row.get("published_on")), None
        title, requisition = row.get("title"), row.get("code") or identity
    elif source.platform == "personio":
        identity, title = str(row["id"]), row.get("title")
        location, description, url = str(row.get("location") or ""), plain_text(row.get("description")), row.get("url")
        # createdAt is creation, not proven first publication. Do not repurpose it.
        publication, updated, remote, requisition = None, None, False, identity
    elif source.platform == "pinpoint":
        identity = str(row.get("id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", identity):
            raise ConnectorError("invalid_posting_identifier")
        if row.get("deadline_at"):
            deadline = parse_date(row["deadline_at"])
            if deadline is None:
                raise ConnectorError("invalid_posting_deadline")
            if deadline <= datetime.now(UTC):
                return None
        location = str((row.get("location") or {}).get("name") or "")
        description = "\n\n".join(plain_text(row.get(key)) for key in (
            "description", "key_responsibilities", "skills_knowledge_expertise", "benefits",
        )).strip()
        title, url = row.get("title"), row.get("url")
        publication, updated = None, None
        remote = row.get("workplace_type") == "remote"
        requisition = (row.get("job") or {}).get("requisition_id") or row.get("requisition_id") or (row.get("job") or {}).get("id") or identity
    elif source.platform == "ashby":
        if row.get("isListed") is not True:
            return None
        identity = str(row["id"])
        location = str(row.get("location") or "")
        url = row.get("jobUrl")
        description = plain_text(row.get("descriptionPlain") or row.get("descriptionHtml"))
        publication = parse_date(row.get("publishedAt"))
        updated = None
        remote = row.get("isRemote") is True
        title = row.get("title")
        requisition = identity
    else:
        raise ConnectorError("unsupported_source")
    if not identity or not title or not description:
        raise ConnectorError("incomplete_posting_payload")
    if source.platform == "smartrecruiters":
        canonical_url = _smartrecruiters_destination(source, row, str(url or ""))
        apply_url = _smartrecruiters_destination(source, row, str(row.get("applyUrl") or canonical_url))
    elif source.platform in {"workable", "pinpoint"}:
        canonical_url = _feed_destination(source, row, str(url or ""))
        apply_url = _feed_destination(source, row, str(row.get("application_url") or canonical_url))
    else:
        canonical_url = safe_origin_url(str(url or ""), source)
        apply_url = safe_origin_url(str(row.get("applyUrl") or row.get("applicationUrl") or canonical_url), source)
    result = {
        "external_id": identity, "requisition_id": str(requisition), "title": str(title)[:300],
        "employer": source.employer, "location": location[:400], "description": description[:100_000],
        "remote": remote, "canonical_url": canonical_url, "apply_url": apply_url,
        "language": str(_feed_language(source) if source.platform in {"personio", "pinpoint"} else row.get("language") or "en")[:20], "publication_at": publication,
        "source_updated_at": updated,
    }
    if source.platform == "workable" and not row.get("language"):
        result["language"] = "und"
    if source.platform == "smartrecruiters":
        result["country"] = country or None
    from .preference_metadata import normalize_metadata
    result["preference_metadata"] = normalize_metadata(
        source, row, feed_language=_feed_language(source) if source.platform in {"personio", "pinpoint"} else None,
    )
    result["content_sha256"] = payload_fingerprint({key: value for key, value in result.items() if key not in {"source_updated_at", "publication_at"}})
    return result


def fetch_postings(source: SourceContract, client: httpx.Client | httpx.AsyncClient | None = None) -> list[dict]:
    if source.platform == "lever":
        # Production entry points are synchronous worker/threadpool functions.
        # A sync injected transport cannot provide an absolute HTTP deadline.
        if client is not None and not isinstance(client, httpx.AsyncClient):
            raise ConnectorError("unsupported_public_transport", safe_to_retry=False)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise ConnectorError("unsupported_public_execution_context", safe_to_retry=False)
        try:
            rows = asyncio.run(_lever_rows(source, client))
        except host_pacing.PacingError as exc:
            raise ConnectorError(str(exc)) from exc
        return _normalized_postings(source, rows)
    if isinstance(client, httpx.AsyncClient):
        raise ConnectorError("unsupported_public_transport", safe_to_retry=False)
    owned_client = client is None
    client = client or _client()
    try:
        if source.platform == "greenhouse":
            data = _json_get(client, _endpoint(source), {"content": "true"})
            rows = data.get("jobs")
            if not isinstance(rows, list) or (data.get("meta") or {}).get("total", len(rows)) != len(rows):
                raise ConnectorError("incomplete_scan")
        elif source.platform == "smartrecruiters":
            rows = _smartrecruiters_rows(source, client)
        elif source.platform in {"workable", "personio", "pinpoint"}:
            rows = _public_feed_rows(source, client)
        else:
            data = _json_get(client, _endpoint(source))
            rows = data.get("jobs")
            if not isinstance(rows, list):
                raise ConnectorError("invalid_source_payload")
        return _normalized_postings(source, rows)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ConnectorError("invalid_source_payload") from exc
    finally:
        if owned_client:
            client.close()


def _normalized_postings(source: SourceContract, rows: list) -> list[dict]:
    if len(rows) > MAX_POSTINGS:
        raise ConnectorError("scan_limit_exceeded")
    try:
        results = [job for row in rows if (job := _normalize(source, row))]
    except (KeyError, TypeError, AttributeError) as exc:
        raise ConnectorError("invalid_source_payload") from exc
    if len({job["external_id"] for job in results}) != len(results):
        raise ConnectorError("duplicate_posting_identifiers")
    return results


def load_form(source: SourceContract, external_id: str, client: httpx.Client | None = None) -> dict:
    if source.platform == "smartrecruiters":
        owned_client = client is None
        client = client or _client()
        try:
            row = _smartrecruiters_detail(source, _SmartRecruitersRead(client), external_id)
            job = _normalize(source, row)
            if not job:
                raise ConnectorError("posting_or_source_closed", safe_to_retry=False)
            return {"version": payload_fingerprint({"source": source.id, "posting": external_id,
                                                    "content": job["content_sha256"]}),
                    "fields": [], "consents": [], "supported": False,
                    "job_content_sha256": job["content_sha256"],
                    "handoff_reason": "Complete hosted form is required; public posting details do not grant application submission permission or expose the complete form."}
        except (KeyError, TypeError, AttributeError) as exc:
            raise ConnectorError("invalid_source_payload") from exc
        finally:
            if owned_client:
                client.close()
    if source.platform != "greenhouse":
        jobs = fetch_postings(source, client)
        if not any(job["external_id"] == external_id for job in jobs):
            raise ConnectorError("posting_or_source_closed", safe_to_retry=False)
        return {"version": payload_fingerprint({"source": source.id, "posting": external_id}),
                "fields": [], "consents": [], "supported": False,
                "handoff_reason": "Complete hosted form is required; this provider's public feed does not expose all application questions."}
    owned_client = client is None
    client = client or _client()
    try:
        data = _json_get(client, _endpoint(source, external_id), {"questions": "true"})
        if str(data.get("id")) != external_id:
            raise ConnectorError("posting_identity_changed")
        deadline = parse_date(data.get("application_deadline"))
        if deadline and deadline <= datetime.now(UTC):
            raise ConnectorError("posting_or_source_closed", safe_to_retry=False)
        fields, required_groups = [], []
        unsupported = bool(data.get("demographic_questions") or data.get("data_compliance") or data.get("compliance") or data.get("location_questions") or data.get("include_ai_disclaimer"))
        mapping = {"input_text": "text", "textarea": "textarea", "input_file": "file",
                   "input_hidden": "hidden", "multi_value_single_select": "single_select",
                   "multi_value_multi_select": "multi_select"}
        questions = data.get("questions")
        if not isinstance(questions, list) or not questions:
            unsupported = True
            questions = []
        for index, question in enumerate(questions):
            group = f"question-group-{index}"
            ids = []
            for field in question.get("fields") or []:
                identity = str(field.get("name") or "")
                field_type = mapping.get(field.get("type"))
                if not identity or field_type is None or field_type == "hidden":
                    unsupported = True
                if field_type == "file" and identity != "resume":
                    unsupported = True
                ids.append(identity)
                fields.append({
                    "id": identity, "label": str(question.get("label") or identity),
                    "type": "email" if identity == "email" else field_type or "text",
                    "required": bool(question.get("required")) and len(question.get("fields") or []) == 1,
                    "group": group,
                    "options": [{"value": str(value.get("value")), "label": str(value.get("label"))}
                                for value in field.get("values") or []],
                })
            if question.get("required"):
                required_groups.append(ids)
        for identity, label in (("first_name", "First name"), ("last_name", "Last name"), ("email", "Email")):
            if not any(field["id"] == identity for field in fields):
                fields.append({"id": identity, "label": label, "type": "email" if identity == "email" else "text", "required": True, "options": []})
            else:
                for field in fields:
                    if field["id"] == identity:
                        field["required"] = True
        # Snapshot all provider policy and question structures, even if unsupported.
        version_payload = {key: data.get(key) for key in (
            "id", "questions", "location_questions", "compliance", "demographic_questions",
            "data_compliance", "include_ai_disclaimer", "ai_disclaimer", "ai_opt_out_request_url",
        )}
        return {
            "version": payload_fingerprint(version_payload), "fields": fields,
            "consents": [], "required_groups": required_groups,
            "supported": not unsupported,
            "policies": {"ai_disclaimer": plain_text(data.get("ai_disclaimer")),
                         "ai_opt_out_request_url": data.get("ai_opt_out_request_url")},
            "handoff_reason": "This form has additional consent, demographic, location or file requirements requiring the employer portal." if unsupported else None,
            "job_content_sha256": (_normalize(source, data) or {}).get("content_sha256") if data.get("content") else None,
        }
    finally:
        if owned_client:
            client.close()


def submit_greenhouse(source: SourceContract, external_id: str, *, credential: str,
                      answers: dict, content: bytes, filename: str, media_type: str,
                      form: dict | None = None) -> tuple[int, dict | None]:
    """One POST only. Any unrecognized response is an uncertain outcome."""
    if source.platform != "greenhouse" or not source.submission_enabled or not source.form_parity_verified or not source.submission_grant:
        raise ConnectorError("submission_not_authorized", safe_to_retry=False)
    payload = dict(answers)
    # Greenhouse selection option IDs are numeric. Preserve text questions,
    # including numeric prose, and convert only declared selection fields.
    for field in (form or {}).get("fields", []):
        identity = field["id"]
        if identity in payload and field["type"] in {"single_select", "multi_select"}:
            values = payload[identity] if isinstance(payload[identity], list) else [payload[identity]]
            mapped = [int(value) if re.fullmatch(r"-?\d+", value) else value for value in values]
            payload[identity] = mapped if field["type"] == "multi_select" else mapped[0]
    payload["resume_content"] = base64.b64encode(content).decode("ascii")
    payload["resume_content_filename"] = filename
    with _client() as client:
        try:
            response = client.post(_endpoint(source, external_id), json=payload,
                                   auth=httpx.BasicAuth(credential, ""))
        except httpx.HTTPError as exc:
            raise ConnectorError("submission_outcome_unknown", safe_to_retry=False) from exc
        if response.status_code >= 500 or response.status_code in {408, 409, 429}:
            return response.status_code, None
        if response.status_code >= 400:
            raise ConnectorError(f"submission_rejected_{response.status_code}", safe_to_retry=False)
        try:
            result = response.json()
        except ValueError:
            return response.status_code, None
        contract = source.receipt_contract or {}
        receipt_id = result.get(contract.get("receipt_id_field", "")) if isinstance(result, dict) else None
        complete = result.get(contract.get("completion_field", "")) if isinstance(result, dict) else None
        if (response.status_code in {200, 201} and receipt_id and
                "completion_value" in contract and type(complete) is type(contract["completion_value"]) and complete == contract["completion_value"] and
                not result.get("errors")):
            return response.status_code, {"provider": "greenhouse", "application_id": str(receipt_id),
                                          "completion_verified": True, "received_at": datetime.now(UTC).isoformat(),
                                          "response_sha256": hashlib.sha256(response.content).hexdigest()}
        return response.status_code, None
