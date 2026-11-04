"""Owned public-feed metadata only; no Redis service or provider is contacted."""
from __future__ import annotations

import copy
import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from backend.app.domains.employer import source_revalidation as cache

NOW = datetime(2026, 11, 4, 2, 3, 4, 123456, tzinfo=UTC)
PIPELINE = "a" * 64
VALIDATOR = {"etag": 'W/"source-revision"', "vary": ["accept"]}


def source(**changes):
    values = dict(
        id="source-public", employer="Example Engineering", platform="smartrecruiters",
        board_token="ExampleEngineering", region="global", allowed_hosts=["careers.example.com"],
        careers_url="https://careers.example.com/jobs", submission_enabled=False,
        submission_grant="private-configuration-is-hashed", credential_env="SYNTHETIC_CREDENTIAL",
        form_parity_verified=False, receipt_contract=None, employer_key="reviewed-employer",
        verification_url="https://careers.example.com/jobs", verification_note="Reviewed synthetic source",
        verified_by=1, verified_at=NOW, enabled=True, admission_policy={"daily": 3}, last_success_at=NOW,
    )
    return SimpleNamespace(**(values | changes))


def posting(identity="100", **changes):
    values = dict(
        source_id="source-public", external_id=identity, requisition_id=f"REQ-{identity}",
        title="Software Engineer", employer="Example Engineering", location="Bengaluru", country="IN",
        remote=False, description="Build Python services.",
        canonical_url=f"https://jobs.smartrecruiters.com/ExampleEngineering/{identity}-engineer",
        apply_url=f"https://www.smartrecruiters.com/ExampleEngineering/{identity}-engineer",
        language="en", preference_metadata={"provider": "smartrecruiters"},
        publication_at=NOW - timedelta(days=1), source_updated_at=None, content_sha256="b" * 64,
        skills=["python"], opening_key="c" * 64, is_open=True, closed_at=None,
    )
    return values | changes


def snapshot(*rows, src=None):
    value = cache.capture_snapshot(src or source(), rows or [posting()], pipeline=PIPELINE)
    assert value is not None
    return value


def metadata(snap=None, *, excluded=None):
    snap = snap or snapshot()
    entry = {"validator": copy.deepcopy(VALIDATOR), "summary_sha256": "d" * 64}
    entry |= ({"excluded": True} if excluded else
              {"posting_sha256": cache.posting_fingerprint(snap.postings["100"])})
    return {"collection": None, "details": {excluded or "100": entry}}


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.calls = []
        self.closed = False

    def get(self, key):
        self.calls.append(("GET", key))
        return self.values.get(key)

    def setex(self, key, seconds, raw):
        self.calls.append(("SETEX", key, seconds))
        self.values[key] = raw
        return True

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def isolated_revision_cache():
    revision = cache.pipeline_revision
    revision.cache_clear()
    yield
    revision.cache_clear()


def test_fixed_request_representation_is_anonymous_and_immutable():
    assert dict(cache.INGESTION_HEADERS) == {
        "User-Agent": "HireWiz-EmployerConnector/1.0", "Accept": "application/json",
        "Accept-Encoding": "identity", "Cache-Control": "no-cache, max-age=0",
    }
    with pytest.raises(TypeError):
        cache.INGESTION_HEADERS["Authorization"] = "must-not-be-added"


@pytest.mark.parametrize("etag", ['""', '"a,b\\c"', 'W/"revision"', '"' + "a" * 510 + '"'])
def test_opaque_quoted_etags_and_fixed_vary_are_retained(etag):
    value = cache.response_validator({"ETag": etag, "Vary": "Origin, Accept-Encoding, ACCEPT, User-Agent"})
    assert value == {"etag": etag, "vary": ["accept", "accept-encoding", "origin", "user-agent"]}
    assert cache.valid_validator(value)


@pytest.mark.parametrize("headers", [
    {}, {"ETag": "unquoted"}, {"ETag": 'w/"wrong-case"'}, {"ETag": '"a" "b"'},
    {"ETag": '"a", "b"'}, {"ETag": '"line\nbreak"'}, {"ETag": '"white space"'},
    {"ETag": '"' + "a" * 511 + '"'}, {"ETag": '"snowman-☃"'}, {"ETag": '"latin-\xe9"'},
    {"ETag": '"a"', "Vary": "*"}, {"ETag": '"a"', "Vary": "Cookie"},
    {"ETag": '"a"', "Cache-Control": 'public, No-Store="ignored-value"'},
    {"ETag": '"a"', "etag": '"b"'},
])
def test_unusable_response_validators_are_not_cached(headers):
    assert cache.response_validator(headers) is None


@pytest.mark.parametrize("value", [
    None, '"a"', {"etag": '"a"'}, {"etag": '"a"', "vary": [], "body": "forbidden"},
    {"etag": '"a"', "vary": ["Accept"]}, {"etag": '"a"', "vary": ["accept", "accept"]},
    {"etag": '"a"', "vary": "accept"}, {"etag": '"a"', "vary": [False]},
])
def test_validator_schema_is_closed(value):
    assert not cache.valid_validator(value)


def test_304_uses_weak_comparison_and_can_inherit_missing_response_etag():
    assert cache.matches_not_modified(VALIDATOR, {"ETag": '"source-revision"'})
    assert cache.matches_not_modified(VALIDATOR, {})
    assert cache.response_validator({}, VALIDATOR) == VALIDATOR
    assert not cache.matches_not_modified(VALIDATOR, {"ETag": '"different"'})
    assert not cache.matches_not_modified(VALIDATOR, {"Vary": "Authorization"})
    assert not cache.matches_not_modified(VALIDATOR, {"Cache-Control": "no-store"})
    assert not cache.matches_not_modified(None, {})


def test_snapshot_detaches_sql_objects_and_returns_fresh_projections():
    row, src = posting(), source()
    snap = snapshot(SimpleNamespace(**row), src=src)
    before = snap.binding
    row["skills"].append("changed")
    src.admission_policy["daily"] = 99
    returned = snap.postings
    returned["100"]["skills"].append("external-mutation")
    snap.binding["source_config_sha256"] = "0" * 64
    assert snap.postings["100"]["skills"] == ["python"]
    assert snap.binding == before
    assert "private-configuration-is-hashed" not in snap._binding_json
    with pytest.raises(FrozenInstanceError):
        snap.source_id = "another"


def test_snapshot_canonicalizes_offsets_and_sqlite_naive_utc_and_order():
    # An explicit offset and a naive SQLite UTC value describe the same SQL instant.
    offset = NOW.astimezone(timezone(timedelta(hours=5, minutes=30)))
    first = snapshot(posting("101"), posting(publication_at=NOW), src=source(last_success_at=NOW))
    second = snapshot(posting(publication_at=offset), posting("101"), src=source(last_success_at=NOW.replace(tzinfo=None)))
    assert first.binding == second.binding
    assert first.postings["100"]["publication_at"] == NOW.isoformat(timespec="microseconds")


@pytest.mark.parametrize("field,value", [
    ("employer", "Renamed"), ("board_token", "Different"), ("region", "eu"),
    ("allowed_hosts", ["different.example"]), ("verification_note", "Re-reviewed"),
    ("verified_at", NOW + timedelta(seconds=1)), ("verified_by", 2),
    ("submission_enabled", True), ("submission_grant", "different-grant"),
    ("receipt_contract", {"version": 2}), ("admission_policy", {"daily": 2}),
])
def test_routing_verification_and_admission_config_changes_break_binding(field, value):
    assert snapshot().binding != snapshot(src=source(**{field: value})).binding


@pytest.mark.parametrize("field,value", [
    ("publication_at", NOW), ("source_updated_at", NOW), ("is_open", False),
    ("closed_at", NOW), ("skills", ["java"]), ("opening_key", "e" * 64),
    ("description", "New complete job description"), ("country", "GB"),
])
def test_all_provider_dates_open_state_and_derived_fields_break_sql_binding(field, value):
    original = snapshot()
    changed = snapshot(posting(**{field: value}))
    assert original.binding != changed.binding
    assert cache.posting_fingerprint(original.postings["100"]) != cache.posting_fingerprint(changed.postings["100"])


def test_only_checkpoint_and_pipeline_changes_also_break_binding():
    original = snapshot()
    assert original.binding != snapshot(src=source(last_success_at=NOW + timedelta(microseconds=1))).binding
    changed = cache.capture_snapshot(source(), [posting()], pipeline="f" * 64)
    assert changed is not None and changed.binding != original.binding
    assert snapshot(posting(first_seen_at=NOW, last_checked_at=NOW)).binding == original.binding


def test_absent_invalid_or_unbounded_sql_snapshots_disable_optimization(monkeypatch):
    assert cache.capture_snapshot(source(last_success_at=None), [posting()], pipeline=PIPELINE) is None
    assert cache.capture_snapshot(source(enabled=False), [posting()], pipeline=PIPELINE) is None
    assert cache.capture_snapshot(source(), [posting()], pipeline="unversioned") is None
    assert cache.capture_snapshot(source(), [posting(), posting()], pipeline=PIPELINE) is None
    assert cache.capture_snapshot(source(), [posting(source_id="other")], pipeline=PIPELINE) is None
    assert cache.capture_snapshot(source(), [posting(publication_at="invalid")], pipeline=PIPELINE) is None
    assert cache.capture_snapshot(source(), [posting(remote=1)], pipeline=PIPELINE) is None
    assert cache.source_fingerprint(SimpleNamespace(id="missing-config")) is None
    monkeypatch.setattr(cache, "pipeline_revision", lambda: None)
    assert cache.capture_snapshot(source(), [posting()]) is None
    monkeypatch.setattr(cache, "MAX_POSTINGS", 1)
    assert cache.capture_snapshot(source(), [posting(), posting("101")], pipeline=PIPELINE) is None
    monkeypatch.setattr(cache, "MAX_SNAPSHOT_BYTES", 20)
    assert cache.capture_snapshot(source(), [posting()], pipeline=PIPELINE) is None


def test_completed_empty_sql_snapshot_is_distinct_from_no_checkpoint():
    snap = cache.capture_snapshot(source(), [], pipeline=PIPELINE)
    assert snap is not None and snap.postings == {}


def test_roundtrip_uses_one_source_key_get_setex_and_no_payload_cache():
    client, snap = MemoryRedis(), snapshot()
    expected = metadata(snap)
    assert cache.publish(snap, expected, client=client, clock=lambda: 1000)
    assert cache.load(snap, client=client, clock=lambda: 1000) == expected
    assert [call[0] for call in client.calls] == ["SETEX", "GET"]
    assert client.calls[0][2] == cache.TTL_SECONDS
    key, raw = next(iter(client.values.items()))
    assert key.startswith("hirewiz:employer:source-revalidation:v1:") and snap.source_id not in key
    assert b"Build Python services" not in raw and b"private-configuration" not in raw
    assert not client.closed  # Injected test clients are owned by their caller.


@pytest.mark.parametrize("change", [
    {"last_success_at": NOW + timedelta(seconds=1)}, {"employer": "Changed"},
])
def test_sql_or_config_changes_reject_old_metadata_even_under_same_redis_key(change):
    client, old = MemoryRedis(), snapshot()
    assert cache.publish(old, metadata(old), client=client, clock=lambda: 1000)
    assert cache.load(snapshot(src=source(**change)), client=client, clock=lambda: 1000) is None


def test_changed_posting_and_late_old_writer_cannot_revalidate_new_sql():
    client, old = MemoryRedis(), snapshot()
    new = snapshot(posting(description="Changed content"))
    assert cache.publish(new, metadata(new), client=client, clock=lambda: 1000)
    assert cache.publish(old, metadata(old), client=client, clock=lambda: 1001)
    assert cache.load(new, client=client, clock=lambda: 1001) is None
    different_source = snapshot(posting(source_id="another"), src=source(id="another"))
    assert cache.load(different_source, client=client, clock=lambda: 1001) is None


@pytest.mark.parametrize("mutation", [
    lambda record: record.update(extra=True),
    lambda record: record.update(schema=True),
    lambda record: record["binding"].update(schema=True),
    lambda record: record.update(emitted_at=True),
    lambda record: record.update(emitted_at=1001),
    lambda record: record.update(expires_at=1000 + cache.TTL_SECONDS + 1),
    lambda record: record["metadata"].update(body="must-not-be-cached"),
    lambda record: record["metadata"]["details"]["100"].update(posting_sha256="0" * 64),
    lambda record: record["metadata"]["details"]["100"].update(excluded=True),
])
def test_invalid_closed_schema_binding_or_lifetime_is_a_cache_miss(mutation):
    client, snap = MemoryRedis(), snapshot()
    assert cache.publish(snap, metadata(snap), client=client, clock=lambda: 1000)
    key = next(iter(client.values))
    record = json.loads(client.values[key])
    mutation(record)
    client.values[key] = json.dumps(record).encode()
    assert cache.load(snap, client=client, clock=lambda: 1000) is None


def test_expiry_boundary_and_malformed_or_oversized_records_are_misses():
    client, snap = MemoryRedis(), snapshot()
    assert cache.publish(snap, metadata(snap), client=client, clock=lambda: 1000)
    assert cache.load(snap, client=client, clock=lambda: 1000 + cache.TTL_SECONDS - 0.001)
    assert cache.load(snap, client=client, clock=lambda: 1000 + cache.TTL_SECONDS) is None
    key = next(iter(client.values))
    for raw in (b"not-json", b"\xff", b"{" + b" " * cache.MAX_RECORD_BYTES,
                b'{"schema":1,"schema":1}'):
        client.values[key] = raw
        assert cache.load(snap, client=client, clock=lambda: 1000) is None


def test_only_explicit_excluded_markers_can_cover_absent_or_closed_sql_postings():
    client, snap = MemoryRedis(), snapshot()
    assert cache.publish(snap, metadata(snap, excluded="101"), client=client, clock=lambda: 1000)
    assert cache.load(snap, client=client, clock=lambda: 1000)["details"]["101"]["excluded"] is True
    assert not cache.publish(snap, metadata(snap, excluded="100"), client=client, clock=lambda: 1000)
    closed = snapshot(posting(is_open=False, closed_at=NOW))
    assert cache.publish(closed, metadata(closed, excluded="100"), client=client, clock=lambda: 1000)
    assert not cache.publish(closed, metadata(closed), client=client, clock=lambda: 1000)
    unstamped = metadata(snap)
    del unstamped["details"]["100"]["posting_sha256"]
    assert not cache.publish(snap, unstamped, client=client, clock=lambda: 1000)


def test_detail_subset_is_trimmed_deterministically_without_caching_payloads():
    client = MemoryRedis()
    snap = snapshot(*(posting(str(index)) for index in range(250)))
    details = {identity: {"validator": {"etag": '"' + "e" * 500 + '"', "vary": []},
                         "summary_sha256": "d" * 64, "posting_sha256": cache.posting_fingerprint(payload)}
               for identity, payload in snap.postings.items()}
    assert cache.publish(snap, {"collection": None, "details": details}, client=client, clock=lambda: 1000)
    raw = next(iter(client.values.values()))
    assert len(raw) <= cache.MAX_RECORD_BYTES
    recovered = cache.load(snap, client=client, clock=lambda: 1000)
    assert recovered is not None and 0 < len(recovered["details"]) < len(details)
    assert sorted(recovered["details"]) == sorted(details)[:len(recovered["details"])]


def test_disabled_cache_errors_and_invalid_clock_do_not_escape_or_publish(monkeypatch):
    snap = snapshot()
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert cache.load(snap) is None
    assert not cache.publish(snap, metadata(snap))

    class FailedRedis(MemoryRedis):
        def get(self, key):
            raise OSError("synthetic disconnected cache")

        def setex(self, key, seconds, raw):
            raise OSError("synthetic disconnected cache")

    failed = FailedRedis()
    assert cache.load(snap, client=failed) is None
    assert not cache.publish(snap, metadata(snap), client=failed)
    client = MemoryRedis()
    for now in (float("nan"), float("inf"), -1):
        assert not cache.publish(snap, metadata(snap), client=client, clock=lambda value=now: value)
    assert not client.calls


def test_runtime_redis_client_has_bounded_sockets_zero_retries_and_is_closed(monkeypatch):
    import redis

    client, options = MemoryRedis(), {}
    def factory(url, **kwargs):
        assert url == "redis://synthetic.invalid/0"
        options.update(kwargs)
        return client

    monkeypatch.setenv("REDIS_URL", "redis://synthetic.invalid/0")
    monkeypatch.setattr(redis.Redis, "from_url", factory)
    snap = snapshot()
    assert cache.publish(snap, metadata(snap), clock=lambda: 1000)
    assert client.closed
    assert options["socket_timeout"] == options["socket_connect_timeout"] == 0.2
    assert options["retry"].get_retries() == 0
    assert options["retry_on_timeout"] is False and options["retry_on_error"] == []
    assert options["health_check_interval"] == 0


@pytest.mark.parametrize("release", ["", "development", "latest", "a" * 39, "A" * 40])
def test_unversioned_pipeline_disables_revalidation(monkeypatch, release):
    monkeypatch.setenv("APP_RELEASE", release)
    assert cache.pipeline_revision() is None


def test_pipeline_hashes_named_actual_bytes_once_and_requires_all_files(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_RELEASE", "a" * 40)
    monkeypatch.setattr(cache, "_BACKEND", tmp_path)
    monkeypatch.setattr(cache, "_PIPELINE_FILES", ("connector.py", "normalizer.py"))
    (tmp_path / "connector.py").write_text("owned connector")
    assert cache.pipeline_revision() is None
    cache.pipeline_revision.cache_clear()
    (tmp_path / "normalizer.py").write_text("owned normalizer")
    first = cache.pipeline_revision()
    assert first is not None and len(first) == 64
    (tmp_path / "normalizer.py").write_text("changed normalization")
    assert cache.pipeline_revision() == first
    cache.pipeline_revision.cache_clear()
    assert cache.pipeline_revision() != first
