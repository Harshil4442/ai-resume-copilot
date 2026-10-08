"""Real Storage SDK HTTP request construction with an intercepted local session."""
from __future__ import annotations

import hashlib
import io
import json
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import google.auth
import pytest
import requests
from google.auth.credentials import AnonymousCredentials
from google.cloud.storage import Client
from urllib3.response import HTTPResponse

from app.domains.recovery import gcp_journal
from app.domains.recovery.contracts import canonical
from app.domains.recovery.gcp_contracts import (
    JournalIntent,
    JournalReceipt,
    OpeningHold,
    RegistryPin,
    WitnessBody,
    WitnessPin,
)
from app.domains.recovery.gcp_journal import GcsJournal, GcsWitnessFence
from app.domains.recovery.gcp_media import BoundedSink
from app.domains.recovery.store import GuardUnavailable


class CountedRaw(HTTPResponse):
    def __init__(self, body: bytes) -> None:
        super().__init__(body=io.BytesIO(body), preload_content=False)
        self.requested: list[int | None] = []
        self.read_bytes = 0

    def read(self, amt=None, *args, **kwargs):
        self.requested.append(amt)
        data = super().read(amt, *args, **kwargs)
        self.read_bytes += len(data)
        return data


def response(body: bytes, *, status: int = 200) -> requests.Response:
    result = requests.Response()
    result.status_code, result._content = status, False
    result.headers["Content-Length"] = str(len(body))
    result.raw = CountedRaw(body)
    result.request = requests.Request("POST", "https://synthetic.invalid").prepare()
    return result


def operation() -> JournalIntent:
    return JournalIntent(operation_id=UUID("52b772f7-c9ce-4359-92c3-f0b831712356"),
        pin=RegistryPin(database="projects/hirewiz-local-authority/databases/authority-test",
            database_uid="9012403f-fbb6-4cda-b94f-a9dc7200e928",
            authority_id="ad4d3c65-f9f4-4dd2-a55b-78bdbd151025", incarnation=1,
            epoch_id="571d949d-1a23-4f10-ad39-4eff638ccf04"),
        effect=OpeningHold(subject_uuid="a4451f4a-6431-4541-9a89-3a77019e4218",
            employer_key="a" * 64, tenant_id="synthetic", opening_key="b" * 64,
            binding_sha256="c" * 64), created_at_ms=1_800_000_000_000,
        deadline_ms=1_800_000_010_000)


@pytest.mark.parametrize("upload_result", ["success", "412", "timeout"])
def test_actual_storage_sdk_generation_zero_and_fenced_byte_get_without_adc_or_retry(
    monkeypatch: pytest.MonkeyPatch, upload_result: str,
) -> None:
    def forbid_adc(*args, **kwargs):
        raise AssertionError("No ambient credential lookup is permitted")
    monkeypatch.setattr(google.auth, "default", forbid_adc)
    monkeypatch.setenv("DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA", "true")
    session = MagicMock(spec=requests.Session)
    session.is_mtls = False
    client = Client(project="hirewiz-local-authority", credentials=AnonymousCredentials(),
                    _http=session)
    intent = operation()
    raw = canonical(intent.model_dump(mode="json")).encode()
    metadata = json.dumps({"name": "synthetic", "bucket": "synthetic-authority-journal",
                           "generation": "41", "size": str(len(raw))}).encode()
    first = (response(metadata) if upload_result == "success" else
             response(b'{"error":{"code":412,"message":"synthetic exists"}}', status=412)
             if upload_result == "412" else requests.exceptions.Timeout("synthetic response loss"))
    session.request.side_effect = [first, response(metadata), response(metadata), response(raw)]
    receipt = GcsJournal(client.bucket("synthetic-authority-journal")).write(intent)
    assert receipt.generation == "41" and receipt.sha256 == intent.digest
    assert session.request.call_count == 4  # No hidden SDK retry or DELETE.
    calls = session.request.call_args_list
    methods = [call.args[0] if call.args else call.kwargs["method"] for call in calls]
    assert methods == ["POST", "GET", "GET", "GET"]
    urls = [call.args[1] if len(call.args) > 1 else call.kwargs["url"] for call in calls]
    queries = [parse_qs(urlsplit(url).query) for url in urls]
    assert queries[0]["ifGenerationMatch"] == ["0"]
    assert queries[0]["uploadType"] == ["multipart"]
    assert "generation" not in queries[1]  # Read the live generation first.
    assert queries[2]["generation"] == queries[2]["ifGenerationMatch"] == ["41"]
    assert "alt" not in queries[2]  # Fresh exact-generation size metadata.
    assert queries[3]["generation"] == queries[3]["ifGenerationMatch"] == ["41"]
    assert queries[3]["alt"] == ["media"]
    assert calls[3].kwargs["headers"]["range"] == f"bytes=0-{len(raw)}"
    assert calls[3].kwargs["stream"] is True and calls[3].kwargs["allow_redirects"] is False
    assert all(call.kwargs["timeout"] == 2.0 for call in calls)
    # The actual SDK's multipart body carries canonical intent bytes, not a metadata hash.
    assert raw in calls[0].kwargs["data"]


def test_actual_storage_sdk_is_denied_before_io_when_background_metadata_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA", raising=False)
    session = MagicMock(spec=requests.Session)
    session.is_mtls = False
    client = Client(project="hirewiz-local-authority", credentials=AnonymousCredentials(),
                    _http=session)
    with pytest.raises(GuardUnavailable, match="metadata suppression"):
        GcsJournal(client.bucket("synthetic-authority-journal")).write(operation())
    session.request.assert_not_called()


def test_shared_populated_sdk_metadata_cache_is_denied_before_hidden_404_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA", "true")
    session = MagicMock(spec=requests.Session)
    session.is_mtls = False
    client = Client(project="hirewiz-local-authority", credentials=AnonymousCredentials(),
                    _http=session)
    client._bucket_metadata_cache.update_cache("synthetic-authority-journal", "synthetic", "global")
    with pytest.raises(GuardUnavailable, match="fresh dedicated"):
        GcsJournal(client.bucket("synthetic-authority-journal")).write(operation())
    session.request.assert_not_called()


FLOWS = ["success", "412", "timeout", "status", "witness"]


def read_case(monkeypatch, flow, *, live_changes=None, exact_changes=None, media_kind="exact"):
    monkeypatch.setenv("DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA", "true")
    def forbid_adc(*args, **kwargs):
        raise AssertionError("No ambient credential lookup is permitted")
    monkeypatch.setattr(google.auth, "default", forbid_adc)
    session = MagicMock(spec=requests.Session)
    session.is_mtls = False
    client = Client(project="hirewiz-local-authority", credentials=AnonymousCredentials(), _http=session)
    sinks = []
    class ObservedSink(BoundedSink):
        def __init__(self, length):
            super().__init__(length)
            self.peak = 0
            sinks.append(self)
        def write(self, data):
            result = super().write(data)
            self.peak = max(self.peak, self.tell())
            return result
    monkeypatch.setattr(gcp_journal, "BoundedSink", ObservedSink)
    intent = operation()
    journal = GcsJournal(client.bucket("synthetic-authority-journal"))
    if flow == "witness":
        body = WitnessBody(registry=intent.pin, state="OPEN", manifest_generation="123",
                           manifest_sha256="d" * 64, partitions_sha256="e" * 64)
        raw = canonical(body.model_dump(mode="json")).encode()
        config = WitnessPin(registry=intent.pin, bucket="synthetic-authority-journal",
            path="witness/current", generation="41", sha256=hashlib.sha256(raw).hexdigest())
        class SyntheticIdentity:
            def identity(self):
                return {"name": intent.pin.database, "uid": str(intent.pin.database_uid),
                        "type": "FIRESTORE_NATIVE"}
        fence = GcsWitnessFence(client.bucket(config.bucket), SyntheticIdentity(), config, body)
        def run():
            return fence.check(intent.pin)
    else:
        path, raw = journal._intent_bytes(intent)
        if flow == "status":
            receipt = JournalReceipt(bucket="synthetic-authority-journal", path=path,
                                     generation="41", sha256=intent.digest)
            def run():
                return journal.verify(intent, receipt)
        else:
            def run():
                return journal.write(intent)
    metadata = {"generation": "41", "size": str(len(raw))}
    def encoded(changes):
        result = {**metadata, **(changes or {})}
        if result.get("size") == "missing":
            result.pop("size")
        if result.get("size") == "mismatch":
            result["size"] = str(len(raw) + 1)
        return json.dumps(result).encode()
    media_body = (raw + b"x" * (1_048_576 - len(raw)) if media_kind == "extra" else
                  raw[:-1] if media_kind == "truncated" else
                  b"X" + raw[1:] if media_kind == "mismatch" else raw)
    media = response(media_body, status=302 if media_kind == "redirect" else
                     500 if media_kind == "error" else 200)
    raw_reader = media.raw
    if media_kind == "preloaded":
        media._content, media._content_consumed = raw, True
    if media_kind == "redirect":
        media.headers["Location"] = "https://synthetic.invalid/no-follow"
    if media_kind == "encoded":
        media.headers["Content-Encoding"] = "gzip"
    prefix = []
    if flow in {"success", "412", "timeout"}:
        prefix = [response(encoded(None)) if flow == "success" else
                  response(b'{"error":{"code":412}}', status=412) if flow == "412" else
                  requests.exceptions.Timeout("synthetic stored upload response loss")]
    replies = prefix + [response(encoded(live_changes)), response(encoded(exact_changes)), media]
    session.request.side_effect = replies
    return SimpleNamespace(run=run, session=session, client=client, media=media,
                           raw=raw_reader, length=len(raw), upload=len(prefix), sinks=sinks,
                           replies=replies)


def assert_read_boundary(case, *, media_sent: bool) -> None:
    calls = case.session.request.call_args_list
    methods = [call.args[0] if call.args else call.kwargs["method"] for call in calls]
    urls = [call.args[1] if len(call.args) > 1 else call.kwargs["url"] for call in calls]
    assert set(methods) <= {"GET", "POST"} and methods.count("POST") == case.upload
    assert all("/o/" in url for method, url in zip(methods, urls, strict=True) if method == "GET")
    assert case.client._http is case.session  # No shared session/client replacement.
    case.session.close.assert_not_called()
    assert all(sink.peak <= case.length + 1 and sink.closed for sink in case.sinks)
    if media_sent:
        assert len(calls) == case.upload + 3
        assert calls[-1].kwargs["headers"]["range"] == f"bytes=0-{case.length}"
        assert calls[-1].kwargs["allow_redirects"] is False and calls[-1].kwargs["stream"] is True
        query = parse_qs(urlsplit(urls[-1]).query)
        assert query["generation"] == query["ifGenerationMatch"] == ["41"]
        assert case.raw.read_bytes <= case.length + 1 and case.raw.closed
    else:
        assert case.raw.read_bytes == 0


@pytest.mark.parametrize("flow", ["status", "witness"])
def test_receipt_status_and_live_witness_paths_use_exact_size_range_and_owned_cleanup(monkeypatch, flow):
    case = read_case(monkeypatch, flow)
    case.run()
    assert case.raw.read_bytes == case.length
    assert_read_boundary(case, media_sent=True)


@pytest.mark.parametrize("flow", FLOWS)
@pytest.mark.parametrize("phase", ["live", "exact"])
def test_one_megabyte_metadata_denies_before_media_on_all_journal_and_witness_paths(monkeypatch, flow, phase):
    changes = {f"{phase}_changes": {"size": "1048576"}}
    case = read_case(monkeypatch, flow, **changes)
    with pytest.raises(GuardUnavailable, match="size"):
        case.run()
    assert case.session.request.call_count == case.upload + (1 if phase == "live" else 2)
    assert_read_boundary(case, media_sent=False)


@pytest.mark.parametrize("flow", ["status", "witness"])
@pytest.mark.parametrize("size", ["missing", None, True, 2.0, "01", "mismatch"])
def test_exact_generation_size_requires_present_canonical_matching_wire_value(monkeypatch, flow, size):
    case = read_case(monkeypatch, flow, exact_changes={"size": size})
    with pytest.raises(GuardUnavailable, match="size"):
        case.run()
    assert case.session.request.call_count == case.upload + 2
    assert_read_boundary(case, media_sent=False)


@pytest.mark.parametrize("generation", [41, 41.0, "041", None])
def test_exact_generation_wire_metadata_cannot_normalize_into_the_pinned_generation(monkeypatch, generation):
    case = read_case(monkeypatch, "status", exact_changes={"generation": generation})
    with pytest.raises(GuardUnavailable, match="generation"):
        case.run()
    assert_read_boundary(case, media_sent=False)


@pytest.mark.parametrize("flow", FLOWS)
@pytest.mark.parametrize("kind", ["extra", "truncated", "mismatch"])
def test_malformed_media_is_denied_with_read_and_retained_bytes_bounded(monkeypatch, flow, kind):
    case = read_case(monkeypatch, flow, media_kind=kind)
    with pytest.raises(GuardUnavailable, match="byte|length"):
        case.run()
    if kind == "extra":
        assert case.raw.read_bytes == case.length + 1  # 1MiB body not fully consumed.
    assert_read_boundary(case, media_sent=True)


@pytest.mark.parametrize("flow", ["status", "witness"])
@pytest.mark.parametrize("kind", ["preloaded", "redirect", "error", "encoded"])
def test_preconsumed_redirect_error_and_encoded_media_are_not_materialized(monkeypatch, flow, kind):
    case = read_case(monkeypatch, flow, media_kind=kind)
    with pytest.raises(GuardUnavailable, match="fresh, raw"):
        case.run()
    assert case.raw.read_bytes == 0
    assert_read_boundary(case, media_sent=True)


def test_stored_encoding_is_rejected_in_fresh_exact_metadata_before_media(monkeypatch):
    case = read_case(monkeypatch, "witness", exact_changes={"contentEncoding": "gzip"})
    with pytest.raises(GuardUnavailable, match="size"):
        case.run()
    assert_read_boundary(case, media_sent=False)


def test_body_guard_failure_does_not_change_shared_session_or_next_download_budget(monkeypatch):
    first = read_case(monkeypatch, "status", media_kind="extra")
    second = read_case(monkeypatch, "status")
    first.session.request.side_effect = first.replies + second.replies
    original = first.client._http
    with pytest.raises(GuardUnavailable, match="length"):
        first.run()
    first.run()  # Same real SDK client/session; the next response has a fresh body budget.
    assert first.client._http is original and first.session.request.call_count == 6
    first.session.close.assert_not_called()
    assert first.raw.read_bytes == first.length + 1 and first.raw.closed
    assert second.raw.read_bytes == second.length and second.raw.closed


@pytest.mark.parametrize("expected", [b"", None, bytearray(b"{}"), b"x" * 65_537])
def test_expected_byte_budget_is_validated_before_any_actual_sdk_io(monkeypatch, expected):
    case = read_case(monkeypatch, "status")
    with pytest.raises(GuardUnavailable, match="positive and bounded"):
        gcp_journal._exact_bytes(case.client.bucket("synthetic-authority-journal"),
            "synthetic/intent.json", expected, generation="41", timeout=2.0)
    case.session.request.assert_not_called()
    case.session.close.assert_not_called()


def test_maximum_expected_bytes_fit_multiple_actual_sdk_chunks_without_oversized_reads(monkeypatch):
    case = read_case(monkeypatch, "status")
    expected = b"x" * 65_536
    metadata = json.dumps({"generation": "41", "size": str(len(expected))}).encode()
    media = response(expected)
    raw_reader = media.raw
    case.session.request.side_effect = [response(metadata), response(metadata), media]
    assert gcp_journal._exact_bytes(case.client.bucket("synthetic-authority-journal"),
        "synthetic/intent.json", expected, generation="41", timeout=2.0) == "41"
    assert raw_reader.read_bytes == len(expected) and raw_reader.closed
    assert all(type(amount) is int and 0 < amount <= 8192 for amount in raw_reader.requested)
    assert case.session.request.call_args.kwargs["headers"]["range"] == "bytes=0-65536"
    assert case.client._http is case.session
    case.session.close.assert_not_called()
