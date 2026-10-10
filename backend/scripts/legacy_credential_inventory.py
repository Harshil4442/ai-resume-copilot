"""Retained native Cloud Run credential sources; no global fencing authority.

Only explicit model/database environment sources are retirement candidates.
Values and complete native configs stay in memory. Do not serialize the returned
private objects; use sanitized_provenance(). No CLI or implicit native calls.
"""

from __future__ import annotations

import base64
import json
import re
import ssl
import time
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast

import certifi
from sqlalchemy.engine import make_url

ResourceKind = Literal["service", "revision", "job", "execution"]
SecretState = Literal["ENABLED", "DISABLED", "DESTROYED"]
CredentialKind = Literal["model", "database"]

PROJECT = "ai-resume-parser-482412"
PROJECT_NUMBER = "157225724590"
REGION = "us-central1"
PARENT = f"projects/{PROJECT}/locations/{REGION}"
EXPECTED_SERVICES = frozenset(
    {"ai-resume-parser", "hirewiz-analysis-worker", "hirewiz-employer-worker"}
)
MODEL_NAMES = frozenset(
    {"LLM_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY"}
)
DB_NAMES = frozenset({"DATABASE_URL"})
# These names are never model/database retirement candidates, regardless of suffix.
SETTLEMENT_NAMES = frozenset(
    {
        "RAZORPAY_KEY_ID",
        "RAZORPAY_KEY_SECRET",
        "RAZORPAY_WEBHOOK_SECRET",
        "RAZORPAY_WEBHOOK_SECRET_PREVIOUS",
        "JWT_SECRET",
        "ANALYSIS_TASK_TOKEN",
        "EMPLOYER_TASK_TOKEN",
    }
)
# Literal authentication secret fields accepted by the locked psycopg/libpq client.
# This is a transient redaction set, never a retirement-candidate classifier.
DB_SECRET_QUERY_NAMES = frozenset(
    {"password", "sslpassword", "scram_client_key", "scram_server_key", "oauth_client_secret"}
)
MAX_PAGES = 16
MAX_RESOURCES = 1024
MAX_TOTAL_RESOURCES = 4096
MAX_CALLS = 8192
MAX_BYTES = 4 * 1024 * 1024
MAX_VALUE_BYTES = 8192
MAX_SECONDS = 600
SEGMENT = r"[a-z][a-z0-9-]{0,62}"
BASE = rf"projects/(?:{PROJECT}|{PROJECT_NUMBER})/locations/{REGION}"
SERVICE = re.compile(rf"{BASE}/services/{SEGMENT}")
REVISION = re.compile(rf"{BASE}/services/{SEGMENT}/revisions/{SEGMENT}")
JOB = re.compile(rf"{BASE}/jobs/{SEGMENT}")
EXECUTION = re.compile(rf"{BASE}/jobs/{SEGMENT}/executions/{SEGMENT}")
SECRET = re.compile(
    rf"projects/{PROJECT_NUMBER}/secrets/[A-Za-z0-9_-]{{1,255}}/versions/[1-9][0-9]{{0,18}}"
)
UID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
MAX_ENV_NAME_CHARACTERS = 32768
CREDIBLE_ALIAS = re.compile(
    r"(?:(?:LLM|GEMINI|GOOGLE|OPENAI|GROQ|ANTHROPIC|AZURE_OPENAI)_[A-Z0-9_]*(?:KEY|TOKEN|CREDENTIALS|KEY_FILE)|(?:DATABASE|DB|POSTGRES|PG)_(?:URL|DSN|PASSWORD|PASS|USER|USERNAME|HOST|SERVICE|SERVICEFILE)|(?:LLM_API_KEY|DATABASE_URL)_FILE)"
)
# Plausibility only: unfamiliar spellings remain unresolved, never candidates.
# This does not normalize the native identity or select additional secret reads.
OPAQUE_CREDENTIAL_ALIAS = re.compile(
    r"\s*(?:(?:LLM|GEMINI|GOOGLE|OPENAI|GROQ|ANTHROPIC|AZURE[^A-Za-z0-9]+OPENAI)[^A-Za-z0-9][\s\S]*(?:KEY|TOKEN|CREDENTIALS|KEY[^A-Za-z0-9]+FILE)|(?:DATABASE|DB|POSTGRES|PG)[^A-Za-z0-9]+(?:URL|DSN|PASSWORD|PASS|USER|USERNAME|HOST|SERVICE|SERVICEFILE)|(?:LLM[^A-Za-z0-9]+API[^A-Za-z0-9]+KEY|DATABASE[^A-Za-z0-9]+URL)[^A-Za-z0-9]+FILE)\s*",
    re.IGNORECASE | re.ASCII,
)
# Privacy only; this never selects a credential candidate or secret reference.
SENSITIVE_MARKER = re.compile(
    r"KEY|SECRET|TOKEN|DATABASE|DSN|PASSWORD|PASS", re.IGNORECASE | re.ASCII
)


def _environment_name_valid(name: Any) -> bool:
    # Read retained EnvVar identity; do not apply shell-identifier grammar.
    if (
        not isinstance(name, str)
        or not 1 <= len(name) <= MAX_ENV_NAME_CHARACTERS
        or "=" in name
        or "\0" in name
    ):
        return False
    try:
        name.encode("utf-8")
    except UnicodeError:
        return False
    return True


def _unsupported_credential_alias(name: str) -> bool:
    return bool(CREDIBLE_ALIAS.fullmatch(name) or OPAQUE_CREDENTIAL_ALIAS.fullmatch(name))


class InventoryDenied(RuntimeError):
    """Fixed reason codes only; native exceptions never escape."""


class Transport(Protocol):
    def get(self, api: str, path: str, params: Mapping[str, str]) -> dict[str, Any]: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise InventoryDenied("native_redirect_refused")


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise InventoryDenied("native_duplicate_json_key")
        result[name] = value
    return result


def _request_allowed(api: str, path: str, params: Mapping[str, str]) -> bool:
    if (
        not isinstance(api, str)
        or not isinstance(path, str)
        or not isinstance(params, Mapping)
        or any(not isinstance(k, str) or not isinstance(v, str) for k, v in params.items())
    ):
        return False
    if api == "project":
        return path == f"projects/{PROJECT}" and not params
    if api == "secret":
        return not params and SECRET.fullmatch(path.removesuffix(":access")) is not None
    if api != "run":
        return False
    if any(pattern.fullmatch(path) for pattern in (SERVICE, REVISION, JOB, EXECUTION)):
        return not params
    allowed_lists = {
        f"{PARENT}/services",
        f"{PARENT}/services/-/revisions",
        f"{PARENT}/jobs",
        f"{PARENT}/jobs/-/executions",
    }
    return (
        path in allowed_lists
        and set(params) in ({"pageSize", "showDeleted"}, {"pageSize", "showDeleted", "pageToken"})
        and params.get("pageSize") == "100"
        and params.get("showDeleted") == "true"
        and (
            "pageToken" not in params
            or isinstance(params["pageToken"], str)
            and 0 < len(params["pageToken"]) <= 4096
        )
    )


class NativeTransport:
    """Authenticated GETs on three fixed Google origins; no ambient proxy/TLS logs."""

    def __init__(self, access_token: str):
        if (
            not isinstance(access_token, str)
            or not access_token
            or len(access_token) > 8192
            or any(c.isspace() for c in access_token)
        ):
            raise InventoryDenied("native_access_token_unavailable")
        self._token = access_token
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cafile=certifi.where())
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirect(),
            urllib.request.HTTPSHandler(context=context),
        )
        self._started = time.monotonic()
        self._calls = 0

    def get(self, api: str, path: str, params: Mapping[str, str]) -> dict[str, Any]:
        if not _request_allowed(api, path, params):
            raise InventoryDenied("native_request_shape_refused")
        self._calls += 1
        if self._calls > MAX_CALLS or time.monotonic() >= self._started + MAX_SECONDS:
            raise InventoryDenied("native_collection_budget_exceeded")
        origins = {
            "run": "https://run.googleapis.com/v2/",
            "secret": "https://secretmanager.googleapis.com/v1/",
            "project": "https://cloudresourcemanager.googleapis.com/v3/",
        }
        url = origins[api] + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": "Bearer " + self._token,
                "Accept": "application/json",
                "Cache-Control": "no-cache",
            },
            method="GET",
        )
        try:
            with self._opener.open(request, timeout=5) as response:
                if response.status != 200 or response.geturl() != url:
                    raise InventoryDenied("native_response_origin_or_status_refused")
                raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise InventoryDenied("native_payload_bound_exceeded")
            if self._token.encode() in raw:
                raise InventoryDenied("native_bearer_reflection_refused")
            parsed = json.loads(raw, object_pairs_hook=_object)
            if not isinstance(parsed, dict):
                raise InventoryDenied("native_payload_shape_refused")
            if time.monotonic() >= self._started + MAX_SECONDS:
                raise InventoryDenied("native_collection_budget_exceeded")
            return parsed
        except InventoryDenied:
            raise
        except Exception:
            raise InventoryDenied("native_read_unavailable") from None


@dataclass(frozen=True)
class Origin:
    resource: str
    uid: str
    kind: ResourceKind
    container_index: int
    environment_name: str = field(repr=False)
    environment_index: int | None = None


@dataclass(frozen=True)
class SecretHandle:
    reference: str
    state: SecretState
    origins: tuple[Origin, ...]
    value: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class Credential:
    kind: CredentialKind
    origins: tuple[Origin, ...]
    value: str = field(repr=False)


@dataclass(frozen=True)
class Unresolved:
    origin: Origin
    reason: str


@dataclass(frozen=True)
class RetainedInventory:
    credentials: tuple[Credential, ...] = field(repr=False)
    secret_handles: tuple[SecretHandle, ...] = field(repr=False)
    unresolved: tuple[Unresolved, ...]
    resources: tuple[tuple[str, str, str], ...]

    def sanitized_provenance(self) -> dict[str, Any]:
        # No caller labels, raw configuration, values, value hashes or endpoints.
        result = {
            "scope": "retained_native_configuration_only",
            "project": PROJECT,
            "region": REGION,
            "status": "collected_with_unresolved_sources"
            if self.unresolved
            else "retained_environment_sources_resolved",
            "resources": [
                {"kind": kind, "name": name, "uid": uid} for kind, name, uid in self.resources
            ],
            "model_credential_count": sum(c.kind == "model" for c in self.credentials),
            "database_credential_count": sum(c.kind == "database" for c in self.credentials),
            "secret_versions": [
                {
                    "reference": s.reference,
                    "state": s.state,
                    "value_status": "resolved_in_memory" if s.value is not None else "unavailable",
                }
                for s in self.secret_handles
            ],
            "unresolved": [
                {
                    "resource": u.origin.resource,
                    "uid": u.origin.uid,
                    "container_index": u.origin.container_index,
                    "environment_index": u.origin.environment_index,
                    "reason": u.reason,
                }
                for u in self.unresolved
            ],
            "remaining_scope": [
                "Other regions/projects/external consumers",
                "Pruned or expired native resources",
                "Image-embedded credentials and arbitrary process/file configuration",
                "Issuer restoration/minting/replacement-secret authority",
                "Provider revocation and database retirement",
            ],
            "global_fence": "not_provided",
            "cutover_ready": False,
        }
        raw = json.dumps(result, sort_keys=True, allow_nan=False)
        if any(
            fragment in raw
            for value in (
                *[c.value for c in self.credentials],
                *[s.value for s in self.secret_handles if s.value is not None],
            )
            for fragment in _redaction_values(value)
        ):
            raise InventoryDenied("sanitized_provenance_secret_reflection")
        return result


def _list(
    transport: Transport, path: str, key: str, pattern: re.Pattern[str]
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    token: str | None = None
    tokens: set[str] = set()
    for _ in range(MAX_PAGES):
        params = {"pageSize": "100", "showDeleted": "true"}
        if token is not None:
            params["pageToken"] = token
        raw = transport.get("run", path, params)
        if (
            set(raw) - {key, "nextPageToken", "unreachable"}
            or raw.get("unreachable")
            or "unreachable" in raw
            and not isinstance(raw["unreachable"], list)
        ):
            raise InventoryDenied("native_list_partial_or_unsupported")
        rows = raw.get(key, [])
        if not isinstance(rows, list) or len(rows) > 100:
            raise InventoryDenied("native_list_shape_or_page_bound")
        for row in rows:
            name, _ = _identity(row, pattern)
            if name in result:
                raise InventoryDenied("native_duplicate_resource")
            result[name] = row
        if len(result) > MAX_RESOURCES:
            raise InventoryDenied("native_resource_bound")
        token = raw.get("nextPageToken")
        if token is None or token == "":
            return result
        if not isinstance(token, str) or len(token) > 4096 or token in tokens:
            raise InventoryDenied("native_pagination_token_invalid_or_repeated")
        tokens.add(token)
    raise InventoryDenied("native_pagination_bound")


def _identity(raw: Any, pattern: re.Pattern[str]) -> tuple[str, str]:
    if not isinstance(raw, dict):
        raise InventoryDenied("native_resource_shape_invalid")
    name, uid = raw.get("name"), raw.get("uid")
    if (
        not isinstance(name, str)
        or pattern.fullmatch(name) is None
        or not isinstance(uid, str)
        or UID.fullmatch(uid) is None
    ):
        raise InventoryDenied("native_resource_identity_invalid")
    generation, etag = raw.get("generation"), raw.get("etag")
    if (
        not isinstance(generation, str)
        or not re.fullmatch(r"[1-9][0-9]{0,18}", generation)
        or int(generation) > 9223372036854775807
        or not isinstance(etag, str)
        or not 1 <= len(etag) <= 256
        or any(c.isspace() for c in etag)
    ):
        raise InventoryDenied("native_resource_generation_or_etag_invalid")
    return name, uid


def _config(raw: dict[str, Any], kind: str) -> dict[str, Any]:
    if kind == "service":
        config = raw.get("template")
    elif kind == "job":
        outer = raw.get("template")
        config = outer.get("template") if isinstance(outer, dict) else None
    elif kind == "execution":
        config = raw.get("template")
    else:
        config = raw
    if (
        not isinstance(config, dict)
        or not isinstance(config.get("containers"), list)
        or not 1 <= len(config["containers"]) <= 16
    ):
        raise InventoryDenied("native_container_configuration_missing_or_bound")
    return config


def _binding(raw: dict[str, Any], kind: str) -> tuple[Any, ...]:
    # Compare all containers/env/commands/volumes verbatim in memory; never hash them.
    return (
        raw.get("name"),
        raw.get("uid"),
        raw.get("generation"),
        raw.get("etag"),
        raw.get("deleteTime"),
        raw.get("expireTime"),
        raw.get("service"),
        raw.get("job"),
        _config(raw, kind),
    )


def _secret_reference(raw: Any) -> str:
    if not isinstance(raw, dict) or set(raw) != {"secret", "version"}:
        raise InventoryDenied("native_secret_reference_shape_unsupported")
    secret, version = raw.get("secret"), raw.get("version")
    if (
        not isinstance(secret, str)
        or not isinstance(version, str)
        or not re.fullmatch(r"[1-9][0-9]{0,18}", version)
    ):
        raise InventoryDenied("native_secret_version_unpinned")
    if "/" not in secret:
        secret = f"projects/{PROJECT_NUMBER}/secrets/{secret}"
    else:
        secret = secret.replace(f"projects/{PROJECT}/", f"projects/{PROJECT_NUMBER}/", 1)
    reference = f"{secret}/versions/{version}"
    if SECRET.fullmatch(reference) is None:
        raise InventoryDenied("native_secret_reference_foreign_or_invalid")
    return reference


def _redaction_values(value: str) -> set[str]:
    """Transient full values and decoded URL authentication secrets; never output."""
    result = {part for part in (value, value.strip()) if part}
    if "://" not in value:
        return result
    try:
        url = make_url(value.strip())
    except Exception:
        raise InventoryDenied("credential_uri_redaction_parse_unavailable") from None
    if url.password:
        result.update(part for part in (url.password, url.password.strip()) if part)
    for name, item in url.query.items():
        if name.lower() not in DB_SECRET_QUERY_NAMES:
            continue
        for fragment in (item,) if isinstance(item, str) else item:
            result.update(part for part in (fragment, fragment.strip()) if part)
    return result


def _value_bytes(value: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeError:
        raise InventoryDenied("native_environment_source_shape_unsupported") from None


def _crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = crc >> 1 ^ (0x82F63B78 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


def _secret_value(transport: Transport, reference: str) -> tuple[SecretState, str | None]:
    metadata = transport.get("secret", reference, {})
    if (
        metadata.get("name") != reference
        or not isinstance(metadata.get("state"), str)
        or metadata.get("state") not in {"ENABLED", "DISABLED", "DESTROYED"}
        or not isinstance(metadata.get("etag"), str)
        or not metadata["etag"]
    ):
        raise InventoryDenied("native_secret_metadata_identity_or_state_invalid")
    state = cast(SecretState, metadata["state"])
    if state != "ENABLED":
        return state, None
    raw = transport.get("secret", reference + ":access", {})
    payload = raw.get("payload")
    if (
        raw.get("name") != reference
        or not isinstance(payload, dict)
        or set(payload) != {"data", "dataCrc32c"}
    ):
        raise InventoryDenied("native_secret_payload_shape_invalid")
    data, checksum = payload.get("data"), payload.get("dataCrc32c")
    if (
        not isinstance(data, str)
        or len(data) > 2 * MAX_VALUE_BYTES
        or not isinstance(checksum, str)
        or not re.fullmatch(r"[0-9]{1,10}", checksum)
    ):
        raise InventoryDenied("native_secret_payload_bound_or_checksum_invalid")
    try:
        decoded = base64.b64decode(data, validate=True)
        if len(decoded) > MAX_VALUE_BYTES or _crc32c(decoded) != int(checksum):
            raise ValueError
        value = decoded.decode("utf-8")
    except (ValueError, UnicodeError):
        raise InventoryDenied("native_secret_payload_integrity_invalid") from None
    if transport.get("secret", reference, {}) != metadata:
        raise InventoryDenied("native_secret_metadata_changed")
    return state, value


def collect_retained_credentials(transport: Transport) -> RetainedInventory:
    """Collect known native sources; no caller alias set or completeness attestation."""
    started = time.monotonic()
    project = transport.get("project", f"projects/{PROJECT}", {})
    if (
        project.get("projectId") != PROJECT
        or project.get("name") != f"projects/{PROJECT_NUMBER}"
        or project.get("state") != "ACTIVE"
    ):
        raise InventoryDenied("native_project_binding_invalid")
    collections: tuple[tuple[ResourceKind, str, str, re.Pattern[str]], ...] = (
        ("service", "services", f"{PARENT}/services", SERVICE),
        ("revision", "revisions", f"{PARENT}/services/-/revisions", REVISION),
        ("job", "jobs", f"{PARENT}/jobs", JOB),
        ("execution", "executions", f"{PARENT}/jobs/-/executions", EXECUTION),
    )
    lists = {kind: _list(transport, path, key, pattern) for kind, key, path, pattern in collections}
    if sum(len(rows) for rows in lists.values()) > MAX_TOTAL_RESOURCES:
        raise InventoryDenied("native_total_resource_bound")
    if not EXPECTED_SERVICES.issubset({name.rsplit("/", 1)[1] for name in lists["service"]}):
        raise InventoryDenied("native_expected_services_missing")
    native_uids = [row["uid"] for rows in lists.values() for row in rows.values()]
    if len(set(native_uids)) != len(native_uids):
        raise InventoryDenied("native_duplicate_resource_uid")
    values: dict[tuple[CredentialKind, str], list[Origin]] = {}
    secret_cache: dict[str, tuple[SecretState, str | None]] = {}
    secret_origins: dict[str, list[Origin]] = {}
    unresolved: list[Unresolved] = []
    identities: list[tuple[str, str, str]] = []
    sensitive: set[str] = set()
    for kind, _, _, pattern in collections:
        for name, listed in lists[kind].items():
            native = transport.get("run", name, {})
            actual_name, uid = _identity(native, pattern)
            if actual_name != name or _binding(native, kind) != _binding(listed, kind):
                raise InventoryDenied("native_list_get_configuration_mismatch")
            parent_kind, parent_field = (
                ("service", "service") if kind == "revision" else ("job", "job")
            )
            if kind in {"revision", "execution"}:
                parent_name = native.get(parent_field)
                derived_parent = name.rsplit(
                    "/" + ("revisions" if kind == "revision" else "executions") + "/", 1
                )[0]
                if (
                    not isinstance(parent_name, str)
                    or parent_name not in (derived_parent, derived_parent.rsplit("/", 1)[1])
                    or derived_parent not in lists[cast(ResourceKind, parent_kind)]
                ):
                    raise InventoryDenied("native_parent_binding_unavailable")
            identities.append((kind, name, uid))
            config = _config(native, kind)
            for index, container in enumerate(config["containers"]):
                if (
                    not isinstance(container, dict)
                    or not isinstance(container.get("image"), str)
                    or not container["image"]
                    or len(container["image"]) > 1024
                ):
                    raise InventoryDenied("native_container_image_missing_or_bound")
                env = container.get("env", [])
                if not isinstance(env, list) or len(env) > 1000:
                    raise InventoryDenied("native_environment_bound_or_shape")
                names: set[str] = set()
                for environment_index, entry in enumerate(env):
                    if (
                        not isinstance(entry, dict)
                        or not _environment_name_valid(entry.get("name"))
                        or entry["name"] in names
                    ):
                        raise InventoryDenied("native_environment_identity_or_duplicate")
                    key = entry["name"]
                    names.add(key)
                    origin = Origin(name, uid, kind, index, key, environment_index)
                    if key not in MODEL_NAMES | DB_NAMES | SETTLEMENT_NAMES:
                        # Unknown raw names are private too; deny indirect reflection
                        # into otherwise-public resource/version/UID provenance.
                        sensitive.add(key)
                    if (
                        set(entry) == {"name", "value"}
                        and isinstance(entry["value"], str)
                        and _value_bytes(entry["value"]) <= MAX_VALUE_BYTES
                    ):
                        inline_value: str = entry["value"]
                        value: str | None = inline_value
                        reference = None
                        if (
                            key in MODEL_NAMES | DB_NAMES | SETTLEMENT_NAMES
                            or _unsupported_credential_alias(key)
                            or SENSITIVE_MARKER.search(key)
                            or not key.isascii()
                        ):
                            sensitive.update(_redaction_values(inline_value))
                    elif (
                        set(entry) == {"name", "valueSource"}
                        and isinstance(entry["valueSource"], dict)
                        and set(entry["valueSource"]) == {"secretKeyRef"}
                    ):
                        value = None
                        reference = entry["valueSource"]["secretKeyRef"]
                    elif set(entry) == {"name"}:
                        value, reference = "", None
                    else:
                        raise InventoryDenied("native_environment_source_shape_unsupported")
                    if key in SETTLEMENT_NAMES:
                        continue
                    if key not in MODEL_NAMES | DB_NAMES:
                        if _unsupported_credential_alias(key):
                            unresolved.append(
                                Unresolved(
                                    origin, "credential_alias_not_supported_by_source_contract"
                                )
                            )
                        continue
                    if reference is not None:
                        try:
                            pinned = _secret_reference(reference)
                        except InventoryDenied as error:
                            unresolved.append(Unresolved(origin, str(error)))
                            continue
                        secret_origins.setdefault(pinned, []).append(origin)
                        if len(secret_origins) > 128:
                            raise InventoryDenied("native_secret_reference_bound")
                        if pinned not in secret_cache:
                            secret_cache[pinned] = _secret_value(transport, pinned)
                        state, value = secret_cache[pinned]
                        if value is None:
                            unresolved.append(
                                Unresolved(
                                    origin, "referenced_secret_value_unavailable_" + state.lower()
                                )
                            )
                            continue
                    assert value is not None
                    if not value.strip():
                        continue
                    sensitive.update(_redaction_values(value))
                    category: CredentialKind = "model" if key in MODEL_NAMES else "database"
                    normalized = value.strip()
                    if category == "model" and (
                        len(normalized) > 4096
                        or any(c.isspace() or ord(c) < 32 for c in normalized)
                    ):
                        unresolved.append(
                            Unresolved(origin, "model_credential_value_shape_unsupported")
                        )
                        continue
                    if category == "database":
                        try:
                            url = make_url(normalized)
                            if url.drivername not in {
                                "postgres",
                                "postgresql",
                                "postgresql+psycopg",
                            } or not all((url.username, url.password, url.host, url.database)):
                                raise ValueError
                        except Exception:
                            unresolved.append(
                                Unresolved(origin, "database_credential_value_shape_unsupported")
                            )
                            continue
                    values.setdefault((category, normalized), []).append(origin)
                    if len(values) > 128:
                        raise InventoryDenied("native_credential_value_bound")
                if container.get("volumeMounts") or config.get("volumes"):
                    unresolved.append(
                        Unresolved(
                            Origin(name, uid, kind, index, ""),
                            "mounted_configuration_not_inspected",
                        )
                    )
    # Enumerate again, preserving full config comparison in memory, to refuse
    # additions/removals/replacements during this bounded non-atomic collection.
    for kind, key, path, pattern in collections:
        fresh = _list(transport, path, key, pattern)
        if set(fresh) != set(lists[kind]) or any(
            _binding(fresh[name], kind) != _binding(lists[kind][name], kind) for name in fresh
        ):
            raise InventoryDenied("native_collection_changed")
    if time.monotonic() >= started + MAX_SECONDS:
        raise InventoryDenied("native_collection_budget_exceeded")
    handles = tuple(
        SecretHandle(reference, state, tuple(secret_origins[reference]), value)
        for reference, (state, value) in secret_cache.items()
    )
    credentials = tuple(
        Credential(category, tuple(origins), value) for (category, value), origins in values.items()
    )
    result = RetainedInventory(credentials, handles, tuple(unresolved), tuple(identities))
    sanitized = result.sanitized_provenance()
    serialized = json.dumps(sanitized, sort_keys=True, allow_nan=False)
    # Excluded merchant/JWT/task values exist only in the transient native input;
    # redact against them here, never return them as inventory values or handles.
    if any(value and value in serialized for value in sensitive):
        raise InventoryDenied("sanitized_provenance_secret_reflection")
    return result
