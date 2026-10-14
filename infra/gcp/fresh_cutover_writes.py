"""Fixed official write routes for the reviewed fresh HireWiz cutover.

No mutation at import. Invoke only within an approved, hard-owned operation.
Every mutation claims a durable attempt first; unknown outcomes never replay.
Credentials and vendor bodies remain RAM-only, including successful responses.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import fresh_source_authority as source_sql
import native_fresh_fence as native
from psycopg import sql
from sqlalchemy.engine import URL

from scripts import database_transfer as transfer
from scripts import legacy_credential_inventory as retained

PROJECT = "ai-resume-parser-482412"
NUMBER = "157225724590"
NEW_ACCOUNTS = ("hirewiz-runtime-20261014", "hirewiz-migrate-20261014", "hirewiz-observer-20261014")
OLD_PRINCIPALS = {
    "hirewiz-api@ai-resume-parser-482412.iam.gserviceaccount.com": "101596337064821972716",
    "ais-gemini-key-43d738d3dfb9485@226979001779.iam.gserviceaccount.com": "100592881791239522151",
    "ais-gemini-key-41af2f49a3fb4b6@157225724590.iam.gserviceaccount.com": "117611807420650799123",
    "ais-gemini-key-7aeb7c91129947c@157225724590.iam.gserviceaccount.com": "103651370799427077361",
    "157225724590-compute@developer.gserviceaccount.com": "115689643755778240830",
}
OLD_KEYS = frozenset(
    {
        "projects/226979001779/locations/global/keys/3b5f42c3-94ae-4695-a476-804dee61f243",
        "projects/157225724590/locations/global/keys/122de5ba-8ec6-498f-9e97-777188d18fcf",
        "projects/157225724590/locations/global/keys/4c39725c-a68a-44c7-8f5f-2c5ddfaa9ee4",
        "projects/43171513202/locations/global/keys/1e59173c-014d-4ea6-a659-58c56d0a5507",
    }
)
SOURCE_SECRET = "hirewiz-source-observer-db-20261014"
MODEL_SECRET = "hirewiz-model-isolated-20261014"
MODEL_KEY_ID = "hirewiz-model-isolated-20261014"
REDIS_SECRET = "hirewiz-rate-limit-storage-url"
POLICIES = {
    "expense": (
        "hirewiz-expense-policy-20261014",
        "docs/examples/expense-policy-estimated-reviewed-2026-10-09.json",
        "8ce31e98039c768340a1d6137d798e345e5c02c432b3d84d54331256828d1631",
    ),
    "model": (
        "hirewiz-model-cost-policy-20261014",
        "infra/gcp/model-cost-policy.candidate.json",
        "3560177fe3d84c5b18f0432429b5729988336170ce2c1dfd40cc2f56a12cdbdc",
    ),
}
TASKS_EMAIL = "hirewiz-tasks@ai-resume-parser-482412.iam.gserviceaccount.com"
TASKS_UID = "111393701271010610534"
TRIGGER_ID = "42c65704-7868-4af4-96b9-60879bb2d655"
TRIGGER_PATH = "projects/" + PROJECT + "/locations/us-central1/triggers/" + TRIGGER_ID
LAST_OPERATOR_BUILD = "bc25069a-8137-4d73-a0e5-8fa1fcae7f54"
QUEUES = (
    "hirewiz-analysis",
    "hirewiz-employer-ingestion",
    "hirewiz-employer-search",
    "hirewiz-employer-application",
)
BUCKET = "ai-resume-parser-482412-hirewiz-application-artifacts"
ARTIFACT_CONDITION = {
    "title": "hirewiz_customer_objects_only_20261014",
    "expression": "resource.type == 'storage.googleapis.com/Object' && resource.name.startsWith('projects/_/buckets/"
    + BUCKET
    + "/objects/') && !resource.name.startsWith('projects/_/buckets/"
    + BUCKET
    + "/objects/releases/')",
}
TARGET_HOST = "ep-weathered-tooth-b43oa1n6.c-6.us-east-2.aws.neon.tech"
TARGET_CLUSTER = "61273750d6782a6bc688488b47d274ce81589d8066d54f389e7773a834e7ccd7"
TARGET_ROLES = (
    ("hirewiz_runtime_20261013_r1", 24576, "hirewiz-fresh-runtime-db-20261013"),
    ("hirewiz_migrator_20261013_r1", 24578, "hirewiz-fresh-migrator-db-20261013"),
    ("hirewiz_observer_20261013_r1", 24580, "hirewiz-fresh-observer-db-20261013"),
)
SECRET_NAMES = frozenset(
    {
        SOURCE_SECRET,
        MODEL_SECRET,
        *(row[2] for row in TARGET_ROLES),
        *(row[0] for row in POLICIES.values()),
    }
)
ACCESS_SECRET_NAMES = SECRET_NAMES | {
    REDIS_SECRET,
    "hirewiz-analysis-task-token",
    "hirewiz-worker-jwt-bootstrap",
}
SECRET_ACCOUNT_IDS = {
    REDIS_SECRET: frozenset({NEW_ACCOUNTS[0]}),
    "hirewiz-analysis-task-token": frozenset({NEW_ACCOUNTS[0]}),
    "hirewiz-worker-jwt-bootstrap": frozenset({NEW_ACCOUNTS[0]}),
    **{row[0]: frozenset({NEW_ACCOUNTS[0]}) for row in POLICIES.values()},
    TARGET_ROLES[0][2]: frozenset({NEW_ACCOUNTS[0]}),
    TARGET_ROLES[1][2]: frozenset({NEW_ACCOUNTS[1]}),
    TARGET_ROLES[2][2]: frozenset({NEW_ACCOUNTS[2]}),
    SOURCE_SECRET: frozenset({NEW_ACCOUNTS[2]}),
    MODEL_SECRET: frozenset({NEW_ACCOUNTS[0]}),
}
LOCAL_PYTHON = Path(
    "/opt/homebrew/Cellar/python@3.14/3.14.6/Frameworks/Python.framework/Versions/3.14/bin/python3.14"
)
LOCAL_PYTHON_SHA256 = "4f00ea2ad53d62437a6a3946b73c73614a97e8accdc5b96dc095ea1a0d9c6a56"
LOCAL_HELPER_SHA256 = "3b52855e86b742fb4071c72e254cc672ccca8f58d1957903eff9e1a7fa80aedb"


class WriteDenied(RuntimeError):
    """Fixed refusal only. An uncertain mutation requires native reconciliation."""


def need(value: Any, code: str) -> None:
    if not value:
        raise WriteDenied(code)


@dataclass(frozen=True, slots=True)
class NativeAccount:
    email: str
    uid: str


class OfficialWrites:
    """Reuse the verified stock SDK normal manager without exporting credentials.

    Only this fixed application project's new identities/secrets and the exact
    old subjects are writable. Stock SDK refresh/challenge replay is retained;
    requested calls are not represented as an exact HTTP request count.
    """

    def __init__(self, boundary: native.NativeFreshFenceBoundary, workdir: Path):
        need(
            type(boundary) is native.NativeFreshFenceBoundary
            and type(boundary.reads) is native.SDKGoogleReads,
            "write_native_boundary_required",
        )
        self.boundary, self.workdir = boundary, workdir
        self.requested_calls = 0
        self.access_names = set(ACCESS_SECRET_NAMES)
        self.access_accounts = dict(SECRET_ACCOUNT_IDS)

    def _call(
        self,
        api: str,
        method: str,
        path: str,
        *,
        body: Any = None,
        query: dict[str, str] | None = None,
        allow_absent: bool = False,
    ) -> dict[str, Any] | None:
        origins = {
            "iam": "https://iam.googleapis.com/v1/",
            "keys": "https://apikeys.googleapis.com/v2/",
            "secret": "https://secretmanager.googleapis.com/v1/",
            "build": "https://cloudbuild.googleapis.com/v1/",
            "tasks": "https://cloudtasks.googleapis.com/v2/",
            "storage": "https://storage.googleapis.com/storage/v1/",
            "run": "https://run.googleapis.com/v2/",
        }
        new_emails = {name + "@" + PROJECT + ".iam.gserviceaccount.com" for name in NEW_ACCOUNTS}
        secret_resource = re.fullmatch(
            r"projects/"
            + NUMBER
            + r"/secrets/([a-z0-9-]+)(?::(addVersion|getIamPolicy|setIamPolicy))?",
            path,
        )
        allowed = (
            api == "run"
            and ((method == "GET" and path in {
                native.release.documents.RESOURCE,
                native.release.documents.RESOURCE + ":getIamPolicy",
                native.release.native_tex.RESOURCE,
                native.release.native_tex.RESOURCE + ":getIamPolicy",
            }) or (method == "POST" and path in {
                native.release.documents.RESOURCE + ":setIamPolicy",
                native.release.native_tex.RESOURCE + ":setIamPolicy",
            }))
            or
            api == "iam"
            and method == "POST"
            and path == "projects/" + PROJECT + "/serviceAccounts"
            or api == "iam"
            and method == "GET"
            and any(
                path == "projects/-/serviceAccounts/" + email
                for email in {*new_emails, *OLD_PRINCIPALS, TASKS_EMAIL}
            )
            or api == "iam"
            and method == "POST"
            and any(
                path == "projects/-/serviceAccounts/" + uid + ":disable"
                for uid in OLD_PRINCIPALS.values()
            )
            or api == "iam"
            and method == "POST"
            and path
            in {
                "projects/-/serviceAccounts/" + TASKS_UID + ":getIamPolicy",
                "projects/-/serviceAccounts/" + TASKS_UID + ":setIamPolicy",
            }
            or api == "tasks"
            and method == "GET"
            and path
            in {
                "projects/" + PROJECT + "/locations/us-central1/queues/" + queue for queue in QUEUES
            }
            or api == "tasks"
            and method == "POST"
            and path
            in {
                "projects/" + PROJECT + "/locations/us-central1/queues/" + queue + suffix
                for queue in QUEUES
                for suffix in (":getIamPolicy", ":setIamPolicy")
            }
            or api == "storage"
            and method == "GET"
            and path == "b/" + BUCKET
            or api == "storage"
            and method in {"GET", "PUT"}
            and path == "b/" + BUCKET + "/iam"
            or api == "keys"
            and method in {"GET", "DELETE"}
            and path in OLD_KEYS
            or api == "keys"
            and method == "GET"
            and re.fullmatch(r"operations/[a-zA-Z0-9._-]{1,200}", path)
            or api == "keys"
            and method == "POST"
            and path == "projects/" + NUMBER + "/locations/global/keys"
            or api == "keys"
            and method == "GET"
            and path == "keys:lookupKey"
            and query is not None
            and set(query) == {"keyString"}
            and native.key_string_envelope(query["keyString"])
            and not native.known_foreign_model_key(query["keyString"])
            or api == "keys"
            and method == "GET"
            and path
            in {
                "projects/" + NUMBER + "/locations/global/keys/" + MODEL_KEY_ID,
                "projects/" + NUMBER + "/locations/global/keys/" + MODEL_KEY_ID + "/keyString",
            }
            or api == "secret"
            and method == "POST"
            and path == "projects/" + NUMBER + "/secrets"
            or api == "secret"
            and secret_resource
            and secret_resource[1] in SECRET_NAMES
            and method in {"GET", "POST"}
            or api == "secret"
            and secret_resource
            and secret_resource[1] in self.access_names - SECRET_NAMES
            and (
                method == "GET"
                and secret_resource[2] == "getIamPolicy"
                or method == "POST"
                and secret_resource[2] == "setIamPolicy"
            )
            or api == "secret"
            and method == "GET"
            and path in {"projects/" + NUMBER + "/secrets/" + row[2] + "/versions"
                         for row in TARGET_ROLES}
            and query == {"pageSize": "100"}
            or api == "secret"
            and method in {"GET", "POST"}
            and re.fullmatch(
                r"projects/"
                + NUMBER
                + r"/secrets/"
                + TARGET_ROLES[1][2]
                + r"/versions/[1-9][0-9]{0,8}(?::disable)?",
                path,
            )
            or api == "build"
            and method == "GET"
            and path
            in {
                "projects/" + PROJECT + "/locations/us-central1/builds",
                "projects/" + PROJECT + "/locations/global/builds",
                "projects/" + PROJECT + "/locations/us-central1/builds/" + LAST_OPERATOR_BUILD,
                TRIGGER_PATH,
            }
        )
        need(
            allowed and api in origins and self.requested_calls < 256,
            "write_fixed_route_or_call_bound_refused",
        )
        self.requested_calls += 1
        url = origins[api] + path + ("?" + urlencode(query) if query else "")
        try:
            response, raw = self.boundary.reads._http().request(
                url,
                method=method,
                body=None if body is None else json.dumps(body, separators=(",", ":")).encode(),
                headers={"Cache-Control": "no-cache", "Content-Type": "application/json"},
                redirections=0,
            )
            status = int(response.status)
            if allow_absent and status == 404:
                return None
            need(
                status == 200 and type(raw) is bytes and len(raw) <= 4 * 1024 * 1024,
                "write_native_result_unknown_reconcile_only",
            )
            value = json.loads(raw, object_pairs_hook=native.object_pairs)
            need(type(value) is dict, "write_native_result_shape_refused")
            return value
        except WriteDenied:
            raise
        except Exception:
            raise WriteDenied("write_native_result_unknown_reconcile_only") from None

    def _once(self, phase: str) -> None:
        try:
            source_sql.once(self.workdir, phase)
        except Exception:
            raise WriteDenied("write_already_attempted_or_private_directory_refused") from None

    @staticmethod
    def _account(row: Any, email: str, uid: str | None = None) -> NativeAccount:
        need(
            type(row) is dict
            and row.get("email") == email
            and type(row.get("uniqueId")) is str
            and re.fullmatch(r"[1-9][0-9]{5,24}", row["uniqueId"])
            and (uid is None or row["uniqueId"] == uid),
            "write_native_account_identity_drift",
        )
        return NativeAccount(email, row["uniqueId"])

    def create_accounts(self) -> tuple[NativeAccount, ...]:
        deadline = time.monotonic() + 55
        emails = [name + "@" + PROJECT + ".iam.gserviceaccount.com" for name in NEW_ACCOUNTS]
        for email in emails:
            need(
                self._call("iam", "GET", "projects/-/serviceAccounts/" + email, allow_absent=True)
                is None,
                "write_new_account_exists_reconcile_only",
            )
        accounts = []
        for name, email in zip(NEW_ACCOUNTS, emails, strict=True):
            need(time.monotonic() < deadline, "write_action_deadline")
            self._once("create-" + name)
            row = self._call(
                "iam",
                "POST",
                "projects/" + PROJECT + "/serviceAccounts",
                body={"accountId": name, "serviceAccount": {"displayName": name}},
            )
            account = self._account(row, email)
            observed = self._call("iam", "GET", "projects/-/serviceAccounts/" + email)
            need(
                observed is not None
                and observed.get("projectId") == PROJECT
                and observed.get("disabled", False) is False,
                "write_new_account_state_drift",
            )
            need(
                self._account(observed, email, account.uid) == account,
                "write_new_account_uid_drift",
            )
            accounts.append(account)
        need(time.monotonic() < deadline, "write_action_deadline")
        return tuple(accounts)

    def verify_prepared_owner_resume_pristine(self) -> None:
        """Read-only prerequisite for resuming an existing NOLOGIN owner.

        SQL independently verifies the exact owner/target and unchanged source.
        Here native absence/UID/state checks exclude prior cloud isolation,
        replacement issuance and rotated target versions. No supplied flag or
        historic receipt substitutes for these current GETs.
        """
        scope = self.boundary.scope
        need(scope is not None and self.boundary.retired_since is None,
             "write_owner_resume_scope_not_pristine")
        assert scope is not None
        need({principal.email: principal.uid for principal in scope.principals} == OLD_PRINCIPALS
             and {key.name for key in scope.keys} == OLD_KEYS,
             "write_owner_resume_captured_authority_incomplete")
        need(set(self.boundary._setup_passwords) == set(native.SETUP_REFERENCES)
             and (scope.target.runtime_secret, scope.target.migration_secret,
                  scope.target.observer_secret) == tuple(native.SETUP_REFERENCES.values()),
             "write_owner_resume_target_setup_not_pristine")
        deadline = time.monotonic() + 30
        for principal in scope.principals:
            need(time.monotonic() < deadline, "write_owner_resume_observation_expired")
            row = self._call("iam", "GET", "projects/-/serviceAccounts/" + principal.email)
            self._account(row, principal.email, principal.uid)
            need(row is not None and row.get("disabled", False) is False,
                 "write_owner_resume_old_principal_not_enabled")
        keys = self.boundary._module("backend/scripts/model_key_revocation_observer.py",
                                     "owner_resume_native_keys")
        for key in scope.keys:
            row = self._call("keys", "GET", key.name)
            metadata = keys._metadata(row, key.name.rsplit("/keys/", 1)[0], datetime.now(UTC))
            need(metadata["uid"] == key.uid and metadata["delete_time"] is None
                 and row is not None and (row.get("serviceAccountEmail") or None) == key.issuer,
                 "write_owner_resume_old_key_not_active")
        for name in NEW_ACCOUNTS:
            email = name + "@" + PROJECT + ".iam.gserviceaccount.com"
            need(self._call("iam", "GET", "projects/-/serviceAccounts/" + email,
                            allow_absent=True) is None,
                 "write_owner_resume_replacement_account_exists")
        for api, path in (
            ("keys", f"projects/{NUMBER}/locations/global/keys/{MODEL_KEY_ID}"),
            ("secret", f"projects/{NUMBER}/secrets/{MODEL_SECRET}"),
            ("secret", f"projects/{NUMBER}/secrets/{SOURCE_SECRET}"),
        ):
            need(self._call(api, "GET", path, allow_absent=True) is None,
                 "write_owner_resume_replacement_issuance_exists")
        for _, _, name in TARGET_ROLES:
            parent = f"projects/{NUMBER}/secrets/{name}/versions"
            listing = self._call("secret", "GET", parent, query={"pageSize": "100"})
            need(listing is not None and listing.get("nextPageToken") in (None, "")
                 and type(listing.get("versions")) is list and len(listing["versions"]) == 1,
                 "write_owner_resume_target_versions_not_pristine")
            row = listing["versions"][0]
            need(type(row) is dict and row.get("name") == parent + "/1"
                 and row.get("state") == "ENABLED",
                 "write_owner_resume_target_versions_not_pristine")
        need(time.monotonic() < deadline, "write_owner_resume_observation_expired")

    def verify_trigger_held(self) -> None:
        """Fresh exact trigger read; a caller declaration cannot retire Compute."""
        row = self._call("build", "GET", TRIGGER_PATH)
        need(type(row) is dict, "write_trigger_scope_missing")
        assert row is not None
        github = row.get("github")
        need(
            row.get("id") == TRIGGER_ID
            and row.get("resourceName") == TRIGGER_PATH
            and row.get("name") == "Trigger-for-github-updates"
            and row.get("filename") == "cloudbuild.yaml"
            and row.get("disabled") is True
            and row.get("serviceAccount")
            == "projects/"
            + PROJECT
            + "/serviceAccounts/157225724590-compute@developer.gserviceaccount.com"
            and type(github) is dict
            and github.get("owner") == "Harshil4442"
            and github.get("name") == "ai-resume-copilot"
            and type(github.get("push")) is dict
            and github["push"].get("branch") == "^main$"
            and github["push"].get("invertRegex", False) is False
            and "tag" not in github["push"]
            and "pullRequest" not in github,
            "write_trigger_not_exactly_held_before_compute_retirement",
        )

    def _no_live_builds(self) -> None:
        # The default Compute identity must remain usable through its last build.
        # A partial page, unknown state or inaccessible location is a refusal.
        last = self._call(
            "build",
            "GET",
            "projects/" + PROJECT + "/locations/us-central1/builds/" + LAST_OPERATOR_BUILD,
        )
        need(
            type(last) is dict
            and last.get("id") == LAST_OPERATOR_BUILD
            and last.get("projectId") == PROJECT
            and last.get("name")
            == "projects/" + NUMBER + "/locations/us-central1/builds/" + LAST_OPERATOR_BUILD
            and last.get("serviceAccount")
            == "projects/"
            + PROJECT
            + "/serviceAccounts/157225724590-compute@developer.gserviceaccount.com"
            and last.get("status")
            in {"SUCCESS", "FAILURE", "INTERNAL_ERROR", "TIMEOUT", "CANCELLED", "EXPIRED"},
            "write_last_operator_build_not_terminal",
        )
        for location in ("us-central1", "global"):
            row = self._call(
                "build",
                "GET",
                f"projects/{PROJECT}/locations/{location}/builds",
                query={
                    "pageSize": "100",
                    "filter": 'status!="SUCCESS" AND status!="FAILURE" AND status!="INTERNAL_ERROR" AND status!="TIMEOUT" AND status!="CANCELLED" AND status!="EXPIRED"',
                },
            )
            need(
                row is not None and not row.get("nextPageToken") and not row.get("unreachable"),
                "write_build_inventory_partial",
            )
            assert row is not None
            builds = row.get("builds", [])
            need(type(builds) is list and builds == [], "write_live_builds_prevent_compute_disable")

    def disable_old_principals(self) -> tuple[NativeAccount, ...]:
        scope = self.boundary.scope
        need(scope is not None and scope.principals, "write_authenticated_old_scope_missing")
        self.boundary.verify_foreign_model_retirement()
        assert scope is not None
        need(
            all(
                OLD_PRINCIPALS.get(principal.email) == principal.uid
                for principal in scope.principals
            ),
            "write_old_principal_unmapped",
        )
        deadline = time.monotonic() + 55
        self.verify_trigger_held()
        self._no_live_builds()
        self.boundary.verify_admission_closed()
        disabled = []
        for principal in scope.principals:
            need(time.monotonic() < deadline, "write_action_deadline")
            path = "projects/-/serviceAccounts/" + principal.email
            row = self._call("iam", "GET", path)
            account = self._account(row, principal.email, principal.uid)
            need(
                row is not None and row.get("disabled", False) is False,
                "write_old_principal_already_disabled_reconcile_only",
            )
            if principal.uid == "115689643755778240830":
                # Reobserve immediately before this actor's mutation; previous
                # disables do not establish that no new Build was submitted.
                self.verify_trigger_held()
                self._no_live_builds()
            self._once("disable-" + hashlib.sha256(principal.email.encode()).hexdigest()[:16])
            self._call(
                "iam", "POST", "projects/-/serviceAccounts/" + account.uid + ":disable", body={}
            )
            observed = self._call("iam", "GET", path)
            self._account(observed, account.email, account.uid)
            need(
                observed is not None and observed.get("disabled") is True,
                "write_old_principal_disable_unconfirmed",
            )
            disabled.append(account)
        need(time.monotonic() < deadline, "write_action_deadline")
        return tuple(disabled)

    def _operation(
        self, acknowledgement: Any, *, resource: str, uid: str | None, deadline: float
    ) -> dict[str, Any]:
        need(
            type(acknowledgement) is dict
            and type(acknowledgement.get("name")) is str
            and re.fullmatch(r"operations/[a-zA-Z0-9._-]{1,200}", acknowledgement["name"]),
            "write_operation_ack_unknown_reconcile_only",
        )
        name = acknowledgement["name"]
        row = acknowledgement
        for _ in range(16):
            need(time.monotonic() < deadline, "write_operation_unknown_reconcile_only")
            need(
                row.get("name") == name and type(row.get("done", False)) is bool,
                "write_operation_identity_drift",
            )
            if row.get("done") is True:
                result = row.get("response")
                need(
                    not row.get("error")
                    and type(result) is dict
                    and result.get("name") == resource
                    and (uid is None or result.get("uid") == uid),
                    "write_operation_failed_or_resource_drift",
                )
                return result
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))
            value = self._call("keys", "GET", name)
            need(value is not None, "write_operation_unavailable")
            row = value
        raise WriteDenied("write_operation_unknown_reconcile_only")

    def delete_old_model_keys(self) -> tuple[str, ...]:
        scope = self.boundary.scope
        need(scope is not None and scope.keys, "write_authenticated_old_scope_missing")
        self.boundary.verify_foreign_model_retirement()
        assert scope is not None
        need(all(key.name in OLD_KEYS for key in scope.keys), "write_old_key_unmapped")
        deadline = time.monotonic() + 55
        completed = []
        for key in scope.keys:
            need(time.monotonic() < deadline, "write_action_deadline")
            before = self._call("keys", "GET", key.name)
            need(
                before is not None
                and before.get("name") == key.name
                and before.get("uid") == key.uid
                and (before.get("serviceAccountEmail") or None) == key.issuer
                and not before.get("deleteTime")
                and type(before.get("etag")) is str
                and bool(before["etag"]),
                "write_old_key_uid_issuer_or_state_drift",
            )
            assert before is not None
            self._once("delete-key-" + hashlib.sha256(key.name.encode()).hexdigest()[:16])
            ack = self._call("keys", "DELETE", key.name, query={"etag": before["etag"]})
            self._operation(ack, resource=key.name, uid=key.uid, deadline=deadline)
            observed = self._call("keys", "GET", key.name)
            need(
                observed is not None
                and observed.get("name") == key.name
                and observed.get("uid") == key.uid
                and (observed.get("serviceAccountEmail") or None) == key.issuer
                and observed.get("deleteTime"),
                "write_old_key_delete_unconfirmed",
            )
            completed.append(key.name)
        need(time.monotonic() < deadline, "write_action_deadline")
        return tuple(completed)

    def create_secret(self, name: str) -> str:
        need(
            name in {SOURCE_SECRET, MODEL_SECRET, *(row[0] for row in POLICIES.values())},
            "write_new_secret_name_refused",
        )
        path = f"projects/{NUMBER}/secrets/{name}"
        need(
            self._call("secret", "GET", path, allow_absent=True) is None,
            "write_new_secret_exists_reconcile_only",
        )
        self._once("create-secret-" + name)
        row = self._call(
            "secret",
            "POST",
            "projects/" + NUMBER + "/secrets",
            query={"secretId": name},
            body={"replication": {"automatic": {}}},
        )
        need(row is not None and row.get("name") == path, "write_new_secret_identity_unknown")
        observed = self._call("secret", "GET", path)
        need(
            observed is not None
            and observed.get("name") == path
            and observed.get("replication") == {"automatic": {}},
            "write_new_secret_readback_drift",
        )
        return path

    def add_version(self, name: str, value: str, *, phase: str) -> str:
        need(
            name in SECRET_NAMES
            and type(value) is str
            and 1 <= len(value.encode()) <= 65536
            and re.fullmatch(r"[a-z0-9-]{1,80}", phase),
            "write_secret_payload_or_name_refused",
        )
        data = value.encode()
        self._once("version-" + phase)
        row = self._call(
            "secret",
            "POST",
            f"projects/{NUMBER}/secrets/{name}:addVersion",
            body={
                "payload": {
                    "data": base64.b64encode(data).decode(),
                    "dataCrc32c": str(retained._crc32c(data)),
                }
            },
        )
        prefix = f"projects/{NUMBER}/secrets/{name}/versions/"
        need(
            row is not None
            and type(row.get("name")) is str
            and row["name"].startswith(prefix)
            and re.fullmatch(r"[1-9][0-9]{0,8}", row["name"][len(prefix) :])
            and row.get("state") == "ENABLED",
            "write_secret_version_identity_unknown",
        )
        assert row is not None
        version = row["name"][len(prefix) :]
        state, observed = retained._secret_value(self.boundary.reads, row["name"])
        need(
            state == "ENABLED" and type(observed) is str and hmac.compare_digest(observed, value),
            "write_secret_version_readback_mismatch",
        )
        return name + ":" + version

    def grant_secret_access(self, name: str, accounts: tuple[NativeAccount, ...]) -> None:
        # Only accessor on exact new database/model sources or the existing Redis
        # secret. Redis payload/version methods remain unavailable here; its
        # existing value and existing principal bindings are preserved.
        need(
            name in self.access_names
            and 1 <= len(accounts) <= 3
            and len({a.email for a in accounts}) == len(accounts),
            "write_secret_grant_scope_refused",
        )
        members = []
        for account in accounts:
            need(
                account.email
                in {
                    n + "@" + PROJECT + ".iam.gserviceaccount.com"
                    for n in self.access_accounts[name]
                },
                "write_new_account_name_refused",
            )
            row = self._call("iam", "GET", "projects/-/serviceAccounts/" + account.email)
            self._account(row, account.email, account.uid)
            members.append("serviceAccount:" + account.email)
        path = f"projects/{NUMBER}/secrets/{name}"
        before = self._call(
            "secret", "GET", path + ":getIamPolicy", query={"options.requestedPolicyVersion": "3"}
        )
        need(
            before is not None and type(before.get("etag")) is str and bool(before["etag"]),
            "write_secret_policy_etag_missing",
        )
        assert before is not None
        bindings = before.get("bindings", [])
        need(
            type(bindings) is list
            and len(bindings) <= 64
            and all(type(b) is dict for b in bindings),
            "write_secret_policy_shape_refused",
        )
        modified = json.loads(json.dumps(before))
        access = [
            binding
            for binding in modified.setdefault("bindings", [])
            if binding.get("role") == "roles/secretmanager.secretAccessor"
            and not binding.get("condition")
        ]
        need(len(access) <= 1, "write_secret_duplicate_unconditional_binding")
        if access:
            need(
                type(access[0].get("members")) is list
                and all(type(member) is str for member in access[0]["members"]),
                "write_secret_binding_members_shape",
            )
            access[0]["members"] = sorted(set(access[0]["members"]) | set(members))
        else:
            modified["bindings"].append(
                {"role": "roles/secretmanager.secretAccessor", "members": sorted(members)}
            )
        self._once("grant-secret-" + name)
        self._call("secret", "POST", path + ":setIamPolicy", body={"policy": modified})
        observed = self._call(
            "secret", "GET", path + ":getIamPolicy", query={"options.requestedPolicyVersion": "3"}
        )

        def canonical(bindings: Any) -> list[str]:
            need(
                type(bindings) is list and all(type(binding) is dict for binding in bindings),
                "write_secret_policy_shape_refused",
            )
            normalized = []
            for binding in bindings:
                copy = dict(binding)
                need(
                    type(copy.get("members")) is list
                    and all(type(member) is str for member in copy["members"]),
                    "write_secret_binding_members_shape",
                )
                copy["members"] = sorted(copy["members"])
                normalized.append(json.dumps(copy, sort_keys=True, separators=(",", ":")))
            return sorted(normalized)

        need(
            observed is not None
            and canonical(observed.get("bindings", [])) == canonical(modified["bindings"]),
            "write_secret_policy_readback_drift",
        )

    def grant_attested_policy_sources(
        self, source: native.release.Plan, runtime: NativeAccount
    ) -> None:
        # The existing native CI/Git/image and actual numeric policy payload/hash
        # checks run before adding these two exact names to the IAM-only route.
        source.validate()
        self.boundary.verify_identity_and_policy(source)
        for reference in (source.expense_secret, source.model_policy_secret):
            name = reference.split(":")[0]
            need(name in {row[0] for row in POLICIES.values()}, "write_policy_secret_class_overlap")
            self.access_names.add(name)
            self.access_accounts[name] = frozenset({NEW_ACCOUNTS[0]})
            self.grant_secret_access(name, (runtime,))

    def _grant_resource(
        self,
        api: str,
        resource: str,
        role: str,
        member: str,
        *,
        condition: dict[str, str] | None = None,
    ) -> None:
        get_method = "GET" if api == "storage" else "POST"
        get_path = resource if api == "storage" else resource + ":getIamPolicy"
        set_path = resource if api == "storage" else resource + ":setIamPolicy"
        query = (
            {"optionsRequestedPolicyVersion": "3"}
            if api == "storage"
            else {"options.requestedPolicyVersion": "3"}
            if api == "iam"
            else None
        )
        get_body = {"options": {"requestedPolicyVersion": 3}} if api == "tasks" else None
        before = self._call(api, get_method, get_path, query=query, body=get_body)
        need(
            before is not None
            and type(before.get("etag")) is str
            and bool(before["etag"])
            and type(before.get("bindings", [])) is list
            and len(before.get("bindings", [])) <= 64,
            "write_resource_policy_shape_or_etag",
        )
        assert before is not None
        modified = json.loads(json.dumps(before))
        modified["version"] = 3
        bindings = modified.setdefault("bindings", [])
        need(
            all(
                type(binding) is dict
                and type(binding.get("members")) is list
                and len(binding["members"]) <= 256
                and all(type(value) is str for value in binding["members"])
                for binding in bindings
            ),
            "write_resource_binding_shape",
        )
        matching = [
            binding
            for binding in bindings
            if binding.get("role") == role and binding.get("condition") == condition
        ]
        need(len(matching) <= 1, "write_resource_duplicate_binding")
        if matching:
            matching[0]["members"] = sorted(set(matching[0]["members"]) | {member})
        else:
            binding: dict[str, Any] = {"role": role, "members": [member]}
            if condition is not None:
                binding["condition"] = condition
            bindings.append(binding)
        self._once("grant-resource-" + hashlib.sha256((api + resource).encode()).hexdigest()[:16])
        self._call(
            api,
            "PUT" if api == "storage" else "POST",
            set_path,
            body=modified if api == "storage" else {"policy": modified},
        )
        after = self._call(api, get_method, get_path, query=query, body=get_body)

        def canonical(value: Any) -> list[str]:
            need(
                type(value) is list
                and all(type(row) is dict and type(row.get("members")) is list for row in value),
                "write_resource_policy_readback_shape",
            )
            return sorted(
                json.dumps(
                    {**row, "members": sorted(row["members"])},
                    sort_keys=True,
                    separators=(",", ":"),
                )
                for row in value
            )

        need(
            after is not None
            and (
                after.get("version") == 3
                if any(binding.get("condition") for binding in bindings)
                else after.get("version") is None
                or type(after.get("version")) is int
                and after["version"] in {0, 1, 3}
            )
            and canonical(after.get("bindings", [])) == canonical(bindings),
            "write_resource_policy_readback_drift",
        )

    def grant_runtime_resources(self, runtime: NativeAccount) -> None:
        need(
            runtime.email == NEW_ACCOUNTS[0] + "@" + PROJECT + ".iam.gserviceaccount.com",
            "write_runtime_account_class",
        )
        self._account(
            self._call("iam", "GET", "projects/-/serviceAccounts/" + runtime.email),
            runtime.email,
            runtime.uid,
        )
        self.grant_document_invoker(runtime)
        self.grant_native_tex_invoker(runtime)
        task = self._call("iam", "GET", "projects/-/serviceAccounts/" + TASKS_EMAIL)
        self._account(task, TASKS_EMAIL, TASKS_UID)
        need(
            task is not None and task.get("disabled", False) is False,
            "write_tasks_identity_disabled",
        )
        for name in ("hirewiz-analysis-task-token", "hirewiz-worker-jwt-bootstrap"):
            self.grant_secret_access(name, (runtime,))
        member = "serviceAccount:" + runtime.email
        self._grant_resource(
            "iam", "projects/-/serviceAccounts/" + TASKS_UID, "roles/iam.serviceAccountUser", member
        )
        for queue in QUEUES:
            path = "projects/" + PROJECT + "/locations/us-central1/queues/" + queue
            native_queue = self._call("tasks", "GET", path)
            need(
                native_queue is not None
                and native_queue.get("name") == path
                and native_queue.get("state") == "PAUSED",
                "write_runtime_queue_scope_or_state_drift",
            )
            self._grant_resource("tasks", path, "roles/cloudtasks.enqueuer", member)
        bucket = self._call("storage", "GET", "b/" + BUCKET)
        need(
            bucket is not None
            and bucket.get("name") == BUCKET
            and str(bucket.get("projectNumber")) == NUMBER
            and bucket.get("iamConfiguration", {})
            .get("uniformBucketLevelAccess", {})
            .get("enabled")
            is True,
            "write_artifact_bucket_identity_or_uniform_access",
        )
        self._grant_resource(
            "storage",
            "b/" + BUCKET + "/iam",
            "roles/storage.objectUser",
            member,
            condition=ARTIFACT_CONDITION,
        )

    def grant_document_invoker(self, runtime: NativeAccount) -> None:
        """Only the isolated replacement runtime may invoke the fixed worker."""
        documents = native.release.documents
        need(runtime.email == documents.API_INVOKER, "write_document_runtime_identity")
        source_sql.verify_cloud_isolated(self.boundary)
        account = self._call("iam", "GET", "projects/-/serviceAccounts/" + runtime.email)
        self._account(account, runtime.email, runtime.uid)
        need(account is not None and account.get("disabled", False) is False,
             "write_document_runtime_disabled")
        service = self._call("run", "GET", documents.RESOURCE)
        need(service is not None and service.get("name") == documents.RESOURCE
             and service.get("uri") == documents.ACCEPTED_ORIGIN
             and type(service.get("uid")) is str and bool(service["uid"])
             and service.get("reconciling", False) is False,
             "write_document_service_identity")
        assert service is not None
        path = documents.RESOURCE
        before = self._call("run", "GET", path + ":getIamPolicy",
                            query={"options.requestedPolicyVersion": "3"})
        retired_email = "hirewiz-api@" + PROJECT + ".iam.gserviceaccount.com"
        old_binding = [{"role": "roles/run.invoker", "members": ["serviceAccount:" + retired_email]}]
        wanted = [{"role": "roles/run.invoker", "members": ["serviceAccount:" + runtime.email]}]
        need(before is not None and set(before) <= {"version", "etag", "bindings"}
             and type(before.get("etag")) is str and bool(before["etag"])
             and type(before.get("version", 1)) is int and before.get("version", 1) in {1, 3}
             and before.get("bindings", []) in ([], old_binding, wanted),
             "write_document_iam_initial_policy_refused")
        assert before is not None
        if before.get("bindings", []) == wanted:
            # A genuine current desired policy is read-only reconciliation. Do
            # not replay a prior grant or overwrite its current etag.
            reread = self._call("run", "GET", documents.RESOURCE)
            need(reread is not None and reread.get("name") == documents.RESOURCE
                 and reread.get("uid") == service["uid"]
                 and reread.get("uri") == documents.ACCEPTED_ORIGIN,
                 "write_document_service_identity_drift")
            return
        policy = {"version": 3, "etag": before["etag"], "bindings": wanted}
        self._once("grant-document-runtime")
        self._call("run", "POST", path + ":setIamPolicy", body={"policy": policy})
        after = self._call("run", "GET", path + ":getIamPolicy",
                           query={"options.requestedPolicyVersion": "3"})
        need(after is not None and set(after) <= {"version", "etag", "bindings"}
             and type(after.get("etag")) is str and bool(after["etag"])
             and type(after.get("version", 1)) is int and after.get("version", 1) in {1, 3}
             and after.get("bindings", []) == wanted,
             "write_document_iam_readback_drift")
        reread = self._call("run", "GET", documents.RESOURCE)
        need(reread is not None and reread.get("name") == documents.RESOURCE
             and reread.get("uid") == service["uid"]
             and reread.get("uri") == documents.ACCEPTED_ORIGIN,
             "write_document_service_identity_drift")

    def grant_native_tex_invoker(self, runtime: NativeAccount) -> None:
        """The accepted TeX service admits only the isolated new runtime."""
        tex = native.release.native_tex
        origin, image = getattr(tex, "ACCEPTED_ORIGIN", ""), getattr(tex, "ACCEPTED_IMAGE", "")
        try:
            tex.api_environment(origin, image)
        except ValueError:
            raise WriteDenied("write_native_tex_accepted_binding_missing") from None
        need(runtime.email == tex.INVOKER, "write_native_tex_runtime_identity")
        source_sql.verify_cloud_isolated(self.boundary)
        account = self._call("iam", "GET", "projects/-/serviceAccounts/" + runtime.email)
        self._account(account, runtime.email, runtime.uid)
        need(account is not None and account.get("disabled", False) is False,
             "write_native_tex_runtime_disabled")
        service = self._call("run", "GET", tex.RESOURCE)
        need(service is not None and service.get("name") == tex.RESOURCE
             and service.get("uri") == origin and type(service.get("uid")) is str
             and bool(service["uid"]) and service.get("reconciling", False) is False,
             "write_native_tex_service_identity")
        assert service is not None
        path = tex.RESOURCE
        before = self._call("run", "GET", path + ":getIamPolicy",
                            query={"options.requestedPolicyVersion": "3"})
        wanted = [{"role": "roles/run.invoker", "members": ["serviceAccount:" + runtime.email]}]
        need(before is not None and set(before) <= {"version", "etag", "bindings"}
             and type(before.get("etag")) is str and bool(before["etag"])
             and type(before.get("version", 1)) is int and before.get("version", 1) in {1, 3}
             and before.get("bindings", []) in ([], wanted),
             "write_native_tex_iam_initial_policy_refused")
        assert before is not None
        if before.get("bindings", []) != wanted:
            self._once("grant-native-tex-runtime")
            self._call("run", "POST", path + ":setIamPolicy", body={"policy": {
                "version": 3, "etag": before["etag"], "bindings": wanted,
            }})
            after = self._call("run", "GET", path + ":getIamPolicy",
                               query={"options.requestedPolicyVersion": "3"})
            need(after is not None and set(after) <= {"version", "etag", "bindings"}
                 and type(after.get("etag")) is str and bool(after["etag"])
                 and type(after.get("version", 1)) is int and after.get("version", 1) in {1, 3}
                 and after.get("bindings", []) == wanted,
                 "write_native_tex_iam_readback_drift")
        reread = self._call("run", "GET", path)
        need(reread is not None and reread.get("name") == path
             and reread.get("uid") == service["uid"] and reread.get("uri") == origin,
             "write_native_tex_service_identity_drift")

    def create_model_replacement(self, runtime: NativeAccount) -> str:
        source_sql.verify_cloud_isolated(self.boundary)
        cut = self.boundary.retired_since
        need(cut is not None, "write_native_isolation_cut_required")
        assert cut is not None
        email = NEW_ACCOUNTS[0] + "@" + PROJECT + ".iam.gserviceaccount.com"
        need(type(runtime) is NativeAccount and runtime.email == email,
             "write_new_model_issuer_class_refused")
        account_path = "projects/-/serviceAccounts/" + runtime.email
        account = self._call("iam", "GET", account_path)
        need(self._account(account, email, runtime.uid) == runtime
             and account is not None and account.get("projectId") == PROJECT
             and account.get("disabled", False) is False,
             "write_new_model_issuer_native_state_drift")
        path = f"projects/{NUMBER}/locations/global/keys/{MODEL_KEY_ID}"
        need(
            self._call("keys", "GET", path, allow_absent=True) is None,
            "write_new_model_key_exists_reconcile_only",
        )
        deadline = time.monotonic() + 55
        self._once("create-model-" + MODEL_KEY_ID)
        ack = self._call(
            "keys",
            "POST",
            f"projects/{NUMBER}/locations/global/keys",
            query={"keyId": MODEL_KEY_ID},
            body={
                "displayName": MODEL_KEY_ID,
                "restrictions": {"apiTargets": [{"service": "generativelanguage.googleapis.com"}]},
                "serviceAccountEmail": runtime.email,
            },
        )
        result = self._operation(ack, resource=path, uid=None, deadline=deadline)
        observed = self._call("keys", "GET", path)
        need(
            observed is not None
            and observed.get("name") == path
            and observed.get("uid") == result.get("uid")
            and type(observed.get("uid")) is str
            and not observed.get("deleteTime")
            and observed.get("serviceAccountEmail") == runtime.email
            and observed.get("restrictions")
            == {"apiTargets": [{"service": "generativelanguage.googleapis.com"}]},
            "write_new_model_key_readback_drift",
        )
        assert observed is not None
        try:
            created = datetime.fromisoformat(
                str(observed.get("createTime", "")).replace("Z", "+00:00")
            )
        except (ValueError, TypeError):
            raise WriteDenied("write_new_model_key_timestamp_unavailable") from None
        need(
            created.tzinfo is not None and cut <= created <= datetime.now(UTC),
            "write_new_model_key_precedes_isolation",
        )
        key = self._call("keys", "GET", path + "/keyString")
        need(
            key is not None
            and native.key_string_envelope(key.get("keyString"))
            and not native.known_foreign_model_key(key.get("keyString")),
            "write_new_model_key_value_unavailable",
        )
        assert key is not None
        lookup = self._call("keys", "GET", "keys:lookupKey",
                            query={"keyString": key["keyString"]})
        need(lookup is not None and lookup.get("name") == path
             and lookup.get("parent") == path.rsplit("/keys/", 1)[0],
             "write_new_model_key_native_value_unmapped")
        current = self._call("iam", "GET", account_path)
        need(self._account(current, email, runtime.uid) == runtime
             and current is not None and current.get("disabled", False) is False,
             "write_new_model_issuer_native_state_drift")
        self.create_secret(MODEL_SECRET)
        return self.add_version(MODEL_SECRET, key["keyString"], phase="post-isolation-model")


def secret_dsn(identity: transfer.NativeDatabaseIdentity, password: str) -> str:
    identity.validate()
    return URL.create(
        "postgresql",
        username=identity.actor,
        password=password,
        host=identity.host,
        port=identity.port,
        database=identity.database,
        query={"sslmode": "verify-full", "channel_binding": "prefer"},
    ).render_as_string(hide_password=False)


def publish_policies(writes: OfficialWrites, root: Path) -> dict[str, str]:
    """Only the already reviewed public bytes; no automatic expiry renewal."""
    values = {}
    for alias, (_name, relative, digest) in POLICIES.items():
        path = root / relative
        info = path.lstat()
        need(
            stat.S_ISREG(info.st_mode) and 0 < info.st_size <= 65536,
            "write_public_policy_file_refused",
        )
        raw = path.read_bytes()
        need(hashlib.sha256(raw).hexdigest() == digest, "write_public_policy_pin_mismatch")
        try:
            values[alias] = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise WriteDenied("write_public_policy_encoding_refused") from None
    references = {}
    for alias, (name, _, _) in POLICIES.items():
        writes.create_secret(name)
        references[alias] = writes.add_version(
            name, values[alias], phase="publish-" + alias + "-policy"
        )
    return references


def publish_source_observer(
    writes: OfficialWrites, origin: transfer.BoundDatabase, passwords: source_sql.SourcePasswords
) -> str:
    source_sql.validate_origin(origin)
    need(
        type(passwords) is source_sql.SourcePasswords
        and type(passwords.observer) is str
        and re.fullmatch(r"[A-Za-z0-9_-]{64}", passwords.observer),
        "write_source_password_shape",
    )
    identity = transfer.NativeDatabaseIdentity(
        source_sql.SOURCE_HOST,
        5432,
        "neondb",
        source_sql.SOURCE_CLUSTER,
        source_sql.DATABASE_OID,
        source_sql.OBSERVER_NAME,
        source_sql.OBSERVER_OID,
        origin.identity.root_certificate,
    )
    writes.create_secret(SOURCE_SECRET)
    return writes.add_version(
        SOURCE_SECRET, secret_dsn(identity, passwords.observer), phase="source-observer-initial"
    )


def delete_source_role_local(
    observer: transfer.BoundDatabase, owner: source_sql.PreparedOwner, workdir: Path
) -> None:
    """Native SQL checks surround the fixed opaque local normal-manager action.

    It changes neither Google's credentials nor Linux's manager. A successful
    subprocess result alone never admits retirement; fresh native SQL must pass.
    """
    need(sys.platform == "darwin", "write_local_neon_host_required")
    helper = Path(__file__).with_name("local_neon_source_delete.py")
    for path, digest in ((LOCAL_PYTHON, LOCAL_PYTHON_SHA256), (helper, LOCAL_HELPER_SHA256)):
        info = path.lstat()
        need(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.getuid()
            and not info.st_mode & 0o022
            and 0 < info.st_size <= 1048576
            and hashlib.sha256(path.read_bytes()).hexdigest() == digest,
            "write_local_neon_public_runtime_pin_refused",
        )
    source_sql.verify_provider_delete_ready(observer, owner)
    try:
        result = subprocess.run(
            [str(LOCAL_PYTHON), "-I", "-B", str(helper), "--workdir", str(workdir)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=60,
            check=False,
            env={
                "PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                "HOME": "/Users/harshil",
                "LANG": "C",
                "LC_ALL": "C",
            },
        )
        need(
            result.returncode == 0 and 0 < len(result.stdout) <= 4096,
            "write_local_neon_action_unknown_reconcile_only",
        )
        value = json.loads(result.stdout, object_pairs_hook=native.object_pairs)
        need(
            type(value) is dict
            and value.get("status") == "local_normal_neon_delete_completed_requires_sql_absence"
            and value.get("project_id") == "little-pond-36610622"
            and value.get("branch_id") == "br-fancy-sunset-ao8i4a57"
            and value.get("actor_id") == "c9f4043f-260d-402d-8afe-10b644fc72bf"
            and value.get("source_sql_absence_verified") is False
            and value.get("native_fence_complete") is False,
            "write_local_neon_result_join_refused",
        )
    except WriteDenied:
        raise
    except Exception:
        raise WriteDenied("write_local_neon_action_unknown_reconcile_only") from None
    source_sql.verify_provider_delete_complete(observer, owner)


def rotate_source_observer_after_delete(
    writes: OfficialWrites, observer: transfer.BoundDatabase, owner: source_sql.PreparedOwner
) -> tuple[str, transfer.BoundDatabase]:
    """Expire the barrier password after native old-role absence, without DML."""
    need(
        not os.path.lexists(writes.workdir / "rotate-source-observer-password.started.json"),
        "write_source_observer_rotation_already_attempted_reconcile_only",
    )
    source_sql.verify_provider_delete_complete(observer, owner)
    old = (
        *writes.boundary._source_old_passwords,
        *writes.boundary._setup_passwords.values(),
        observer.password,
    )
    password = secrets.token_urlsafe(48)
    need(
        all(not hmac.compare_digest(password.encode(), value.encode()) for value in old),
        "write_source_password_reuses_precut_value",
    )
    deadline = time.monotonic() + 55
    connection = observer.connect()
    try:
        reference = writes.add_version(
            SOURCE_SECRET, secret_dsn(observer.identity, password), phase="final-source-observer"
        )
        need(time.monotonic() < deadline, "write_source_observer_rotation_deadline")
        writes._once("rotate-source-observer-password")
        with connection.transaction():
            connection.execute("SET TRANSACTION READ WRITE")
            connection.execute("SET LOCAL statement_timeout='5000ms'")
            connection.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    sql.Identifier(source_sql.OBSERVER_NAME), sql.Literal(password)
                )
            )
            need(time.monotonic() < deadline, "write_source_observer_rotation_deadline")
    except WriteDenied:
        raise
    except Exception:
        raise WriteDenied("write_source_observer_rotation_unknown_reconcile_only") from None
    finally:
        connection.close()
    final = transfer.BoundDatabase(observer.identity, password)
    source_sql.verify_provider_delete_complete(final, owner)
    writes.boundary._source_old_passwords = tuple(old)
    return reference, final


def rotate_empty_target(
    writes: OfficialWrites, bootstrap: transfer.BoundDatabase
) -> native.release.FreshTargetPlan:
    """Add/read back new numeric versions before one password transaction.

    The actual fence then authenticates all three passwords on the empty target.
    No copied version timestamp or SQL ACK is accepted as that authentication.
    """
    need(
        type(writes) is OfficialWrites
        and type(bootstrap) is transfer.BoundDatabase
        and bootstrap.restore_role is None,
        "write_target_native_bindings_required",
    )
    need(
        not os.path.lexists(writes.workdir / "rotate-fresh-target-passwords.started.json"),
        "write_target_rotation_already_attempted_reconcile_only",
    )
    identity = bootstrap.identity
    need(
        (
            identity.host,
            identity.port,
            identity.database,
            identity.database_oid,
            identity.cluster_sha256,
            identity.actor,
            identity.actor_oid,
        )
        == (
            TARGET_HOST,
            5432,
            "neondb",
            16395,
            TARGET_CLUSTER,
            "hirewiz_bootstrap_20261013",
            16391,
        ),
        "write_target_native_identity_drift",
    )
    boundary = writes.boundary
    source_sql.verify_cloud_isolated(boundary)
    need(
        boundary.retired_since is not None
        and boundary.scope is not None
        and len(boundary._setup_passwords) == 3,
        "write_target_native_cut_or_precut_passwords_missing",
    )
    assert boundary.scope is not None
    boundary._observe_closure(boundary.scope.source, replacements=False)
    passwords = [secrets.token_urlsafe(48) for _ in TARGET_ROLES]
    need(
        len(set(passwords)) == 3
        and all(
            not hmac.compare_digest(new.encode(), old.encode())
            for new in passwords
            for old in (*boundary._source_old_passwords, *boundary._setup_passwords.values())
        ),
        "write_target_password_reuses_precut_value",
    )
    deadline = time.monotonic() + 55
    connection = bootstrap.connect()
    try:
        with connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            connection.execute("SET LOCAL statement_timeout='1000ms'")
            owner = connection.execute(
                "SELECT datdba FROM pg_catalog.pg_database WHERE oid=16395"
            ).fetchone()
            need(tuple(owner or ()) == (16391,), "write_target_owner_drift")
            transfer._empty(connection)
            roles = connection.execute(
                "SELECT oid,rolname,rolcanlogin,rolinherit,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles WHERE oid IN (24576,24578,24580) ORDER BY oid"
            ).fetchall()
            need(
                roles
                == [
                    (oid, name, True, False, False, False, False, False, False)
                    for name, oid, _ in TARGET_ROLES
                ],
                "write_target_role_state_drift",
            )
        refs = []
        for (name, oid, secret), password in zip(TARGET_ROLES, passwords, strict=True):
            role = transfer.NativeDatabaseIdentity(
                TARGET_HOST,
                5432,
                "neondb",
                TARGET_CLUSTER,
                16395,
                name,
                oid,
                identity.root_certificate,
            )
            refs.append(
                writes.add_version(
                    secret, secret_dsn(role, password), phase="post-isolation-" + secret
                )
            )
        need(time.monotonic() < deadline, "write_target_rotation_deadline")
        writes._once("rotate-fresh-target-passwords")
        with connection.transaction():
            connection.execute("SET TRANSACTION READ WRITE")
            connection.execute("SET LOCAL statement_timeout='5000ms'")
            for (name, _, _), password in zip(TARGET_ROLES, passwords, strict=True):
                connection.execute(
                    sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                        sql.Identifier(name), sql.Literal(password)
                    )
                )
            observed = connection.execute(
                "SELECT oid,rolname,rolcanlogin,rolinherit,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles WHERE oid IN (24576,24578,24580) ORDER BY oid"
            ).fetchall()
            need(
                observed == roles and time.monotonic() < deadline,
                "write_target_rotation_role_or_deadline_drift",
            )
        return native.release.FreshTargetPlan(*refs)
    except WriteDenied:
        raise
    except Exception:
        raise WriteDenied("write_target_rotation_unknown_reconcile_only") from None
    finally:
        connection.close()


def finalize_target_migrator(
    writes: OfficialWrites,
    bootstrap: transfer.BoundDatabase,
    source: native.release.Plan,
    target: native.release.FreshTargetPlan,
) -> None:
    """Fixed post-stage NOLOGIN/noCREATE/session0 and exact version DISABLED.

    Source fences and fresh migration phase are native prerequisites. No runtime
    or observer password/version changes, no object ownership rewrite or DROP.
    """
    source.validate()
    target.validate(source)
    writes.boundary.verify_fences(source)
    writes.boundary.verify_fresh_database_phase(
        source, target, native.release.CHAIN[-1], phase="migration"
    )
    need(
        type(bootstrap) is transfer.BoundDatabase and bootstrap.restore_role is None,
        "write_target_native_bindings_required",
    )
    identity = bootstrap.identity
    need(
        (
            identity.host,
            identity.port,
            identity.database,
            identity.database_oid,
            identity.cluster_sha256,
            identity.actor,
            identity.actor_oid,
        )
        == (
            TARGET_HOST,
            5432,
            "neondb",
            16395,
            TARGET_CLUSTER,
            "hirewiz_bootstrap_20261013",
            16391,
        ),
        "write_target_native_identity_drift",
    )
    need(
        re.fullmatch(re.escape(TARGET_ROLES[1][2]) + r":[1-9][0-9]{0,8}", target.migration_secret),
        "write_target_selected_migration_version_required",
    )
    secret, version = target.migration_secret.split(":")
    resource = f"projects/{NUMBER}/secrets/{secret}/versions/{version}"
    before = writes._call("secret", "GET", resource)
    need(
        before is not None
        and before.get("name") == resource
        and before.get("state") == "ENABLED"
        and type(before.get("etag")) is str
        and bool(before["etag"]),
        "write_migration_secret_before_state_drift",
    )
    assert before is not None
    deadline = time.monotonic() + 55
    connection = bootstrap.connect()
    try:
        with connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            connection.execute("SET LOCAL statement_timeout='1000ms'")
            schema = connection.execute(
                "SELECT version_num FROM public.alembic_version LIMIT 2"
            ).fetchall()
            need(schema == [(native.release.CHAIN[-1],)], "write_target_finalization_schema_drift")
        writes._once("finalize-target-migrator-sql")
        with connection.transaction():
            connection.execute("SET TRANSACTION READ WRITE")
            connection.execute("SET LOCAL statement_timeout='5000ms'")
            connection.execute(
                sql.SQL("ALTER ROLE {} NOLOGIN").format(sql.Identifier(TARGET_ROLES[1][0]))
            )
            connection.execute(
                sql.SQL("REVOKE CREATE ON SCHEMA public FROM {}").format(
                    sql.Identifier(TARGET_ROLES[1][0])
                )
            )
            login = connection.execute(
                "SELECT rolcanlogin FROM pg_catalog.pg_roles WHERE oid=24578 AND rolname='hirewiz_migrator_20261013_r1'"
            ).fetchone()
            create = connection.execute(
                "SELECT pg_catalog.has_schema_privilege(24578,'public','CREATE')"
            ).fetchone()
            need(
                tuple(login or ()) == tuple(create or ()) == (False,)
                and time.monotonic() < deadline,
                "write_migrator_nologin_nocreate_unconfirmed",
            )
        with connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            connection.execute("SET LOCAL statement_timeout='1000ms'")
            connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(TARGET_ROLES[1][0])))
            transfer.verify_effective_role(
                connection, identity, transfer.NativeRestoreRole(TARGET_ROLES[1][0], 24578)
            )
            rows = connection.execute(
                "SELECT pid,usesysid,datid,backend_type FROM pg_catalog.pg_stat_activity WHERE usesysid=24578 AND pid<>pg_catalog.pg_backend_pid() LIMIT 65"
            ).fetchall()
            need(
                len(rows) <= 64
                and all(row[1:] == (24578, 16395, "client backend") for row in rows),
                "write_migrator_session_scope_refused",
            )
            for pid, role, database, _ in rows:
                need(time.monotonic() < deadline, "write_target_finalization_deadline")
                result = connection.execute(
                    "SELECT pg_catalog.pg_terminate_backend(pid,1000) FROM pg_catalog.pg_stat_activity WHERE pid=%s AND usesysid=%s AND datid=%s AND backend_type='client backend'",
                    (pid, role, database),
                ).fetchall()
                need(result in ([], [(True,)]), "write_migrator_session_termination_unconfirmed")
            connection.execute("RESET ROLE")
    except WriteDenied:
        raise
    except Exception:
        raise WriteDenied("write_migrator_finalization_unknown_reconcile_only") from None
    finally:
        connection.close()
    need(time.monotonic() < deadline, "write_target_finalization_deadline")
    writes._once("disable-selected-migration-version-" + version)
    writes._call("secret", "POST", resource + ":disable", body={"etag": before["etag"]})
    after = writes._call("secret", "GET", resource)
    need(
        after is not None and after.get("name") == resource and after.get("state") == "DISABLED",
        "write_migration_version_disable_unconfirmed",
    )
    writes.boundary.verify_fresh_database_phase(
        source, target, native.release.CHAIN[-1], phase="serving"
    )
