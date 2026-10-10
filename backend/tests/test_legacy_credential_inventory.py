"""Native-shaped source identities and secrecy, without live credentials/API calls."""

from __future__ import annotations

import base64
import copy
import json
import math
import ssl
from uuid import uuid4

import pytest

from scripts import legacy_credential_inventory as inv

MODEL = "synthetic-model-key-long-enough"
DATABASE = "postgresql://synthetic:private-test-password@database.invalid/neondb"
PAYMENT = "synthetic-merchant-secret-do-not-retire"
REFERENCE = f"projects/{inv.PROJECT_NUMBER}/secrets/model/versions/7"


def env(name, value):
    return {"name": name, "value": value}


def source(name, kind, values):
    config = {
        "containers": [{"image": "registry.invalid/reviewed@sha256:" + "a" * 64, "env": values}]
    }
    result = {"name": name, "uid": str(uuid4()), "generation": "1", "etag": '"etag"'}
    if kind == "service":
        result["template"] = config
    elif kind == "job":
        result["template"] = {"template": config}
    elif kind == "execution":
        result["template"] = config
        result["job"] = name.rsplit("/executions/", 1)[0]
    else:
        result.update(config)
        result["service"] = name.rsplit("/revisions/", 1)[0]
    return result


class NativeFixture:
    def __init__(self):
        services = [
            source(
                f"{inv.PARENT}/services/{name}",
                "service",
                [
                    env("DATABASE_URL", DATABASE),
                    env("LLM_API_KEY", MODEL),
                    env("RAZORPAY_KEY_SECRET", PAYMENT),
                ],
            )
            for name in sorted(inv.EXPECTED_SERVICES)
        ]
        revisions = [
            source(
                s["name"] + "/revisions/retained-" + str(i), "revision", [env("LLM_API_KEY", MODEL)]
            )
            for i, s in enumerate(services)
        ]
        job = source(inv.PARENT + "/jobs/migration", "job", [env("DATABASE_URL", DATABASE)])
        execution = source(
            job["name"] + "/executions/previous-run",
            "execution",
            [env("LLM_API_KEY", "synthetic-execution-override-value")],
        )
        self.rows = {
            "services": services,
            "revisions": revisions,
            "jobs": [job],
            "executions": [execution],
        }
        self.gets = {row["name"]: row for rows in self.rows.values() for row in rows}
        self.calls = []
        self.overrides = {}
        self.secrets = {}
        self.second_list = {}
        self.list_counts = {}
        self.pages = {}

    def get(self, api, path, params):
        self.calls.append((api, path, dict(params)))
        assert inv._request_allowed(api, path, params)
        if (api, path) in self.overrides:
            value = self.overrides[(api, path)]
            if isinstance(value, Exception):
                raise value
            return copy.deepcopy(value)
        if api == "project":
            return {
                "projectId": inv.PROJECT,
                "name": "projects/" + inv.PROJECT_NUMBER,
                "state": "ACTIVE",
            }
        if api == "secret":
            return copy.deepcopy(self.secrets[path])
        if params:
            key = path.rsplit("/", 1)[1]
            self.list_counts[key] = self.list_counts.get(key, 0) + 1
            if key in self.pages:
                return copy.deepcopy(self.pages[key].get(params.get("pageToken", ""), {}))
            rows = (
                self.second_list.get(key, self.rows[key])
                if self.list_counts[key] > 1
                else self.rows[key]
            )
            return copy.deepcopy({key: rows})
        return copy.deepcopy(self.gets[path])

    def pin(self, state="ENABLED", value=MODEL):
        selector = {
            "name": "LLM_API_KEY",
            "valueSource": {"secretKeyRef": {"secret": "model", "version": "7"}},
        }
        for row in self.rows["revisions"]:
            row["containers"][0]["env"] = [copy.deepcopy(selector)]
        raw = value.encode()
        self.secrets[REFERENCE] = {"name": REFERENCE, "state": state, "etag": '"secret-etag"'}
        self.secrets[REFERENCE + ":access"] = {
            "name": REFERENCE,
            "payload": {
                "data": base64.b64encode(raw).decode(),
                "dataCrc32c": str(inv._crc32c(raw)),
            },
        }


def test_native_retained_sources_deduplicate_values_and_bind_execution_override():
    native = NativeFixture()
    result = inv.collect_retained_credentials(native)
    model_values = {c.value for c in result.credentials if c.kind == "model"}
    assert model_values == {MODEL, "synthetic-execution-override-value"}
    assert len([c for c in result.credentials if c.kind == "database"]) == 1
    assert len(next(c for c in result.credentials if c.value == MODEL).origins) == 6
    assert len(result.resources) == 8
    summary = result.sanitized_provenance()
    assert summary["status"] == "retained_environment_sources_resolved"
    assert summary["global_fence"] == "not_provided" and summary["cutover_ready"] is False
    for value in (MODEL, DATABASE, PAYMENT):
        assert value not in repr(result) and value not in json.dumps(summary)
    assert not any(
        path.startswith("projects/") and api == "secret" for api, path, _ in native.calls
    )
    assert all(api in {"run", "project", "secret"} for api, _, _ in native.calls)


def test_explicit_numeric_secret_payload_crc_and_identity_join_without_hashes():
    native = NativeFixture()
    native.pin()
    result = inv.collect_retained_credentials(native)
    assert len(result.secret_handles) == 1
    assert result.secret_handles[0].reference == REFERENCE
    assert result.secret_handles[0].value == MODEL
    assert len(result.secret_handles[0].origins) == 3
    assert len([call for call in native.calls if call[1] == REFERENCE + ":access"]) == 1
    assert MODEL not in repr(result.secret_handles[0])
    assert "sha256" not in json.dumps(result.sanitized_provenance())


@pytest.mark.parametrize("state", ["DISABLED", "DESTROYED"])
def test_unavailable_payload_is_explicitly_unresolved_not_provider_revocation(state):
    native = NativeFixture()
    native.pin(state)
    result = inv.collect_retained_credentials(native)
    assert result.secret_handles[0].state == state and result.secret_handles[0].value is None
    assert len(result.unresolved) == 3
    assert result.sanitized_provenance()["status"] == "collected_with_unresolved_sources"
    assert not any(path.endswith(":access") for _, path, _ in native.calls)


@pytest.mark.parametrize("version", ["latest", "production", "0", "-1", 7, None])
def test_unpinned_secret_source_never_accesses_or_claims_value_resolution(version):
    native = NativeFixture()
    native.pin()
    native.rows["revisions"][0]["containers"][0]["env"][0]["valueSource"]["secretKeyRef"][
        "version"
    ] = version
    result = inv.collect_retained_credentials(native)
    assert any(u.reason == "native_secret_version_unpinned" for u in result.unresolved)
    assert result.sanitized_provenance()["status"] == "collected_with_unresolved_sources"


@pytest.mark.parametrize(
    "field,value",
    [
        ("uid", str(uuid4())),
        ("generation", "2"),
        ("etag", '"changed"'),
        (
            "containers",
            [{"image": "different", "env": [env("LLM_API_KEY", "synthetic-substituted-value")]}],
        ),
    ],
)
def test_list_get_revision_identity_and_every_container_config_must_match(field, value):
    native = NativeFixture()
    row = native.rows["revisions"][0]
    replacement = copy.deepcopy(row)
    replacement[field] = value
    native.overrides[("run", row["name"])] = replacement
    with pytest.raises(inv.InventoryDenied, match="native_list_get_configuration_mismatch"):
        inv.collect_retained_credentials(native)


def test_list_get_job_and_execution_configs_must_match_historical_overrides():
    native = NativeFixture()
    row = native.rows["executions"][0]
    replacement = copy.deepcopy(row)
    replacement["template"]["containers"][0]["env"] = [env("LLM_API_KEY", MODEL)]
    native.overrides[("run", row["name"])] = replacement
    with pytest.raises(inv.InventoryDenied, match="native_list_get_configuration_mismatch"):
        inv.collect_retained_credentials(native)


@pytest.mark.parametrize(
    "field,value",
    [("projectId", "different-project"), ("name", "projects/123"), ("state", "DELETE_REQUESTED")],
)
def test_native_project_id_and_numeric_binding_is_required(field, value):
    native = NativeFixture()
    native.overrides[("project", "projects/" + inv.PROJECT)] = {
        "projectId": inv.PROJECT,
        "name": "projects/" + inv.PROJECT_NUMBER,
        "state": "ACTIVE",
        field: value,
    }
    with pytest.raises(inv.InventoryDenied, match="native_project_binding_invalid"):
        inv.collect_retained_credentials(native)


@pytest.mark.parametrize("collection", ["services", "revisions", "jobs", "executions"])
def test_complete_pagination_includes_second_page_sources(collection):
    native = NativeFixture()
    rows = native.rows[collection]
    native.pages[collection] = {
        "": {collection: rows[:1], "nextPageToken": "second-page"},
        "second-page": {collection: rows[1:]},
    }
    result = inv.collect_retained_credentials(native)
    assert len(result.resources) == 8
    calls = [
        params for _, path, params in native.calls if path.endswith("/" + collection) and params
    ]
    assert any(params.get("pageToken") == "second-page" for params in calls)
    assert all(params["showDeleted"] == "true" for params in calls)


@pytest.mark.parametrize(
    "mutation", ["repeat", "duplicate", "partial", "bad-token", "bad-uid", "foreign"]
)
def test_partial_repeated_or_foreign_native_inventory_refuses(mutation):
    native = NativeFixture()
    rows = native.rows["services"]
    first = {"services": copy.deepcopy(rows)}
    if mutation == "repeat":
        first["nextPageToken"] = "same"
        native.pages["services"] = {"": first, "same": {"services": [], "nextPageToken": "same"}}
    elif mutation == "duplicate":
        first["services"].append(copy.deepcopy(rows[0]))
    elif mutation == "partial":
        first["unreachable"] = ["region"]
    elif mutation == "bad-token":
        first["nextPageToken"] = True
    elif mutation == "bad-uid":
        first["services"][0]["uid"] = "caller-attestation"
    else:
        first["services"][0]["name"] = first["services"][0]["name"].replace(
            inv.PROJECT, "foreign-project"
        )
    if mutation != "repeat":
        native.pages["services"] = {"": first}
    with pytest.raises(inv.InventoryDenied):
        inv.collect_retained_credentials(native)


def test_new_resource_during_collection_refuses_without_a_snapshot_claim():
    native = NativeFixture()
    native.second_list["executions"] = [
        *native.rows["executions"],
        source(
            inv.PARENT + "/jobs/migration/executions/new-run",
            "execution",
            [env("LLM_API_KEY", "synthetic-new-value")],
        ),
    ]
    with pytest.raises(inv.InventoryDenied, match="native_collection_changed"):
        inv.collect_retained_credentials(native)


@pytest.mark.parametrize(
    "key", ["LLM_KEY_FILE", "DATABASE_URL_FILE", "PG_PASSWORD", "ANTHROPIC_API_KEY"]
)
def test_unknown_credible_credential_sources_remain_explicitly_unsupported(key):
    native = NativeFixture()
    native.rows["revisions"][0]["containers"][0]["env"].append(
        env(key, "synthetic-unknown-source-value")
    )
    result = inv.collect_retained_credentials(native)
    assert any(u.origin.environment_name == key for u in result.unresolved)
    assert all(c.value != "synthetic-unknown-source-value" for c in result.credentials)
    assert result.sanitized_provenance()["status"] == "collected_with_unresolved_sources"


def test_all_containers_covered_and_mounted_files_not_silently_treated_complete():
    native = NativeFixture()
    config = native.rows["revisions"][0]
    config["containers"].append(
        {
            "image": "registry.invalid/sidecar",
            "env": [env("OPENAI_API_KEY", "synthetic-sidecar-value")],
            "volumeMounts": [{"name": "secret-file", "mountPath": "/credentials"}],
        }
    )
    config["volumes"] = [
        {
            "name": "secret-file",
            "secret": {"secret": "unknown", "items": [{"version": "latest", "path": "key"}]},
        }
    ]
    result = inv.collect_retained_credentials(native)
    assert any(c.value == "synthetic-sidecar-value" for c in result.credentials)
    assert any(u.reason == "mounted_configuration_not_inspected" for u in result.unresolved)


@pytest.mark.parametrize(
    "entry",
    [
        {"name": "LLM_API_KEY", "value": MODEL, "valueSource": {}},
        {"name": "LLM_API_KEY", "valueFrom": {}},
        {"name": "LLM_API_KEY", "value": True},
    ],
)
def test_unsupported_credential_environment_union_refuses(entry):
    native = NativeFixture()
    native.rows["revisions"][0]["containers"][0]["env"] = [entry]
    with pytest.raises(inv.InventoryDenied, match="native_environment_source_shape_unsupported"):
        inv.collect_retained_credentials(native)


def test_duplicate_environment_names_refuse_instead_of_collapsing_different_keys():
    native = NativeFixture()
    native.rows["revisions"][0]["containers"][0]["env"].append(
        env("LLM_API_KEY", "different-synthetic-value")
    )
    with pytest.raises(inv.InventoryDenied, match="native_environment_identity_or_duplicate"):
        inv.collect_retained_credentials(native)


@pytest.mark.parametrize("mutation", ["name", "checksum", "base64", "state", "etag"])
def test_secret_payload_and_metadata_identity_integrity_is_required(mutation):
    native = NativeFixture()
    native.pin()
    if mutation == "name":
        native.secrets[REFERENCE + ":access"]["name"] = REFERENCE.replace("/7", "/8")
    elif mutation == "checksum":
        native.secrets[REFERENCE + ":access"]["payload"]["dataCrc32c"] = "1"
    elif mutation == "base64":
        native.secrets[REFERENCE + ":access"]["payload"]["data"] = "%%%"
    elif mutation == "state":
        native.secrets[REFERENCE]["state"] = "UNKNOWN"
    else:
        del native.secrets[REFERENCE]["etag"]
    with pytest.raises(inv.InventoryDenied):
        inv.collect_retained_credentials(native)


def test_metadata_changes_after_enabled_payload_access_refuse():
    native = NativeFixture()
    native.pin()
    original = native.get
    count = 0

    def get(api, path, params):
        nonlocal count
        result = original(api, path, params)
        if api == "secret" and path == REFERENCE:
            count += 1
            if count == 2:
                result["state"] = "DISABLED"
        return result

    native.get = get
    with pytest.raises(inv.InventoryDenied, match="native_secret_metadata_changed"):
        inv.collect_retained_credentials(native)


def test_settlement_secret_reference_is_never_accessed_or_retired():
    native = NativeFixture()
    native.rows["revisions"][0]["containers"][0]["env"].append(
        {
            "name": "RAZORPAY_WEBHOOK_SECRET",
            "valueSource": {"secretKeyRef": {"secret": "merchant", "version": "latest"}},
        }
    )
    result = inv.collect_retained_credentials(native)
    assert not result.unresolved
    assert not any(api == "secret" for api, _, _ in native.calls)
    assert not any(
        o.environment_name.startswith("RAZORPAY") for c in result.credentials for o in c.origins
    )


def test_get_failure_is_not_empty_inventory_evidence():
    native = NativeFixture()
    native.overrides[("run", native.rows["revisions"][0]["name"])] = inv.InventoryDenied(
        "native_read_unavailable"
    )
    with pytest.raises(inv.InventoryDenied, match="native_read_unavailable"):
        inv.collect_retained_credentials(native)


def test_runtime_global_fence_source_is_unchanged_and_no_fence_type_exported():
    assert not hasattr(inv, "Fence")
    assert not hasattr(inv, "run_release")


def test_crc32c_matches_published_castagnoli_check_vector():
    assert inv._crc32c(b"123456789") == 0xE3069283


@pytest.mark.parametrize(
    "api,path,params",
    [
        ("run", inv.PARENT + "/services/ai-resume-parser:delete", {}),
        ("secret", REFERENCE.replace("/7", "/latest"), {}),
        ("secret", REFERENCE + ":destroy", {}),
        ("project", "projects/foreign", {}),
        ("apikeys", "keys:getKeyString", {}),
    ],
)
def test_transport_cannot_request_mutation_unpinned_or_key_string(api, path, params):
    native = inv.NativeTransport("synthetic-native-bearer")
    with pytest.raises(inv.InventoryDenied, match="native_request_shape_refused"):
        native.get(api, path, params)


def test_native_tls_has_no_keylog_ambient_proxy_or_redirect(monkeypatch, tmp_path):
    target = tmp_path / "must-not-create-keylog"
    monkeypatch.setenv("SSLKEYLOGFILE", str(target))
    monkeypatch.setenv("HTTPS_PROXY", "https://evil.invalid")
    native = inv.NativeTransport("synthetic-native-bearer")
    handler = next(
        h for h in native._opener.handlers if isinstance(h, inv.urllib.request.HTTPSHandler)
    )
    context = handler._context
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert context.keylog_filename is None and not target.exists()
    assert not any(
        isinstance(h, inv.urllib.request.ProxyHandler) and h.proxies
        for h in native._opener.handlers
    )
    with pytest.raises(inv.InventoryDenied, match="native_redirect_refused"):
        inv._NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.invalid")


class Response:
    def __init__(self, url, body, status=200):
        self.url, self.body, self.status = url, body, status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def geturl(self):
        return self.url

    def read(self, count):
        return self.body[:count]


@pytest.mark.parametrize(
    "mode", ["redirect", "status", "duplicate", "oversize", "bearer", "exception", "list"]
)
def test_native_transport_errors_never_echo_credentials_or_accept_unsafe_responses(mode):
    token = "synthetic-native-bearer"
    native = inv.NativeTransport(token)

    def open(request, timeout):
        assert request.get_method() == "GET" and timeout == 5
        assert request.get_header("Authorization") == "Bearer " + token
        if mode == "exception":
            raise ValueError("sensitive=" + token)
        body = b"{}"
        url = request.full_url
        status = 200
        if mode == "redirect":
            url = "https://evil.invalid"
        elif mode == "status":
            status = 403
        elif mode == "duplicate":
            body = b'{"name":1,"name":2}'
        elif mode == "oversize":
            body = b"x" * (inv.MAX_BYTES + 1)
        elif mode == "bearer":
            body = json.dumps({"value": token}).encode()
        elif mode == "list":
            body = b"[]"
        return Response(url, body, status)

    native._opener.open = open
    with pytest.raises(inv.InventoryDenied) as error:
        native.get("project", "projects/" + inv.PROJECT, {})
    assert token not in str(error.value) and error.value.__cause__ is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("generation", None),
        ("generation", 1),
        ("generation", "0"),
        ("generation", "9223372036854775808"),
        ("etag", None),
        ("etag", ""),
        ("etag", "secret\nvalue"),
    ],
)
def test_native_generation_and_etag_must_be_present_strict_metadata(field, value):
    native = NativeFixture()
    native.rows["revisions"][0][field] = value
    with pytest.raises(inv.InventoryDenied, match="native_resource_generation_or_etag_invalid"):
        inv.collect_retained_credentials(native)


def test_native_uid_reuse_across_resource_names_refuses():
    native = NativeFixture()
    native.rows["revisions"][0]["uid"] = native.rows["revisions"][1]["uid"]
    with pytest.raises(inv.InventoryDenied, match="native_duplicate_resource_uid"):
        inv.collect_retained_credentials(native)


@pytest.mark.parametrize(
    "key,value",
    [
        ("DATABASE_URL", "sqlite:///synthetic.db"),
        ("DATABASE_URL", "postgresql://database.invalid/no-password"),
        ("LLM_API_KEY", '{"key":"synthetic value"}'),
        ("LLM_API_KEY", "synthetic\nmultiline"),
    ],
)
def test_unsupported_credential_value_shape_is_not_silently_a_resolved_handle(key, value):
    native = NativeFixture()
    native.rows["revisions"][0]["containers"][0]["env"] = [env(key, value)]
    result = inv.collect_retained_credentials(native)
    assert any(u.reason.endswith("credential_value_shape_unsupported") for u in result.unresolved)
    assert result.sanitized_provenance()["status"] == "collected_with_unresolved_sources"


def test_secret_reference_in_another_project_is_not_followed():
    native = NativeFixture()
    native.pin()
    for row in native.rows["revisions"]:
        row["containers"][0]["env"][0]["valueSource"]["secretKeyRef"]["secret"] = (
            "projects/foreign/secrets/model"
        )
    result = inv.collect_retained_credentials(native)
    assert len(result.unresolved) == 3
    assert not any(api == "secret" for api, _, _ in native.calls)


def test_parent_job_binding_must_exist_and_match_resolved_execution():
    native = NativeFixture()
    native.rows["executions"][0]["job"] = inv.PARENT + "/jobs/some-other-job"
    with pytest.raises(inv.InventoryDenied, match="native_parent_binding_unavailable"):
        inv.collect_retained_credentials(native)


def test_pagination_exhaustion_and_payload_bound_refuse(monkeypatch):
    native = NativeFixture()
    native.pages["services"] = {"": {"services": native.rows["services"], "nextPageToken": "more"}}
    monkeypatch.setattr(inv, "MAX_PAGES", 1)
    with pytest.raises(inv.InventoryDenied, match="native_pagination_bound"):
        inv.collect_retained_credentials(native)


def test_sanitized_resource_identity_cannot_reflect_loaded_credential():
    native = NativeFixture()
    # The source schema itself allows this native name, but publishing it would
    # reveal a candidate credential. The return boundary must refuse it.
    row = source(inv.PARENT + "/jobs/" + MODEL, "job", [env("LLM_API_KEY", MODEL)])
    native.rows["jobs"].append(row)
    native.gets[row["name"]] = row
    with pytest.raises(inv.InventoryDenied, match="sanitized_provenance_secret_reflection"):
        inv.collect_retained_credentials(native)


def test_native_get_success_uses_fixed_origin_get_and_no_body():
    native = inv.NativeTransport("synthetic-native-bearer")
    observed = []

    def open(request, timeout):
        observed.append(request)
        return Response(request.full_url, b'{"projectId":"ai-resume-parser-482412"}')

    native._opener.open = open
    result = native.get("project", "projects/" + inv.PROJECT, {})
    assert result["projectId"] == inv.PROJECT
    assert (
        observed[0].full_url
        == "https://cloudresourcemanager.googleapis.com/v3/projects/ai-resume-parser-482412"
    )
    assert observed[0].get_method() == "GET" and observed[0].data is None


@pytest.mark.parametrize("state", [{}, [], True, 1, None])
def test_secret_state_invalid_scalar_is_fixed_refusal(state):
    native = NativeFixture()
    native.pin()
    native.secrets[REFERENCE]["state"] = state
    with pytest.raises(
        inv.InventoryDenied, match="native_secret_metadata_identity_or_state_invalid"
    ):
        inv.collect_retained_credentials(native)


def test_excluded_payment_values_are_not_returned_in_any_private_inventory_field():
    native = NativeFixture()
    result = inv.collect_retained_credentials(native)
    assert PAYMENT not in [c.value for c in result.credentials]
    assert PAYMENT not in [s.value for s in result.secret_handles]
    assert not hasattr(result, "sensitive_values")


def test_unicode_environment_value_refuses_without_echo():
    native = NativeFixture()
    native.rows["revisions"][0]["containers"][0]["env"] = [env("LLM_API_KEY", "\ud800")]
    with pytest.raises(inv.InventoryDenied, match="native_environment_source_shape_unsupported"):
        inv.collect_retained_credentials(native)


def test_no_report_values_are_exposed_for_disabled_or_unknown_source():
    native = NativeFixture()
    native.pin("DISABLED")
    native.rows["services"][0]["template"]["containers"][0]["env"].append(
        env("ANTHROPIC_API_KEY", "synthetic-unknown-source-value")
    )
    result = inv.collect_retained_credentials(native)
    summary = json.dumps(result.sanitized_provenance())
    assert "synthetic-unknown-source-value" not in summary
    assert "unavailable" in summary and "collected_with_unresolved_sources" in summary


def test_native_transport_request_count_bound_prevents_next_get(monkeypatch):
    native = inv.NativeTransport("synthetic-native-bearer")
    native._calls = inv.MAX_CALLS
    with pytest.raises(inv.InventoryDenied, match="native_collection_budget_exceeded"):
        native.get("project", "projects/" + inv.PROJECT, {})


@pytest.mark.parametrize("value", [None, True, 1, {}, [], "", "synthetic token"])
def test_native_token_type_and_whitespace_refuse_without_native_call(value):
    with pytest.raises(inv.InventoryDenied, match="native_access_token_unavailable"):
        inv.NativeTransport(value)


def test_native_time_budget_prevents_another_request(monkeypatch):
    native = inv.NativeTransport("synthetic-native-bearer")
    calls = []

    def refuse_unexpected_open(*args, **kwargs):
        calls.append(True)
        raise AssertionError("synthetic opener refuses network in cutoff test")

    monkeypatch.setattr(native._opener, "open", refuse_unexpected_open)
    now = native._started + inv.MAX_SECONDS
    monkeypatch.setattr(inv.time, "monotonic", lambda: now)
    with pytest.raises(inv.InventoryDenied, match="native_collection_budget_exceeded"):
        native.get("project", "projects/" + inv.PROJECT, {})
    assert not calls


def test_resource_bound_refuses_before_independent_gets(monkeypatch):
    native = NativeFixture()
    monkeypatch.setattr(inv, "MAX_RESOURCES", 2)
    with pytest.raises(inv.InventoryDenied, match="native_resource_bound"):
        inv.collect_retained_credentials(native)
    assert not any(api == "run" and not params for api, _, params in native.calls)


def test_unavailable_enabled_secret_access_never_returns_partial_inventory():
    native = NativeFixture()
    native.pin()
    native.overrides[("secret", REFERENCE + ":access")] = inv.InventoryDenied(
        "native_read_unavailable"
    )
    with pytest.raises(inv.InventoryDenied, match="native_read_unavailable"):
        inv.collect_retained_credentials(native)


def _reflect_revision_name(native, suffix):
    row = native.rows["revisions"][0]
    previous = row["name"]
    row["name"] = row["service"] + "/revisions/" + suffix
    native.gets.pop(previous)
    native.gets[row["name"]] = row
    return row


@pytest.mark.parametrize("alias", ["DB_PASSWORD", "PG_PASSWORD"])
def test_credible_unresolved_password_literal_cannot_reflect_in_provenance(alias):
    native = NativeFixture()
    secret = "reflected-db-password"
    row = _reflect_revision_name(native, secret)
    row["containers"][0]["env"] = [env(alias, secret)]
    with pytest.raises(inv.InventoryDenied, match="sanitized_provenance_secret_reflection"):
        inv.collect_retained_credentials(native)
    assert not any(api == "secret" for api, _, _ in native.calls)


def test_database_url_decoded_password_cannot_reflect_in_provenance():
    native = NativeFixture()
    secret = "private-test-password"
    row = _reflect_revision_name(native, secret)
    row["containers"][0]["env"] = [env("DATABASE_URL", DATABASE)]
    with pytest.raises(inv.InventoryDenied, match="sanitized_provenance_secret_reflection"):
        inv.collect_retained_credentials(native)


def test_short_excluded_jwt_secret_cannot_reflect_in_provenance():
    native = NativeFixture()
    secret = "short-key"
    row = _reflect_revision_name(native, secret)
    row["containers"][0]["env"] = [env("JWT_SECRET", secret)]
    with pytest.raises(inv.InventoryDenied, match="sanitized_provenance_secret_reflection"):
        inv.collect_retained_credentials(native)
    assert not any(api == "secret" for api, _, _ in native.calls)


@pytest.mark.parametrize(
    "field",
    ["password", "sslpassword", "scram_client_key", "scram_server_key", "oauth_client_secret"],
)
def test_database_authentication_query_secret_cannot_reflect_in_provenance(field):
    native = NativeFixture()
    secret = "private-query-secret"
    row = _reflect_revision_name(native, secret)
    row["containers"][0]["env"] = [env("DATABASE_URL", DATABASE + "?" + field + "=" + secret)]
    with pytest.raises(inv.InventoryDenied, match="sanitized_provenance_secret_reflection"):
        inv.collect_retained_credentials(native)


def test_repeated_database_query_secret_values_are_all_redacted():
    native = NativeFixture()
    row = _reflect_revision_name(native, "second-query-secret")
    row["containers"][0]["env"] = [
        env("DATABASE_URL", DATABASE + "?password=first-query-secret&password=second-query-secret")
    ]
    with pytest.raises(inv.InventoryDenied, match="sanitized_provenance_secret_reflection"):
        inv.collect_retained_credentials(native)


def test_pinned_database_payload_password_component_is_transiently_redacted():
    native = NativeFixture()
    native.pin(value=DATABASE)
    for row in native.rows["revisions"]:
        row["containers"][0]["env"][0]["name"] = "DATABASE_URL"
    _reflect_revision_name(native, "private-test-password")
    with pytest.raises(inv.InventoryDenied, match="sanitized_provenance_secret_reflection"):
        inv.collect_retained_credentials(native)
    assert any(api == "secret" and path == REFERENCE + ":access" for api, path, _ in native.calls)


def test_unreflected_unsupported_alias_remains_unresolved_not_candidate():
    native = NativeFixture()
    native.rows["revisions"][0]["containers"][0]["env"] = [
        env("PG_PASSWORD", "unreflected-short-secret")
    ]
    result = inv.collect_retained_credentials(native)
    assert any(u.origin.environment_name == "PG_PASSWORD" for u in result.unresolved)
    assert "unreflected-short-secret" not in [c.value for c in result.credentials]
    assert not result.secret_handles
    assert not any(api == "secret" for api, _, _ in native.calls)


def test_unparseable_credential_uri_refuses_before_any_report_even_without_reflection():
    native = NativeFixture()
    secret = "private-port-password"
    dsn = "postgresql://synthetic:" + secret + "@database.invalid:not-a-port/neondb"
    native.rows["revisions"][0]["containers"][0]["env"] = [env("DATABASE_URL", dsn)]
    with pytest.raises(
        inv.InventoryDenied, match="credential_uri_redaction_parse_unavailable"
    ) as error:
        inv.collect_retained_credentials(native)
    assert secret not in str(error.value) and dsn not in str(error.value)
    assert error.value.__cause__ is None and error.value.__suppress_context__
    assert not any(api == "secret" for api, _, _ in native.calls)


@pytest.mark.parametrize("kind", ["revision", "execution"])
@pytest.mark.parametrize("parent_form", ["full", "short"])
def test_native_child_parent_accepts_only_exact_full_or_observed_short_name(kind, parent_form):
    native = NativeFixture()
    row = native.rows["revisions" if kind == "revision" else "executions"][0]
    field = "service" if kind == "revision" else "job"
    full_parent = row[field]
    if parent_form == "short":
        row[field] = full_parent.rsplit("/", 1)[1]
    result = inv.collect_retained_credentials(native)
    assert (kind, row["name"], row["uid"]) in result.resources
    assert result.sanitized_provenance()["cutover_ready"] is False
    assert not any(api == "secret" for api, _, _ in native.calls)


@pytest.mark.parametrize("kind", ["revision", "execution"])
@pytest.mark.parametrize(
    "invalid_parent",
    [
        "wrong-short",
        "empty",
        "missing",
        "nonscalar",
        "foreign-project",
        "foreign-region",
        "wrong-type",
        "numeric-alias",
    ],
)
def test_short_parent_compatibility_keeps_exact_namespace_and_shape_refusals(kind, invalid_parent):
    native = NativeFixture()
    row = native.rows["revisions" if kind == "revision" else "executions"][0]
    field = "service" if kind == "revision" else "job"
    full_parent = row[field]
    replacements = {
        "wrong-short": "some-other-parent",
        "empty": "",
        "missing": None,
        "nonscalar": {"name": full_parent.rsplit("/", 1)[1]},
        "foreign-project": full_parent.replace(inv.PROJECT, "foreign-project"),
        "foreign-region": full_parent.replace(inv.REGION, "europe-west1"),
        "wrong-type": full_parent.replace("/services/", "/jobs/")
        if kind == "revision"
        else full_parent.replace("/jobs/", "/services/"),
        "numeric-alias": full_parent.replace(inv.PROJECT, inv.PROJECT_NUMBER),
    }
    row[field] = replacements[invalid_parent]
    if invalid_parent == "missing":
        del row[field]
    with pytest.raises(inv.InventoryDenied, match="native_parent_binding_unavailable"):
        inv.collect_retained_credentials(native)
    assert not any(api == "secret" for api, _, _ in native.calls)


@pytest.mark.parametrize("kind", ["revision", "execution"])
@pytest.mark.parametrize("parent_form", ["full", "short"])
@pytest.mark.parametrize("deleted_child", [False, True])
def test_retained_child_missing_or_pruned_parent_still_refuses(kind, parent_form, deleted_child):
    native = NativeFixture()
    if kind == "revision":
        full_parent = inv.PARENT + "/services/pruned-parent"
        row = source(full_parent + "/revisions/retained-child", kind, [env("LLM_API_KEY", MODEL)])
        native.rows["revisions"].append(row)
        native.gets[row["name"]] = row
        field = "service"
    else:
        row = native.rows["executions"][0]
        full_parent = row["job"]
        native.rows["jobs"] = []
        field = "job"
    if parent_form == "short":
        row[field] = full_parent.rsplit("/", 1)[1]
    if deleted_child:
        row["deleteTime"] = "2026-10-09T00:00:00Z"
        row["expireTime"] = "2026-11-09T00:00:00Z"
    with pytest.raises(inv.InventoryDenied, match="native_parent_binding_unavailable"):
        inv.collect_retained_credentials(native)


@pytest.mark.parametrize("kind", ["revision", "execution"])
def test_short_parent_does_not_conflate_numeric_and_id_parent_inventory(kind):
    native = NativeFixture()
    row = native.rows["revisions" if kind == "revision" else "executions"][0]
    old_name = row["name"]
    field = "service" if kind == "revision" else "job"
    row["name"] = old_name.replace(inv.PROJECT, inv.PROJECT_NUMBER)
    row[field] = row[field].rsplit("/", 1)[1]
    native.gets[row["name"]] = native.gets.pop(old_name)
    with pytest.raises(inv.InventoryDenied, match="native_parent_binding_unavailable"):
        inv.collect_retained_credentials(native)


@pytest.mark.parametrize("kind", ["revision", "execution"])
def test_short_full_parent_disagreement_between_list_and_get_still_refuses(kind):
    native = NativeFixture()
    row = native.rows["revisions" if kind == "revision" else "executions"][0]
    field = "service" if kind == "revision" else "job"
    changed = copy.deepcopy(row)
    changed[field] = row[field].rsplit("/", 1)[1]
    native.overrides[("run", row["name"])] = changed
    with pytest.raises(inv.InventoryDenied, match="native_list_get_configuration_mismatch"):
        inv.collect_retained_credentials(native)


@pytest.mark.parametrize("started", [600.0000000000001, 1536.0000000000002])
@pytest.mark.parametrize("phase", ["before", "exact", "after"])
@pytest.mark.parametrize("guard", ["before_request", "after_payload"])
def test_native_absolute_deadline_at_adverse_float_starts(monkeypatch, started, phase, guard):
    clock = [started]
    monkeypatch.setattr(inv.time, "monotonic", lambda: clock[0])
    native = inv.NativeTransport("synthetic-native-bearer")
    deadline = started + inv.MAX_SECONDS
    point = {
        "before": math.nextafter(deadline, -math.inf),
        "exact": deadline,
        "after": math.nextafter(deadline, math.inf),
    }[phase]
    calls = []

    def open(request, timeout):
        assert timeout == 5
        calls.append(request.full_url)
        if guard == "after_payload":
            clock[0] = point
        return Response(request.full_url, b'{"ok":true}')

    monkeypatch.setattr(native._opener, "open", open)
    clock[0] = point if guard == "before_request" else math.nextafter(deadline, -math.inf)
    if phase == "before":
        assert native.get("project", "projects/" + inv.PROJECT, {}) == {"ok": True}
    else:
        with pytest.raises(inv.InventoryDenied, match="native_collection_budget_exceeded"):
            native.get("project", "projects/" + inv.PROJECT, {})
    assert native._calls == 1
    assert len(calls) == (0 if guard == "before_request" and phase != "before" else 1)


@pytest.mark.parametrize("started", [600.0000000000001, 1536.0000000000002])
@pytest.mark.parametrize("phase", ["before", "exact", "after"])
def test_collection_absolute_deadline_at_adverse_float_starts(monkeypatch, started, phase):
    native = NativeFixture()
    clock = [started]
    monkeypatch.setattr(inv.time, "monotonic", lambda: clock[0])
    deadline = started + inv.MAX_SECONDS
    point = {
        "before": math.nextafter(deadline, -math.inf),
        "exact": deadline,
        "after": math.nextafter(deadline, math.inf),
    }[phase]
    original = native.get

    def get(api, path, params):
        value = original(api, path, params)
        if (
            api == "run"
            and params
            and path.endswith("/executions")
            and native.list_counts["executions"] == 2
        ):
            clock[0] = point
        return value

    native.get = get
    if phase == "before":
        assert inv.collect_retained_credentials(native).sanitized_provenance()["cutover_ready"] is False
    else:
        with pytest.raises(inv.InventoryDenied, match="native_collection_budget_exceeded"):
            inv.collect_retained_credentials(native)
    assert all(count == 2 for count in native.list_counts.values())
    assert not any(api == "secret" for api, _, _ in native.calls)
