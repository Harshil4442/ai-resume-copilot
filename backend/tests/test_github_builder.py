"""Inert, standalone controls: stdlib unittest; never load SDK or app modules."""
from __future__ import annotations

import copy
import importlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/gcp"))
repair = importlib.import_module("github_builder")


def old_trigger() -> dict:
    return {"id": repair.TRIGGER_ID, "resourceName": repair.TRIGGER_PATH,
            "name": "Trigger-for-github-updates", "filename": "cloudbuild.yaml",
            "github": {"owner": "Harshil4442", "name": "ai-resume-copilot", "push": {"branch": "^main$"}},
            "serviceAccount": repair.OLD_SA, "disabled": False}


class NativeDouble(repair.SDKRepair):
    def __init__(self, workdir: Path):
        super().__init__(Path("/not-used-sdk"), "inert-operator", workdir)
        self.trigger_row = old_trigger()
        self.old_disabled = False
        self.old_account_overrides = {}
        self.builder_row = None
        self.docx_status = "SUCCESS"
        self.live = {}
        self.calls = []
        self.unknown = None
        self.uid_drift = False
        self.policies = {resource: {"version": 3, "etag": "native-etag",
                           "bindings": [{"role": "roles/viewer", "members": ["user:other@example.invalid"],
                                         "condition": {"title": "kept", "expression": "true"}}]}
                         for resource in ("project", "repository", "bucket")}

    def _call(self, api, method, path, *, body=None, query=None, allow_absent=False):
        self.calls.append((api, method, path, copy.deepcopy(body), copy.deepcopy(query)))
        if api == "resource":
            return {"name": "projects/" + repair.NUMBER, "projectId": repair.PROJECT, "state": "ACTIVE"}
        if api == "iam" and method == "GET":
            if path.endswith(repair.BUILDER_EMAIL):
                result = copy.deepcopy(self.builder_row)
                if self.uid_drift and result:
                    result["uniqueId"] = "900000000000000000002"
                return result
            email = path.split("/serviceAccounts/")[1]
            return {"email": email, "uniqueId": repair.OLD_PRINCIPALS[email], "disabled": self.old_disabled,
                    **self.old_account_overrides.get(email, {})}
        mutation = False
        if api == "iam" and method == "POST":
            mutation = True
            self.builder_row = {"email": repair.BUILDER_EMAIL, "uniqueId": "900000000000000000001",
                                "projectId": repair.PROJECT, "disabled": False}
            result = copy.deepcopy(self.builder_row)
        elif api == "build" and path == repair.TRIGGER_PATH:
            if method == "PATCH":
                mutation = True
                self.trigger_row.update(body)
            result = copy.deepcopy(self.trigger_row)
        elif api == "build" and path == repair.DOCX_BUILD:
            result = {"id": repair.DOCX_BUILD_ID, "name": repair.DOCX_BUILD_NAME,
                      "projectId": repair.PROJECT, "serviceAccount": repair.OLD_SA, "status": self.docx_status}
        elif api == "build":
            result = copy.deepcopy(self.live.get(path, {}))
        elif api == "artifact" and path == repair.REPOSITORY:
            result = {"name": repair.REPOSITORY, "format": "DOCKER"}
        elif api == "storage" and path == "b/" + repair.BUCKET:
            result = {"name": repair.BUCKET, "id": repair.BUCKET, "projectNumber": repair.NUMBER}
        else:
            resource = {"project": "project", "artifact": "repository", "storage": "bucket"}[api]
            if method == "PUT" or path.endswith(":setIamPolicy"):
                mutation = True
                self.policies[resource] = copy.deepcopy(body if method == "PUT" else body["policy"])
                self.policies[resource]["etag"] = "new-native-etag"
            result = copy.deepcopy(self.policies[resource])
        if mutation and self.unknown == (api, method, path):
            self.unknown = None
            raise repair.RepairDenied("inert_unknown_result")
        return result

    def mutations(self):
        return [call for call in self.calls if call[1] in {"PATCH", "PUT"}
                or call[1] == "POST" and not call[2].endswith(":getIamPolicy")]


class BuilderRepairControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.path.chmod(0o700)
        self.native = NativeDouble(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def prepared(self):
        self.native.trigger_row["disabled"] = True
        self.native.old_disabled = True
        return self.native.create_builder_once()

    def test_exact_trigger_drift_refuses(self):
        for key, value in (("id", "other"), ("resourceName", "projects/other/locations/us-central1/triggers/x"),
                           ("name", "other"), ("filename", "deploy.yaml"), ("serviceAccount", repair.BUILDER_SA)):
            row = old_trigger()
            row[key] = value
            with self.subTest(key=key), self.assertRaises(repair.RepairDenied):
                repair.trigger(row, service_account=repair.OLD_SA, disabled=False)
        for push in ({"branch": ".*"}, {"branch": "^main$", "invertRegex": True}, {"tag": ".*"}):
            row = old_trigger()
            row["github"]["push"] = push
            with self.subTest(push=push), self.assertRaises(repair.RepairDenied):
                repair.trigger(row, service_account=repair.OLD_SA, disabled=False)

    def test_disable_preserves_trigger_and_patches_one_field(self):
        before = copy.deepcopy(self.native.trigger_row)
        self.native.disable_trigger_once()
        self.assertEqual(self.native.trigger_row, {**before, "disabled": True})
        write = self.native.mutations()[0]
        self.assertEqual(write[3], {**before, "disabled": True})
        self.assertEqual(write[4]["updateMask"], "disabled")
        self.assertEqual(write[4]["projectId"], repair.PROJECT)
        self.assertEqual(write[4]["triggerId"], repair.TRIGGER_ID)

    def test_full_current_optional_configuration_is_preserved_through_all_updates(self):
        options = {
            "description": "Existing production source build",
            "substitutions": {"_REGION": "us-central1"},
            "tags": ["production", "github"],
            "includedFiles": ["backend/**", "infra/**"],
            "ignoredFiles": ["docs/**"],
            "approvalConfig": {"approvalRequired": False},
            "includeBuildLogs": "INCLUDE_BUILD_LOGS_WITH_STATUS",
            "createTime": "2026-01-01T00:00:00Z",
        }
        self.native.trigger_row.update(copy.deepcopy(options))
        self.native.disable_trigger_once()
        self.native.old_disabled = True
        builder = self.native.create_builder_once()
        self.native.grant_builder_once(builder)
        self.native.retarget_once(builder)
        self.native.enable_trigger_once(builder)
        patches = [call for call in self.native.calls if call[1] == "PATCH"]
        self.assertEqual(len(patches), 3)
        for call in patches:
            for key, value in options.items():
                self.assertEqual(call[3][key], value)
            self.assertEqual(call[4]["projectId"], repair.PROJECT)
            self.assertEqual(call[4]["triggerId"], repair.TRIGGER_ID)
        reads = [call for call in self.native.calls if call[2] == repair.TRIGGER_PATH
                 and call[1] == "GET"]
        self.assertTrue(reads and all(call[4] is None for call in reads))
        self.assertEqual({key: self.native.trigger_row[key] for key in options}, options)

    def test_alternative_source_cannot_reach_any_mutation(self):
        for key in ("build", "sourceToBuild", "gitFileSource", "triggerTemplate",
                    "repositoryEventConfig", "pubsubConfig", "webhookConfig", "autodetect"):
            self.native.trigger_row = {**old_trigger(), key: {"synthetic": "alternate"}}
            with self.subTest(key=key), self.assertRaisesRegex(repair.RepairDenied, "alternative_source"):
                self.native.disable_trigger_once()
            self.assertEqual(self.native.mutations(), [])

    def test_optional_configuration_readback_drift_refuses_without_replay(self):
        self.native.trigger_row["description"] = "preserved"
        original = self.native._call
        def changed(api, method, path, **kwargs):
            result = original(api, method, path, **kwargs)
            if api == "build" and method == "PATCH":
                self.native.trigger_row["description"] = "unexpected concurrent change"
            return result
        self.native._call = changed
        with self.assertRaisesRegex(repair.RepairDenied, "unrelated_configuration_drift"):
            self.native.disable_trigger_once()
        self.assertEqual(len(self.native.mutations()), 1)
        self.native.trigger_row["disabled"] = False
        with self.assertRaisesRegex(repair.RepairDenied, "attempt_exists"):
            self.native.disable_trigger_once()
        self.assertEqual(len(self.native.mutations()), 1)

    def test_active_known_docx_build_blocks_drain(self):
        self.native.trigger_row["disabled"] = True
        for status in ("PENDING", "QUEUED", "WORKING", "STATUS_UNKNOWN", None, "NEW_ACTIVE_STATUS"):
            self.native.docx_status = status
            with self.subTest(status=status), self.assertRaises(repair.RepairDenied):
                self.native.require_drained_old_builder()
        self.assertEqual(self.native.mutations(), [])

    def test_partial_or_any_other_live_build_blocks_drain(self):
        self.native.trigger_row["disabled"] = True
        path = f"projects/{repair.PROJECT}/locations/global/builds"
        for result in ({"nextPageToken": "unexamined"}, {"builds": [{"status": "PENDING"}]},
                       {"builds": [{"status": "STATUS_UNKNOWN"}]}, {"builds": None}, {"unreachable": ["x"]}):
            self.native.live[path] = result
            with self.subTest(result=result), self.assertRaises(repair.RepairDenied):
                self.native.require_drained_old_builder()
        self.native.live = {}
        self.native.require_drained_old_builder()
        lists = [call for call in self.native.calls if call[0] == "build" and call[2].endswith("/builds")]
        self.assertTrue(all(call[4]["filter"] == repair.UNFINISHED_FILTER for call in lists))
        self.assertEqual(set(repair.TERMINAL), {"SUCCESS", "FAILURE", "INTERNAL_ERROR", "TIMEOUT", "CANCELLED", "EXPIRED"})

    def test_create_requires_native_old_uid_isolation(self):
        self.native.trigger_row["disabled"] = True
        with self.assertRaises(repair.RepairDenied):
            self.native.create_builder_once()
        self.assertEqual(self.native.mutations(), [])
        self.assertEqual(self.prepared().uid, "900000000000000000001")
        with self.assertRaises(repair.RepairDenied):
            self.native.create_builder_once()
        self.assertEqual(len(self.native.mutations()), 1)

    def test_create_requires_actual_uid_readback(self):
        self.native.old_disabled = True
        self.native.trigger_row["disabled"] = True
        self.native.uid_drift = True
        with self.assertRaisesRegex(repair.RepairDenied, "builder_account_uid_drift"):
            self.native.create_builder_once()
        self.assertTrue((self.path / "builder-account-create.started.json").exists())

    def test_three_scoped_grants_preserve_existing_policies(self):
        builder = self.prepared()
        original = copy.deepcopy(self.native.policies)
        self.native.grant_builder_once(builder)
        for resource, role in (("project", "roles/logging.logWriter"),
                               ("repository", "roles/artifactregistry.writer"), ("bucket", "roles/storage.objectViewer")):
            repair.assert_binding(self.native.policies[resource], role)
            self.assertIn(original[resource]["bindings"][0], self.native.policies[resource]["bindings"])
        writes = self.native.mutations()[1:]
        self.assertEqual([(call[0], call[1], call[2]) for call in writes], [
            ("artifact", "POST", repair.REPOSITORY + ":setIamPolicy"),
            ("project", "POST", "projects/" + repair.PROJECT + ":setIamPolicy"),
            ("storage", "PUT", "b/" + repair.BUCKET + "/iam")])
        self.assertTrue(all((call[3] if call[1] == "PUT" else call[3]["policy"])["etag"] == "native-etag"
                            for call in writes))

    def test_unknown_trigger_outcome_reconciles_without_replay(self):
        self.native.unknown = ("build", "PATCH", repair.TRIGGER_PATH)
        with self.assertRaises(repair.RepairDenied):
            self.native.disable_trigger_once()
        self.native.read_trigger(service_account=repair.OLD_SA, disabled=True)
        self.native.trigger_row["disabled"] = False  # Even external reversal cannot replay this attempt.
        with self.assertRaisesRegex(repair.RepairDenied, "attempt_exists"):
            self.native.disable_trigger_once()
        self.assertEqual(len(self.native.mutations()), 1)

    def test_partial_unknown_grant_allows_only_untouched_phases(self):
        builder = self.prepared()
        self.native.unknown = ("project", "POST", "projects/" + repair.PROJECT + ":setIamPolicy")
        with self.assertRaises(repair.RepairDenied):
            self.native.grant_builder_once(builder)
        repair.assert_binding(self.native._policy("project"), "roles/logging.logWriter")
        repair.assert_binding(self.native._policy("repository"), "roles/artifactregistry.writer")
        self.assertFalse((self.path / "builder-grant-bucket.started.json").exists())
        count = len(self.native.mutations())
        with self.assertRaises(repair.RepairDenied):
            self.native.grant_logging_once(builder)
        self.assertEqual(len(self.native.mutations()), count)
        self.native.grant_source_read_once(builder)
        self.native.verify_builder_permissions(builder)

    def test_retarget_and_enable_preserve_repo_branch_config(self):
        builder = self.prepared()
        self.native.grant_builder_once(builder)
        before = self.native.read_trigger(service_account=repair.OLD_SA, disabled=True)
        self.native.retarget_once(builder)
        self.assertEqual(self.native.read_trigger(service_account=repair.BUILDER_SA, disabled=True), before)
        self.native.enable_trigger_once(builder)
        self.assertEqual(self.native.read_trigger(service_account=repair.BUILDER_SA, disabled=False), before)
        writes = self.native.mutations()[-2:]
        self.assertEqual([call[4]["updateMask"] for call in writes], ["serviceAccount", "disabled"])
        self.assertEqual(writes[0][3], {**old_trigger(), "disabled": True,
                                      "serviceAccount": repair.BUILDER_SA})
        self.assertEqual(writes[1][3], {**old_trigger(), "disabled": False,
                                      "serviceAccount": repair.BUILDER_SA})

    def test_wrong_builder_uid_or_extra_role_blocks_enable(self):
        builder = self.prepared()
        self.native.grant_builder_once(builder)
        self.native.retarget_once(builder)
        count = len(self.native.mutations())
        with self.assertRaises(repair.RepairDenied):
            self.native.enable_trigger_once(repair.Builder("900000000000000000003"))
        self.native.policies["project"]["bindings"].append({"role": "roles/run.admin", "members": [repair.MEMBER]})
        with self.assertRaises(repair.RepairDenied):
            self.native.enable_trigger_once(builder)
        self.assertEqual(len(self.native.mutations()), count)

    def test_denied_routes_never_initialize_sdk(self):
        sdk = repair.SDKRepair(Path("/not-used-sdk"), "inert-operator", self.path)
        for api, method, path in (("secret", "GET", "projects/x/secrets/s/versions/1:access"),
                                  ("iam", "POST", "projects/-/serviceAccounts/" + repair.OLD_UID + ":disable"),
                                  ("build", "POST", repair.TRIGGER_PATH + ":run"),
                                  ("run", "PATCH", "projects/x/locations/y/services/z")):
            with self.subTest(path=path), self.assertRaisesRegex(repair.RepairDenied, "route_refused"):
                sdk._call(api, method, path)
        self.assertIsNone(sdk.http)

    def test_policy_readback_detects_unrelated_changes(self):
        expected = repair.add_binding(self.native.policies["project"], "roles/logging.logWriter")
        changed = copy.deepcopy(expected)
        changed["etag"] = "new"
        changed["bindings"].reverse()
        repair.assert_policy_preserved(changed, expected)
        changed["bindings"][1]["members"].append("user:extra@example.invalid")
        with self.assertRaisesRegex(repair.RepairDenied, "unrelated_delta"):
            repair.assert_policy_preserved(changed, expected)

    def test_missing_etag_and_existing_membership_refuse(self):
        with self.assertRaises(repair.RepairDenied):
            repair.add_binding({"bindings": []}, "roles/logging.logWriter")
        self.native.policies["project"]["bindings"].append({"role": "roles/owner", "members": [repair.MEMBER]})
        with self.assertRaises(repair.RepairDenied):
            repair.add_binding(self.native.policies["project"], "roles/logging.logWriter")

    def test_sixth_old_worker_must_have_exact_native_uid_and_be_disabled_before_create(self):
        worker = "hirewiz-worker@ai-resume-parser-482412.iam.gserviceaccount.com"
        self.assertEqual(repair.OLD_PRINCIPALS[worker], "115123377998883908933")
        self.native.old_disabled = True
        self.native.trigger_row["disabled"] = True
        for override in ({"disabled": False}, {"uniqueId": "900000000000000000002"}):
            self.native.old_account_overrides[worker] = override
            with self.subTest(override=override), self.assertRaisesRegex(repair.RepairDenied, "old_principal_uid_or_state_drift"):
                self.native.create_builder_once()
            self.assertEqual(self.native.mutations(), [])
            self.assertFalse(list(self.path.glob("*.started.json")))

    def test_worker_reactivation_blocks_every_builder_continuation_before_write(self):
        worker = "hirewiz-worker@ai-resume-parser-482412.iam.gserviceaccount.com"
        for phase in ("grant", "retarget", "enable"):
            path = self.path / phase
            path.mkdir(mode=0o700)
            native = NativeDouble(path)
            native.old_disabled = True
            native.trigger_row["disabled"] = True
            builder = native.create_builder_once()
            if phase in {"retarget", "enable"}:
                native.grant_builder_once(builder)
            if phase == "enable":
                native.retarget_once(builder)
            count = len(native.mutations())
            native.old_account_overrides[worker] = {"disabled": False}
            action = {"grant": native.grant_builder_once, "retarget": native.retarget_once,
                      "enable": native.enable_trigger_once}[phase]
            with self.subTest(phase=phase), self.assertRaisesRegex(repair.RepairDenied, "old_principal_uid_or_state_drift"):
                action(builder)
            self.assertEqual(len(native.mutations()), count)

    def test_new_builder_cannot_reuse_retired_worker_uid(self):
        with self.assertRaisesRegex(repair.RepairDenied, "native_uid_invalid"):
            repair.Builder("115123377998883908933").validate()


if __name__ == "__main__":
    unittest.main(verbosity=2)
