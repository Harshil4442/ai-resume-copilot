"""Exact GitHub-trigger builder repair; no native access at import.

Invoke only in the reviewed operation owner. Unknown mutations never replay.
The ordinary retained Google SDK manager owns authentication. Operator logs,
artifacts, command arguments and mutation receipts contain no credential values.
This module has no actor-disable, deployment, secret, SQL, key or build-create route.
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

PROJECT = "ai-resume-parser-482412"
NUMBER = "157225724590"
REGION = "us-central1"
TRIGGER_ID = "42c65704-7868-4af4-96b9-60879bb2d655"
TRIGGER_PATH = f"projects/{PROJECT}/locations/{REGION}/triggers/{TRIGGER_ID}"
OLD_EMAIL = NUMBER + "-compute@developer.gserviceaccount.com"
OLD_UID = "115689643755778240830"
OLD_SA = f"projects/{PROJECT}/serviceAccounts/{OLD_EMAIL}"
BUILDER_ID = "hirewiz-builder-20261014"
BUILDER_EMAIL = f"{BUILDER_ID}@{PROJECT}.iam.gserviceaccount.com"
BUILDER_SA = f"projects/{PROJECT}/serviceAccounts/{BUILDER_EMAIL}"
MEMBER = "serviceAccount:" + BUILDER_EMAIL
REPOSITORY = f"projects/{PROJECT}/locations/{REGION}/repositories/cloud-run-source-deploy"
BUCKET = PROJECT + "_cloudbuild"
DOCX_BUILD_ID = "bc25069a-8137-4d73-a0e5-8fa1fcae7f54"
DOCX_BUILD = f"projects/{PROJECT}/locations/{REGION}/builds/{DOCX_BUILD_ID}"
DOCX_BUILD_NAME = f"projects/{NUMBER}/locations/{REGION}/builds/{DOCX_BUILD_ID}"
TERMINAL = frozenset({"SUCCESS", "FAILURE", "INTERNAL_ERROR", "TIMEOUT", "CANCELLED", "EXPIRED"})
UNFINISHED_FILTER = " AND ".join(f'status!="{status}"' for status in sorted(TERMINAL))
TRIGGER_FIELDS = "id,resourceName,name,filename,github,serviceAccount,disabled"
BUILD_FIELDS = "id,name,projectId,status,serviceAccount,buildTriggerId"
OLD_PRINCIPALS = {
    OLD_EMAIL: OLD_UID,
    "hirewiz-api@ai-resume-parser-482412.iam.gserviceaccount.com": "101596337064821972716",
    "hirewiz-worker@ai-resume-parser-482412.iam.gserviceaccount.com": "115123377998883908933",
    "hirewiz-employer-worker@ai-resume-parser-482412.iam.gserviceaccount.com": "105594666905475527509",
    "ais-gemini-key-43d738d3dfb9485@226979001779.iam.gserviceaccount.com": "100592881791239522151",
    "ais-gemini-key-41af2f49a3fb4b6@157225724590.iam.gserviceaccount.com": "117611807420650799123",
    "ais-gemini-key-7aeb7c91129947c@157225724590.iam.gserviceaccount.com": "103651370799427077361",
}


class RepairDenied(RuntimeError):
    """Safe refusal code only; vendor exceptions and bodies never escape."""


def need(value: Any, code: str) -> None:
    if not value:
        raise RepairDenied(code)


def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        need(key not in result, "builder_duplicate_native_json_key")
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class Builder:
    uid: str
    email: str = BUILDER_EMAIL

    def validate(self) -> None:
        need(self.email == BUILDER_EMAIL and type(self.uid) is str
             and re.fullmatch(r"[1-9][0-9]{5,24}", self.uid)
             and self.uid not in OLD_PRINCIPALS.values(), "builder_native_uid_invalid")


def trigger(row: Any, *, service_account: str, disabled: bool) -> dict[str, Any]:
    need(service_account in {OLD_SA, BUILDER_SA} and type(disabled) is bool
         and type(row) is dict and row.get("id") == TRIGGER_ID
         and row.get("resourceName") == TRIGGER_PATH
         and row.get("name") == "Trigger-for-github-updates"
         and row.get("filename") == "cloudbuild.yaml"
         and row.get("serviceAccount") == service_account
         and row.get("disabled", False) is disabled, "builder_trigger_identity_or_state_drift")
    need(not any(row.get(key) for key in (
        "build", "gitFileSource", "sourceToBuild", "repositoryEventConfig",
        "triggerTemplate", "pubsubConfig", "webhookConfig", "autodetect",
    )), "builder_trigger_alternative_source_refused")
    github = row.get("github")
    need(type(github) is dict and github.get("owner") == "Harshil4442"
         and github.get("name") == "ai-resume-copilot"
         and not github.get("pullRequest") and not github.get("enterpriseConfigResourceName"),
         "builder_trigger_repository_drift")
    push = github.get("push")
    need(type(push) is dict and push.get("branch") == "^main$"
         and not push.get("tag") and push.get("invertRegex", False) is False,
         "builder_trigger_branch_drift")
    return {key: copy.deepcopy(row[key]) for key in ("id", "resourceName", "name", "filename", "github")}


def add_binding(policy: Any, role: str) -> dict[str, Any]:
    """Preserve every other policy field/binding, including its native etag."""
    need(type(policy) is dict and type(policy.get("etag")) is str and bool(policy["etag"])
         and policy.get("version", 1) in {1, 3}, "builder_policy_etag_or_version_missing")
    bindings = policy.get("bindings", [])
    need(type(bindings) is list and len(bindings) <= 512, "builder_policy_shape_refused")
    for binding in bindings:
        need(type(binding) is dict and type(binding.get("role")) is str
             and type(binding.get("members")) is list
             and all(type(member) is str for member in binding["members"]),
             "builder_policy_binding_shape_refused")
        need(MEMBER not in binding["members"], "builder_grant_exists_reconcile_only")
    result = copy.deepcopy(policy)
    matches = [binding for binding in result.get("bindings", [])
               if binding["role"] == role and "condition" not in binding]
    need(len(matches) <= 1, "builder_duplicate_unconditional_role")
    if matches:
        matches[0]["members"].append(MEMBER)
    else:
        result.setdefault("bindings", []).append({"role": role, "members": [MEMBER]})
    return result


def assert_binding(policy: Any, role: str) -> None:
    need(type(policy) is dict and type(policy.get("bindings", [])) is list,
         "builder_policy_readback_shape")
    need(all(type(binding) is dict and type(binding.get("members")) is list
             and all(type(member) is str for member in binding["members"])
             for binding in policy.get("bindings", [])), "builder_policy_readback_binding_shape")
    found = [binding for binding in policy.get("bindings", []) if MEMBER in binding["members"]]
    need(len(found) == 1 and found[0].get("role") == role and "condition" not in found[0],
         "builder_permission_readback_drift")


def assert_policy_preserved(observed: Any, expected: dict[str, Any]) -> None:
    need(type(observed) is dict, "builder_policy_readback_shape")
    def normalized(policy: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(policy)
        result.pop("etag", None)
        result.setdefault("version", 1)
        bindings = result.setdefault("bindings", [])
        need(type(bindings) is list and all(type(binding) is dict
             and type(binding.get("members")) is list for binding in bindings),
             "builder_policy_readback_binding_shape")
        for binding in bindings:
            need(all(type(member) is str for member in binding["members"]),
                 "builder_policy_readback_binding_shape")
            binding["members"].sort()
        bindings.sort(key=lambda binding: json.dumps(binding, sort_keys=True))
        return result
    need(normalized(observed) == normalized(expected), "builder_policy_unrelated_delta")


class SDKRepair:
    """Stock SDK opaque auth with fixed resources and durable mutation attempts.

    SDK refresh/authentication-challenge behavior is retained; application
    mutation retries, custom credential loaders and redirect following are absent.
    """

    def __init__(self, sdk_library: Path, account: str, workdir: Path):
        need(sdk_library.is_absolute() and type(account) is str and bool(account),
             "builder_sdk_configuration_invalid")
        self.sdk_library, self.account, self.workdir = sdk_library, account, workdir
        self.http: Any = None
        self.pid = os.getpid()

    def _http(self) -> Any:
        need(self.pid == os.getpid(), "builder_owner_process_changed")
        if self.http is None:
            sys.path[:0] = [str(self.sdk_library), str(self.sdk_library / "third_party")]
            from googlecloudsdk.core import properties
            from googlecloudsdk.core.credentials import transports

            p = properties.VALUES
            need(p.core.account.Get() == self.account and p.core.project.Get() == PROJECT
                 and p.core.universe_domain.Get() == "googleapis.com"
                 and p.core.disable_file_logging.GetBool() and not p.core.log_http.GetBool()
                 and not p.core.custom_ca_certs_file.Get() and not p.auth.disable_ssl_validation.GetBool()
                 and not p.auth.disable_credentials.GetBool() and not p.auth.credential_file_override.Get()
                 and not p.auth.access_token_file.Get() and not p.auth.impersonate_service_account.Get()
                 and p.auth.token_host.Get() in (None, "", "https://oauth2.googleapis.com/token")
                 and not p.proxy.address.Get() and not p.proxy.port.Get() and not p.proxy.proxy_type.Get(),
                 "builder_normal_retained_manager_required")
            self.http = transports.GetApitoolsTransport(
                timeout=3, enable_resource_quota=False, allow_account_impersonation=False,
                redact_request_body_reason="reviewed HireWiz builder identity repair")
            self.http.session.max_redirects = 0
            self.http.session.trust_env = False
        return self.http

    def _once(self, phase: str) -> None:
        try:
            st = self.workdir.lstat()
            need(self.workdir.is_absolute() and not self.workdir.is_symlink() and self.workdir.is_dir()
                 and st.st_uid == os.getuid() and st.st_mode & 0o777 == 0o700,
                 "builder_private_attempt_directory_required")
            fd = os.open(self.workdir / (phase + ".started.json"),
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(json.dumps({"phase": phase, "automatic_replay": False}).encode() + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            fd = os.open(self.workdir, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except RepairDenied:
            raise
        except Exception:
            raise RepairDenied("builder_attempt_exists_or_unknown_reconcile_only") from None

    def _call(self, api: str, method: str, path: str, *, body: Any = None,
              query: dict[str, str] | None = None, allow_absent: bool = False) -> dict[str, Any] | None:
        origins = {"iam": "https://iam.googleapis.com/v1/", "build": "https://cloudbuild.googleapis.com/v1/",
                   "project": "https://cloudresourcemanager.googleapis.com/v1/",
                   "resource": "https://cloudresourcemanager.googleapis.com/v3/",
                   "artifact": "https://artifactregistry.googleapis.com/v1/",
                   "storage": "https://storage.googleapis.com/storage/v1/"}
        allowed = (
            api == "resource" and method == "GET" and path == "projects/" + PROJECT
            or api == "iam" and method == "GET"
            and path in {"projects/-/serviceAccounts/" + email for email in (*OLD_PRINCIPALS, BUILDER_EMAIL)}
            or api == "iam" and method == "POST" and path == "projects/" + PROJECT + "/serviceAccounts"
            or api == "build" and method in {"GET", "PATCH"} and path == TRIGGER_PATH
            or api == "build" and method == "GET" and path in {
                f"projects/{PROJECT}/locations/{location}/builds" for location in (REGION, "global")}
            or api == "build" and method == "GET" and path == DOCX_BUILD
            or api == "project" and method == "POST"
            and path in {"projects/" + PROJECT + suffix for suffix in (":getIamPolicy", ":setIamPolicy")}
            or api == "artifact" and method == "GET" and path in {REPOSITORY, REPOSITORY + ":getIamPolicy"}
            or api == "artifact" and method == "POST" and path == REPOSITORY + ":setIamPolicy"
            or api == "storage" and method == "GET" and path in {"b/" + BUCKET, "b/" + BUCKET + "/iam"}
            or api == "storage" and method == "PUT" and path == "b/" + BUCKET + "/iam")
        need(allowed, "builder_native_route_refused")
        url = origins[api] + path + ("?" + urlencode(query) if query else "")
        try:
            response, raw = self._http().request(
                url, method=method, body=None if body is None else json.dumps(body).encode(),
                headers={"Content-Type": "application/json", "Cache-Control": "no-cache"}, redirections=0)
            if allow_absent and method == "GET" and int(response.status) == 404:
                return None
            need(int(response.status) == 200 and type(raw) is bytes and len(raw) <= 1024 * 1024,
                 "builder_native_result_unknown_reconcile_only")
            result = json.loads(raw, object_pairs_hook=object_pairs)
            need(type(result) is dict, "builder_native_result_shape_refused")
            return result
        except RepairDenied:
            raise
        except Exception:
            raise RepairDenied("builder_native_result_unknown_reconcile_only") from None

    def _project(self) -> None:
        row = self._call("resource", "GET", "projects/" + PROJECT,
                         query={"fields": "name,projectId,state"})
        need(row is not None and row.get("name") == "projects/" + NUMBER
             and row.get("projectId") == PROJECT and row.get("state") == "ACTIVE",
             "builder_project_number_drift")

    def read_trigger(self, *, service_account: str, disabled: bool) -> dict[str, Any]:
        return trigger(self._full_trigger(service_account=service_account, disabled=disabled),
                       service_account=service_account, disabled=disabled)

    def _full_trigger(self, *, service_account: str, disabled: bool) -> dict[str, Any]:
        row = self._call("build", "GET", TRIGGER_PATH)
        trigger(row, service_account=service_account, disabled=disabled)
        assert isinstance(row, dict)
        return copy.deepcopy(row)

    def _update_trigger_once(
        self, phase: str, *, field: str, value: str | bool,
        service_account: str, disabled: bool,
    ) -> None:
        """Preserve the complete current native record in a single-field PATCH.

        Cloud Build rejects partial bodies for this existing GitHub trigger.
        Native current GET is required; saved packets are never patch inputs.
        """
        need(type(disabled) is bool and (
            type(value) is bool if field == "disabled" else type(value) is str)
            and (phase, field, value, service_account, disabled) in {
            ("builder-trigger-disable", "disabled", True, OLD_SA, False),
            ("builder-trigger-retarget", "serviceAccount", BUILDER_SA, OLD_SA, True),
            ("builder-trigger-enable", "disabled", False, BUILDER_SA, True),
        }, "builder_trigger_update_scope_refused")
        before = self._full_trigger(service_account=service_account, disabled=disabled)
        desired = copy.deepcopy(before)
        desired[field] = value
        expected_account = BUILDER_SA if field == "serviceAccount" else service_account
        expected_disabled = value if field == "disabled" else disabled
        assert isinstance(expected_disabled, bool)
        trigger(desired, service_account=expected_account, disabled=expected_disabled)
        self._once(phase)
        self._call("build", "PATCH", TRIGGER_PATH, body=desired,
                   query={"projectId": PROJECT, "triggerId": TRIGGER_ID, "updateMask": field})
        after = self._full_trigger(service_account=expected_account, disabled=expected_disabled)
        # Cloud Build can omit its default false value on reads. Every other
        # field, including native optional trigger settings, must be preserved.
        after.setdefault("disabled", False)
        desired.setdefault("disabled", False)
        need(after == desired, "builder_trigger_unrelated_configuration_drift")

    def _old_account(self, email: str, uid: str, *, disabled: bool) -> None:
        row = self._call("iam", "GET", "projects/-/serviceAccounts/" + email,
                         query={"fields": "email,uniqueId,disabled"})
        need(row is not None and row.get("email") == email and row.get("uniqueId") == uid
             and row.get("disabled", False) is disabled, "builder_old_principal_uid_or_state_drift")

    def disable_trigger_once(self) -> None:
        self._project()
        self._old_account(OLD_EMAIL, OLD_UID, disabled=False)
        self._update_trigger_once("builder-trigger-disable", field="disabled", value=True,
                                  service_account=OLD_SA, disabled=False)

    def require_drained_old_builder(self) -> None:
        """Read immediately before old Compute disable; no saved boolean gate."""
        self._project()
        self._old_account(OLD_EMAIL, OLD_UID, disabled=False)
        self.read_trigger(service_account=OLD_SA, disabled=True)
        row = self._call("build", "GET", DOCX_BUILD, query={"fields": BUILD_FIELDS})
        need(row is not None and row.get("id") == DOCX_BUILD_ID and row.get("name") == DOCX_BUILD_NAME
             and row.get("projectId") == PROJECT and row.get("serviceAccount") == OLD_SA
             and row.get("status") in TERMINAL, "builder_docx_build_not_terminal")
        for location in (REGION, "global"):
            row = self._call("build", "GET", f"projects/{PROJECT}/locations/{location}/builds",
                             query={"filter": UNFINISHED_FILTER, "pageSize": "100",
                                    "fields": "builds(" + BUILD_FIELDS + "),nextPageToken"})
            need(row is not None and not row.get("nextPageToken") and not row.get("unreachable")
                 and row.get("builds", []) == [], "builder_live_or_partial_build_inventory")
        # Native trigger readback closes config drift during the inventory reads.
        self.read_trigger(service_account=OLD_SA, disabled=True)

    def require_old_principals_isolated(self) -> None:
        self._project()
        for email, uid in OLD_PRINCIPALS.items():
            self._old_account(email, uid, disabled=True)

    def _builder(self, row: Any, expected: Builder | None = None) -> Builder:
        need(type(row) is dict and row.get("email") == BUILDER_EMAIL
             and row.get("projectId") == PROJECT and row.get("disabled", False) is False,
             "builder_account_identity_or_state_drift")
        result = Builder(row.get("uniqueId"))
        result.validate()
        need(expected is None or result == expected, "builder_account_uid_drift")
        return result

    def read_builder(self, expected: Builder | None = None) -> Builder:
        return self._builder(self._call("iam", "GET", "projects/-/serviceAccounts/" + BUILDER_EMAIL,
                             query={"fields": "email,uniqueId,projectId,disabled"}), expected)

    def create_builder_once(self) -> Builder:
        self.require_old_principals_isolated()
        self.read_trigger(service_account=OLD_SA, disabled=True)
        need(self._call("iam", "GET", "projects/-/serviceAccounts/" + BUILDER_EMAIL,
                       allow_absent=True, query={"fields": "email,uniqueId,projectId,disabled"}) is None,
             "builder_account_exists_reconcile_only")
        self._once("builder-account-create")
        row = self._call("iam", "POST", "projects/" + PROJECT + "/serviceAccounts",
                         body={"accountId": BUILDER_ID, "serviceAccount": {"displayName": "HireWiz build and push"}},
                         query={"fields": "email,uniqueId,projectId,disabled"})
        created = self._builder(row)
        return self.read_builder(created)

    def _resource_guards(self) -> None:
        self._project()
        repo = self._call("artifact", "GET", REPOSITORY, query={"fields": "name,format"})
        bucket = self._call("storage", "GET", "b/" + BUCKET,
                            query={"fields": "id,name,projectNumber"})
        need(repo is not None and repo.get("name") == REPOSITORY and repo.get("format") == "DOCKER",
             "builder_repository_native_drift")
        need(bucket is not None and bucket.get("name") == BUCKET and bucket.get("id") == BUCKET
             and str(bucket.get("projectNumber")) == NUMBER, "builder_source_bucket_native_drift")

    def _policy(self, resource: str) -> dict[str, Any]:
        if resource == "project":
            row = self._call("project", "POST", "projects/" + PROJECT + ":getIamPolicy",
                             body={"options": {"requestedPolicyVersion": 3}})
        elif resource == "repository":
            row = self._call("artifact", "GET", REPOSITORY + ":getIamPolicy",
                             query={"options.requestedPolicyVersion": "3"})
        else:
            need(resource == "bucket", "builder_policy_resource_refused")
            row = self._call("storage", "GET", "b/" + BUCKET + "/iam",
                             query={"optionsRequestedPolicyVersion": "3"})
        need(row is not None, "builder_policy_unavailable")
        assert row is not None
        return row

    def grant_builder_once(self, builder: Builder) -> None:
        builder.validate()
        self.require_old_principals_isolated()
        self.read_builder(builder)
        self.read_trigger(service_account=OLD_SA, disabled=True)
        self._resource_guards()
        grants = (("repository", "roles/artifactregistry.writer"),
                  ("project", "roles/logging.logWriter"), ("bucket", "roles/storage.objectViewer"))
        # Validate every current policy before the first write.
        for resource, role in grants:
            add_binding(self._policy(resource), role)
        self.grant_repository_once(builder)
        self.grant_logging_once(builder)
        self.grant_source_read_once(builder)

    def _grant_one_once(self, builder: Builder, resource: str, role: str) -> None:
        need((resource, role) in {("repository", "roles/artifactregistry.writer"),
                                 ("project", "roles/logging.logWriter"),
                                 ("bucket", "roles/storage.objectViewer")}, "builder_grant_scope_refused")
        self.require_old_principals_isolated()
        self.read_builder(builder)
        self.read_trigger(service_account=OLD_SA, disabled=True)
        self._resource_guards()
        policy = add_binding(self._policy(resource), role)
        self._once("builder-grant-" + resource)
        if resource == "project":
            self._call("project", "POST", "projects/" + PROJECT + ":setIamPolicy", body={"policy": policy})
        elif resource == "repository":
            self._call("artifact", "POST", REPOSITORY + ":setIamPolicy", body={"policy": policy})
        else:
            self._call("storage", "PUT", "b/" + BUCKET + "/iam", body=policy)
        observed = self._policy(resource)
        assert_binding(observed, role)
        assert_policy_preserved(observed, policy)

    def grant_repository_once(self, builder: Builder) -> None:
        self._grant_one_once(builder, "repository", "roles/artifactregistry.writer")

    def grant_logging_once(self, builder: Builder) -> None:
        self._grant_one_once(builder, "project", "roles/logging.logWriter")

    def grant_source_read_once(self, builder: Builder) -> None:
        self._grant_one_once(builder, "bucket", "roles/storage.objectViewer")

    def verify_builder_permissions(self, builder: Builder) -> None:
        self.require_old_principals_isolated()
        self.read_builder(builder)
        self._resource_guards()
        for resource, role in (("repository", "roles/artifactregistry.writer"),
                               ("project", "roles/logging.logWriter"), ("bucket", "roles/storage.objectViewer")):
            assert_binding(self._policy(resource), role)

    def retarget_once(self, builder: Builder) -> None:
        self.verify_builder_permissions(builder)
        self._update_trigger_once("builder-trigger-retarget", field="serviceAccount", value=BUILDER_SA,
                                  service_account=OLD_SA, disabled=True)
        self.read_builder(builder)

    def enable_trigger_once(self, builder: Builder) -> None:
        self.verify_builder_permissions(builder)
        self._update_trigger_once("builder-trigger-enable", field="disabled", value=False,
                                  service_account=BUILDER_SA, disabled=True)
        self.read_builder(builder)
