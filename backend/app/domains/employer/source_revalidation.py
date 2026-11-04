"""Disposable public-feed validators bound to an exact SQL ingestion snapshot.

Redis owns no postings, freshness, admission or application authority. A missing
or rejected record only disables conditional reads; SQL remains authoritative.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

SCHEMA = 1
MAX_POSTINGS = 10_000
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
MAX_RECORD_BYTES = 64 * 1024
TTL_SECONDS = 24 * 60 * 60
INGESTION_HEADERS = MappingProxyType({
    "User-Agent": "HireWiz-EmployerConnector/1.0", "Accept": "application/json",
    "Accept-Encoding": "identity", "Cache-Control": "no-cache, max-age=0",
})
PROVIDER_FIELDS = (
    "external_id", "requisition_id", "title", "employer", "location", "country", "remote",
    "description", "canonical_url", "apply_url", "language", "preference_metadata",
    "publication_at", "source_updated_at", "content_sha256",
)
_POSTING_FIELDS = (*PROVIDER_FIELDS, "skills", "opening_key", "is_open", "closed_at")
_NULLABLE_FIELDS = {
    "requisition_id", "country", "preference_metadata", "publication_at", "source_updated_at",
    "opening_key", "closed_at",
}
_SOURCE_FIELDS = (
    "id", "employer", "platform", "board_token", "region", "allowed_hosts", "careers_url",
    "submission_enabled", "submission_grant", "credential_env", "form_parity_verified",
    "receipt_contract", "employer_key", "verification_url", "verification_note", "verified_by",
    "verified_at", "enabled", "admission_policy",
)
_PIPELINE_FILES = (
    "app/domains/employer/source_revalidation.py", "app/domains/employer/connectors.py",
    "app/domains/employer/preference_metadata.py", "app/domains/employer/search_preferences.py",
    "app/domains/employer/admissions.py", "app/domains/common.py",
    "app/services/market/skill_extractor.py", "app/services/market/skill_taxonomy.py",
    "app/services/market/security.py",
)
_BACKEND = Path(__file__).resolve().parents[3]
_HEX = re.compile(r"[0-9a-f]{64}")
_IDENTITY = re.compile(r"[A-Za-z0-9_-]{1,160}")
# HTTPX's string-header constructor is ASCII-only. Optional validators outside
# that transport subset disable reuse rather than failing the following scan.
_ETAG = re.compile(r'(?:W/)?"[\x21\x23-\x7e]*"')
_VARY = {"accept", "accept-encoding", "origin", "user-agent"}
_MISSING = object()


def _headers(headers: Mapping[str, str]) -> dict[str, str]:
    result = {}
    for key, value in headers.items():
        if type(key) is not str or type(value) is not str or "\r" in value or "\n" in value:
            raise ValueError("invalid response headers")
        name = key.lower()
        if name in result:
            raise ValueError("duplicate response header")
        result[name] = value
    return result


def _etag(value: Any) -> bool:
    if type(value) is not str or not _ETAG.fullmatch(value):
        return False
    try:
        return len(value.encode("ascii")) <= 512
    except UnicodeEncodeError:
        return False


def valid_validator(value: Any) -> bool:
    return (type(value) is dict and set(value) == {"etag", "vary"} and _etag(value["etag"])
        and type(value["vary"]) is list
        and all(type(name) is str and name in _VARY for name in value["vary"])
        and value["vary"] == sorted(set(value["vary"])))


def response_validator(headers: Mapping[str, str], prior: dict | None = None) -> dict | None:
    """Keep only validators for the fixed anonymous request representation."""
    try:
        if prior is not None and not valid_validator(prior):
            return None
        values = _headers(headers)
        directives = {part.strip().split("=", 1)[0].lower()
                      for part in values.get("cache-control", "").split(",")}
        if "no-store" in directives:
            return None
        vary = (sorted({part.strip().lower() for part in values["vary"].split(",")
                        if part.strip()}) if "vary" in values else (prior["vary"] if prior else []))
        validator = {"etag": values.get("etag", prior["etag"] if prior else None), "vary": vary}
        return validator if valid_validator(validator) else None
    except (AttributeError, TypeError, ValueError):
        return None


def matches_not_modified(prior: Any, headers: Mapping[str, str]) -> bool:
    if not valid_validator(prior):
        return False
    current = response_validator(headers, prior)
    return current is not None and current["etag"].removeprefix("W/") == prior["etag"].removeprefix("W/")


def _get(value: Any, name: str, default: Any = _MISSING) -> Any:
    result = value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)
    if result is _MISSING:
        raise ValueError("missing snapshot field")
    return result


def _date(value: Any) -> str | None:
    if value is None:
        return None
    if type(value) is str:
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        raise ValueError("invalid SQL timestamp")
    # SQLAlchemy's SQLite fixture roundtrip drops the timezone; PostgreSQL's
    # timezone-aware values and explicit offsets still canonicalize to UTC.
    value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return value.isoformat(timespec="microseconds")


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return _date(value)
    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, Mapping) and all(type(key) is str for key in value):
        return {key: _json_value(item) for key, item in value.items()}
    if type(value) in {list, tuple}:
        return [_json_value(item) for item in value]
    raise ValueError("noncanonical snapshot field")


def _canonical(value: Any) -> str:
    return json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


@lru_cache(maxsize=1)
def pipeline_revision() -> str | None:
    """Hash only the named ingestion dependencies, once per worker process."""
    release = os.getenv("APP_RELEASE", "")
    if not re.fullmatch(r"[0-9a-f]{40}", release):
        return None
    try:
        files = {}
        for name in _PIPELINE_FILES:
            path = _BACKEND / name
            if path.is_symlink() or not path.is_file():
                return None
            files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        return _sha({"schema": SCHEMA, "release": release, "files": files})
    except (OSError, ValueError):
        return None


def source_fingerprint(source: Any) -> str | None:
    try:
        config = {name: _get(source, name) for name in _SOURCE_FIELDS}
        if type(config["id"]) is not str or not 0 < len(config["id"]) <= 64:
            return None
        return _sha(config)
    except (AttributeError, TypeError, ValueError, RecursionError):
        return None


def posting_payload(row: Any) -> dict:
    payload = {name: _get(row, name, None) if name in _NULLABLE_FIELDS else _get(row, name)
               for name in _POSTING_FIELDS}
    for name in ("publication_at", "source_updated_at", "closed_at"):
        payload[name] = _date(payload[name])
    if (type(payload["external_id"]) is not str or not _IDENTITY.fullmatch(payload["external_id"])
            or type(payload["is_open"]) is not bool or type(payload["remote"]) is not bool
            or type(payload["skills"]) is not list or not all(type(skill) is str for skill in payload["skills"])
            or type(payload["content_sha256"]) is not str or not _HEX.fullmatch(payload["content_sha256"])):
        raise ValueError("invalid posting projection")
    return _json_value(payload)


def posting_fingerprint(payload: Mapping[str, Any]) -> str:
    return _sha(posting_payload(payload))


@dataclass(frozen=True)
class Snapshot:
    source_id: str
    _binding_json: str
    _postings_json: str

    @property
    def binding(self) -> dict:
        return json.loads(self._binding_json)

    @property
    def postings(self) -> dict[str, dict]:
        return json.loads(self._postings_json)


def capture_snapshot(source: Any, rows: Iterable[Any], *, pipeline: str | None = None) -> Snapshot | None:
    try:
        pipeline = pipeline_revision() if pipeline is None else pipeline
        source_sha = source_fingerprint(source)
        checkpoint = _date(_get(source, "last_success_at"))
        if (not source_sha or checkpoint is None or type(pipeline) is not str or not _HEX.fullmatch(pipeline)
                or _get(source, "enabled") is not True):
            return None
        source_id = _get(source, "id")
        postings, size = {}, 2
        for count, row in enumerate(rows, 1):
            if count > MAX_POSTINGS or _get(row, "source_id", source_id) != source_id:
                return None
            payload = posting_payload(row)
            identity = payload["external_id"]
            if identity in postings:
                return None
            size += len(_canonical(payload).encode()) + len(_canonical(identity).encode()) + 2
            if size > MAX_SNAPSHOT_BYTES:
                return None
            postings[identity] = payload
        postings_json = _canonical(postings)
        if len(postings_json.encode()) > MAX_SNAPSHOT_BYTES:
            return None
        binding = {"schema": SCHEMA, "source_config_sha256": source_sha, "last_success_at": checkpoint,
                   "posting_set_sha256": hashlib.sha256(postings_json.encode()).hexdigest(), "pipeline_sha256": pipeline}
        return Snapshot(source_id, _canonical(binding), postings_json)
    except (AttributeError, TypeError, ValueError, OverflowError, RecursionError):
        return None


def _metadata(snapshot: Snapshot, value: Any) -> dict | None:
    if (type(value) is not dict or set(value) != {"collection", "details"}
            or (value["collection"] is not None and not valid_validator(value["collection"]))
            or type(value["details"]) is not dict or len(value["details"]) > MAX_POSTINGS):
        return None
    postings = snapshot.postings
    for identity, entry in value["details"].items():
        if (type(identity) is not str or not _IDENTITY.fullmatch(identity) or type(entry) is not dict
                or not valid_validator(entry.get("validator")) or type(entry.get("summary_sha256")) is not str
                or not _HEX.fullmatch(entry["summary_sha256"])):
            return None
        if set(entry) == {"validator", "summary_sha256", "excluded"}:
            if entry["excluded"] is not True or (identity in postings and postings[identity]["is_open"] is not False):
                return None
        elif set(entry) == {"validator", "summary_sha256", "posting_sha256"}:
            if (type(entry["posting_sha256"]) is not str or identity not in postings
                    or postings[identity]["is_open"] is not True
                    or entry["posting_sha256"] != posting_fingerprint(postings[identity])):
                return None
        else:
            return None
    return value


def _key(snapshot: Snapshot) -> str:
    return "hirewiz:employer:source-revalidation:v1:" + hashlib.sha256(snapshot.source_id.encode()).hexdigest()


def _redis_client():
    url = os.getenv("REDIS_URL")
    if not url:
        return None
    from redis import Redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry
    return Redis.from_url(url, socket_connect_timeout=0.2, socket_timeout=0.2,
        retry=Retry(NoBackoff(), 0), retry_on_timeout=False, retry_on_error=[], health_check_interval=0)


def _close(client, owned: bool) -> None:
    if owned and client is not None:
        try:
            client.close()
        except Exception:
            pass


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate cache field")
        result[key] = value
    return result


def load(snapshot: Snapshot, *, client=None, clock: Callable[[], float] = time.time) -> dict | None:
    owned = client is None
    try:
        client = _redis_client() if owned else client
        if client is None:
            return None
        raw = client.get(_key(snapshot))
        if type(raw) not in {str, bytes} or len(raw.encode() if type(raw) is str else raw) > MAX_RECORD_BYTES:
            return None
        record = json.loads(raw, object_pairs_hook=_object)
        now = clock()
        if (type(record) is not dict or set(record) != {"schema", "binding", "emitted_at", "expires_at", "metadata"}
                or type(record["schema"]) is not int or record["schema"] != SCHEMA
                or _canonical(record["binding"]) != snapshot._binding_json or type(record["emitted_at"]) is not int
                or type(record["expires_at"]) is not int or not math.isfinite(now)
                or not 0 <= record["emitted_at"] <= now < record["expires_at"]
                or record["expires_at"] != record["emitted_at"] + TTL_SECONDS):
            return None
        return _metadata(snapshot, record["metadata"])
    except Exception:
        return None
    finally:
        _close(client, owned)


def publish(snapshot: Snapshot, metadata: dict, *, client=None, clock: Callable[[], float] = time.time) -> bool:
    """Caller must publish only after its SQL commit and completion fences."""
    owned = client is None
    try:
        checked_metadata = _metadata(snapshot, metadata)
        now = clock()
        if checked_metadata is None or not math.isfinite(now) or now < 0:
            return False
        emitted = int(now)
        record: dict[str, Any] = {"schema": SCHEMA, "binding": snapshot.binding, "emitted_at": emitted,
                  "expires_at": emitted + TTL_SECONDS, "metadata": {"collection": checked_metadata["collection"], "details": {}}}
        for identity, entry in sorted(checked_metadata["details"].items()):
            record["metadata"]["details"][identity] = entry
            if len(_canonical(record).encode()) > MAX_RECORD_BYTES:
                del record["metadata"]["details"][identity]
                break
        if record["metadata"] == {"collection": None, "details": {}}:
            return False
        raw = _canonical(record).encode()
        if len(raw) > MAX_RECORD_BYTES:
            return False
        client = _redis_client() if owned else client
        return client is not None and client.setex(_key(snapshot), TTL_SECONDS, raw) is True
    except Exception:
        return False
    finally:
        _close(client, owned)
