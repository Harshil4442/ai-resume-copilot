"""Read-only native Google key observation; never a global cutover certificate.

The supplied subset is bound to native key resources by lookupKey, not a JSON
assertion. Complete paginated parent listings and independent get responses must
agree. No generation, key mutation, payment configuration or database access.
"""

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import certifi

HOST = "https://apikeys.googleapis.com/v2/"
RESOURCE = re.compile(r"projects/([1-9][0-9]{0,19})/locations/global/keys/[A-Za-z0-9_-]{1,63}")
ALIAS = re.compile(r"HIREWIZ_FENCE_GOOGLE_KEY_[A-Z0-9_]{1,40}")
MAX_CREDENTIALS = 32
MAX_PROJECTS = 16
MAX_PAGES = 16
MAX_KEYS = 1024
MAX_PAYLOAD = 1024 * 1024
WINDOW_SECONDS = 30


class ObservationDenied(RuntimeError):
    """Only fixed, nonsecret reason codes cross the process boundary."""


@dataclass(frozen=True)
class GoogleCredential:
    label: str
    value: str = field(repr=False)


class Transport(Protocol):
    def get(self, path: str, params: Mapping[str, str], timeout: float) -> dict[str, Any]: ...


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ObservationDenied("native_redirect_refused")


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ObservationDenied("native_duplicate_json_key")
        value[key] = item
    return value


class NativeGoogleTransport:
    def __init__(self, access_token: str):
        if (not access_token or len(access_token) > 8192
                or any(character.isspace() for character in access_token)):
            raise ObservationDenied("observer_access_token_unavailable")
        self._token = access_token
        # Explicit CA bundle and no proxy handler: ambient proxy/CA environment
        # cannot redirect a credential lookup. urllib does not log request URLs.
        # create_default_context honors ambient SSLKEYLOGFILE and can create a
        # TLS session-secret log before it can be disabled. Build the strict
        # client context directly; load only the explicit reviewed CA bundle.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cafile=certifi.where())
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPSHandler(context=context),
        )

    def get(self, path: str, params: Mapping[str, str], timeout: float) -> dict[str, Any]:
        is_lookup = path == "keys:lookupKey" and set(params) == {"keyString"}
        is_get = RESOURCE.fullmatch(path) is not None and not params
        is_list = (re.fullmatch(r"projects/[1-9][0-9]{0,19}/locations/global/keys", path)
                   and set(params) in ({"showDeleted", "pageSize"},
                                       {"showDeleted", "pageSize", "pageToken"})
                   and params.get("showDeleted") == "true" and params.get("pageSize") == "100")
        if not (is_lookup or is_get or is_list) or not 0 < timeout <= 5:
            raise ObservationDenied("native_request_shape_refused")
        url = HOST + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(
            url, headers={"Authorization": "Bearer " + self._token,
                          "Accept": "application/json", "Cache-Control": "no-cache"}, method="GET",
        )
        try:
            with self._opener.open(request, timeout=timeout) as response:
                if response.status != 200 or response.geturl() != url:
                    raise ObservationDenied("native_response_origin_or_status_refused")
                raw = response.read(MAX_PAYLOAD + 1)
            if len(raw) > MAX_PAYLOAD:
                raise ObservationDenied("native_payload_bound_exceeded")
            if (self._token.encode() in raw
                    or is_lookup and params["keyString"].encode() in raw):
                raise ObservationDenied("native_secret_reflection_refused")
            parsed = json.loads(raw, object_pairs_hook=_json_object)
            if not isinstance(parsed, dict):
                raise ObservationDenied("native_payload_shape_refused")
            return parsed
        except ObservationDenied:
            raise
        except Exception:
            # HTTP/network/JSON errors can contain a keyString URL or bearer.
            # 404/permission errors are unavailable evidence, never revocation.
            raise ObservationDenied("native_metadata_unavailable") from None


def _timestamp(raw: Any, now: datetime) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw or len(raw) > 64:
        raise ObservationDenied("native_deletion_time_invalid")
    match = re.fullmatch(
        r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})", raw,
    )
    if match is None:
        raise ObservationDenied("native_deletion_time_invalid")
    try:
        value = datetime.fromisoformat(match[1] + match[3].replace("Z", "+00:00")).astimezone(UTC)
        nanoseconds = int((match[2] or "0").ljust(9, "0"))
        if (value, nanoseconds) > (now.replace(microsecond=0), now.microsecond * 1000):
            raise ValueError
    except ValueError:
        raise ObservationDenied("native_deletion_time_invalid") from None
    # Google Timestamp permits nanoseconds. datetime.fromisoformat alone would
    # truncate them and could collapse distinct list/get deletion observations.
    return value.strftime("%Y-%m-%dT%H:%M:%S") + f".{nanoseconds:09d}Z"


def _metadata(raw: Any, parent: str, now: datetime) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ObservationDenied("native_key_metadata_invalid")
    name, uid, etag = (raw.get(key) for key in ("name", "uid", "etag"))
    if (not isinstance(name, str) or RESOURCE.fullmatch(name) is None
            or name.rsplit("/keys/", 1)[0] != parent
            or not isinstance(uid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", uid)
            or not isinstance(etag, str) or not 1 <= len(etag) <= 256
            or any(character.isspace() for character in etag)
            or "keyString" in raw):
        raise ObservationDenied("native_key_metadata_invalid")
    return {"name": name, "uid": uid, "etag": etag,
            "delete_time": _timestamp(raw.get("deleteTime"), now)}


def inspect_google_credentials(
    credentials: Sequence[GoogleCredential], transport: Transport,
) -> dict[str, Any]:
    """Positive deletion proof for inspected keys only; not input completeness."""
    if (not 1 <= len(credentials) <= MAX_CREDENTIALS
            or len({credential.label for credential in credentials}) != len(credentials)
            or any(not isinstance(item.label, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", item.label)
                   or not isinstance(item.value, str) or not 1 <= len(item.value) <= 4096
                   or any(character.isspace() for character in item.value) for item in credentials)):
        raise ObservationDenied("credential_input_invalid")
    if any(item.value in label.label for item in credentials for label in credentials):
        raise ObservationDenied("credential_label_reflection_refused")
    started = datetime.now(UTC)
    deadline = time.monotonic() + WINDOW_SECONDS

    def get(path: str, params: Mapping[str, str]) -> dict[str, Any]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ObservationDenied("native_observation_window_exceeded")
        result = transport.get(path, params, min(5.0, remaining))
        if time.monotonic() > deadline:
            raise ObservationDenied("native_observation_window_exceeded")
        return result

    resolved: dict[str, str] = {}
    for item in credentials:
        lookup = get("keys:lookupKey", {"keyString": item.value})
        name, parent = lookup.get("name"), lookup.get("parent")
        if (not isinstance(name, str) or RESOURCE.fullmatch(name) is None
                or parent != name.rsplit("/keys/", 1)[0]):
            raise ObservationDenied("native_credential_resource_unresolved")
        resolved[item.label] = name
    parents = sorted({name.rsplit("/keys/", 1)[0] for name in resolved.values()})
    if len(parents) > MAX_PROJECTS:
        raise ObservationDenied("native_project_bound_exceeded")
    listed: dict[str, dict[str, Any]] = {}
    project_reports = []
    for parent in parents:
        token = ""
        seen_tokens: set[str] = set()
        count = 0
        for _ in range(MAX_PAGES):
            params = {"showDeleted": "true", "pageSize": "100"}
            if token:
                params["pageToken"] = token
            page = get(parent + "/keys", params)
            rows = page.get("keys", [])
            token = page.get("nextPageToken", "")
            if (not isinstance(rows, list) or len(rows) > 100 or not isinstance(token, str)
                    or len(token) > 4096 or token in seen_tokens):
                raise ObservationDenied("native_pagination_invalid")
            for row in rows:
                metadata = _metadata(row, parent, datetime.now(UTC))
                if metadata["name"] in listed:
                    raise ObservationDenied("native_duplicate_key_resource")
                listed[metadata["name"]] = metadata
                count += 1
                if len(listed) > MAX_KEYS:
                    raise ObservationDenied("native_key_bound_exceeded")
            if not token:
                break
            seen_tokens.add(token)
        else:
            raise ObservationDenied("native_page_bound_exceeded")
        project_reports.append({"parent": parent, "native_listed_key_count": count})
    observed = []
    for label, name in resolved.items():
        if name not in listed:
            raise ObservationDenied("native_bound_key_missing_from_parent_inventory")
        fresh = _metadata(get(name, {}), name.rsplit("/keys/", 1)[0], datetime.now(UTC))
        if fresh != listed[name]:
            raise ObservationDenied("native_key_changed_during_observation")
        observed.append({"label": label, "name": name, "etag": fresh["etag"],
                         "delete_time": fresh["delete_time"],
                         "native_deleted": fresh["delete_time"] is not None})
    report = {
        "observed_from_utc": started.isoformat(), "observed_through_utc": datetime.now(UTC).isoformat(),
        "scope": "supplied_google_credentials_only", "projects": project_reports, "keys": observed,
        "all_inspected_google_keys_deleted": all(row["native_deleted"] for row in observed),
        "release_permission": False,
        "unverified": ["historical_credential_inventory_completeness", "unsupported_provider_credentials",
                       "replacement_credential_isolation", "credential_reactivation_authority",
                       "inflight_provider_liabilities", "database_and_cloud_writer_retirement"],
        "generation_requests": 0, "credential_mutations": 0, "credential_values_retained": False,
    }
    if any(item.value in json.dumps(report) for item in credentials):
        raise ObservationDenied("native_secret_reflection_refused")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--google-key-env", action="append", required=True)
    args = parser.parse_args()
    try:
        if (len(args.google_key_env) > MAX_CREDENTIALS
                or len(set(args.google_key_env)) != len(args.google_key_env)
                or any(ALIAS.fullmatch(alias) is None for alias in args.google_key_env)):
            raise ObservationDenied("credential_environment_alias_invalid")
        credentials = [GoogleCredential(f"google_key_{index}", os.environ.get(alias, ""))
                       for index, alias in enumerate(args.google_key_env, 1)]
        transport = NativeGoogleTransport(os.environ.get("HIREWIZ_FENCE_OBSERVER_ACCESS_TOKEN", ""))
        result = inspect_google_credentials(credentials, transport)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["all_inspected_google_keys_deleted"] else 65
    except ObservationDenied as exc:
        print(json.dumps({"scope": "supplied_google_credentials_only", "release_permission": False,
                          "observation_available": False, "reason": str(exc)}))
        return 65


if __name__ == "__main__":
    sys.exit(main())
