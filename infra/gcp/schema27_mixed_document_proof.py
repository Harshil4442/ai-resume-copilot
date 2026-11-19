"""New caller-authorization proof plus retained document binary/corpus evidence.

Actual SDK responses are never replaced. Derived prior-policy components enter
only pure historical validators; the full actual policies and ETags are retained
in a distinctly versioned forward receipt.
"""
from __future__ import annotations

import copy
import json
import time
from dataclasses import asdict
from pathlib import Path

from infra.gcp import document_refresh_probe as prior
from infra.gcp import document_source_release as scanner
from infra.gcp import schema27_forward_diagnostics as diagnostics
from infra.gcp import schema27_mixed_identity_profile as identities

rolling, need = identities.rolling, identities.need
KIND = "hirewiz_schema27_mixed_document_authorization_v1"


def origin():
    for module, relative in ((prior, "document_refresh_probe.py"), (scanner, "document_source_release.py")):
        need(Path(module.__file__).resolve() == identities.ROOT / "infra/gcp" / relative, "forward_helper_origin")
    need(Path(__file__).resolve() == identities.ROOT / "infra/gcp/schema27_mixed_document_proof.py", "forward_helper_origin")


class MetadataHttpError(Exception):
    """Only an actual fixed-route native HTTP status can create this failure."""
    def __init__(self, status, content):
        super().__init__("fixed_identity_metadata_read_failed")
        self.status_code, self.content = status, content


def validate_receipt(profile, value):
    need(type(profile) in {identities.Profile, identities.PublishedProfile, identities.StagedProfile} and type(value) is dict and set(value) == {
        "kind", "identity_profile_sha256", "actual_policies", "actual_policy_sha256", "actual_etags",
        "caller_authorization_verified", "binary_corpus_component", "legacy_policy_components_sha256", "native_approval",
    } and value["kind"] == KIND and value["identity_profile_sha256"] == profile.digest
         and value["caller_authorization_verified"] is True and value["native_approval"] is False,
         "forward_authorization_receipt")
    policies = value["actual_policies"]
    need(type(policies) is dict and set(policies) == set(identities.readback.DOCUMENTS)
         and value["actual_policy_sha256"] == rolling.sha(policies)
         and value["actual_etags"] == {name: identities.rehearsal._etag(policy) for name, policy in policies.items()}
         and value["legacy_policy_components_sha256"] == {name: rolling.sha(profile.legacy_policy_component(name, policy))
             for name, policy in policies.items()}, "forward_actual_policy_binding")
    binary = value["binary_corpus_component"]
    need(type(binary) is dict and set(binary) == {"scope", "acceptance", "sha256"}
         and binary["scope"] == "retained_document_binary_and_corpus"
         and type(binary["acceptance"]) is dict and set(binary["acceptance"]) == set(prior.NativeAcceptance.__dataclass_fields__)
         and binary["sha256"] == rolling.sha(binary["acceptance"])
         and type(binary["acceptance"].get("contract_version")) is int
         and binary["acceptance"]["contract_version"] == 5
         and type(binary["acceptance"].get("required_case_count")) is int
         and binary["acceptance"]["required_case_count"] == 17, "forward_binary_component_binding")
    return value


class Clients(prior.Clients):
    def __init__(self, profile, *, read_only_release=False, read_budget=None):
        origin()
        need(type(profile) in {identities.Profile, identities.PublishedProfile, identities.StagedProfile}, "profile_kind")
        super().__init__(read_only_release=read_only_release)
        self.identity_profile = profile
        self.authorization = None
        self.read_budget = read_budget if read_budget is not None else diagnostics.ReadBudget()

    def _read(self, operation, call):
        return self.data(self.read_budget.read(operation, call, self.errors.HttpError))

    def service(self, name):
        return self.read_budget.read("run_service_read", lambda: super(Clients, self).service(name), self.errors.HttpError)

    def revision(self, name):
        return self.read_budget.read("run_revision_read", lambda: super(Clients, self).revision(name), self.errors.HttpError)

    def job(self, name):
        return self.read_budget.read("run_job_read", lambda: super(Clients, self).job(name), self.errors.HttpError)

    def execution(self, name):
        return self.read_budget.read("run_execution_read", lambda: super(Clients, self).execution(name), self.errors.HttpError)

    def operation(self, name):
        return self.read_budget.read("run_operation_read", lambda: super(Clients, self).operation(name), self.errors.HttpError)

    def entries(self, query, token):
        return self.read_budget.read("logging_entries_read", lambda: super(Clients, self).entries(query, token), self.errors.HttpError)

    def maintenance_snapshot(self):
        account = "hirewiz-document-refresh@" + prior.PROJECT + ".iam.gserviceaccount.com"
        resource = "projects/" + prior.PROJECT + "/serviceAccounts/" + account
        return {"maintenance_service_account": self._read("iam_account_read", lambda: self.iam.projects_serviceAccounts.Get(
            self.im.IamProjectsServiceAccountsGetRequest(name=resource))),
            "maintenance_service_account_keys": self._read("iam_keys_read", lambda: self.iam.projects_serviceAccounts_keys.List(
                self.im.IamProjectsServiceAccountsKeysListRequest(name=resource,
                    keyTypes=[self.im.IamProjectsServiceAccountsKeysListRequest.KeyTypesValueValuesEnum.USER_MANAGED]))),
            "maintenance_role": self._read("maintenance_identity_read", lambda: self.iam.projects_roles.Get(
                self.im.IamProjectsRolesGetRequest(name="projects/" + prior.PROJECT + "/roles/hirewizDocumentServiceMaintenance")))}

    def identity(self, email):
        need(email in {rolling.RUNTIME, identities.rehearsal.NEW_API_EMAIL}, "principal_scope")
        resource = "https://iam.googleapis.com/v1/projects/" + prior.PROJECT + "/serviceAccounts/" + email
        account = self.read_budget.read("iam_account_read", lambda: self._profile_metadata(resource), MetadataHttpError)
        keys = self.read_budget.read("iam_keys_read", lambda: self._profile_metadata(resource + "/keys?keyTypes=USER_MANAGED"),
                                     MetadataHttpError)
        return identities.readback.project_identity(account, keys.get("keys", []))

    def document_policy(self, name):
        need(name in identities.readback.DOCUMENTS, "document_scope")
        uri = "https://run.googleapis.com/v2/" + prior.PARENT + "/services/" + name + ":getIamPolicy?options.requestedPolicyVersion=3"
        return self.read_budget.read("run_service_policy_read", lambda: self._profile_metadata(uri), MetadataHttpError)

    def _profile_metadata(self, uri):
        # A NEW fixed read scope on the SAME verified, hardened stock session.
        # The old global route allowlist and all SDK transports remain unchanged.
        resources = {"https://iam.googleapis.com/v1/projects/" + prior.PROJECT + "/serviceAccounts/" + email
                     for email in (rolling.RUNTIME, identities.rehearsal.NEW_API_EMAIL)}
        policies = {"https://run.googleapis.com/v2/" + prior.PARENT + "/services/" + name
                    + ":getIamPolicy?options.requestedPolicyVersion=3" for name in identities.readback.DOCUMENTS}
        need(uri in resources | {resource + "/keys?keyTypes=USER_MANAGED" for resource in resources} | policies,
             "principal_scope")
        if self.read_only_release:
            response, raw = self.http._transport.request(uri, method="GET", body=None, headers={}, redirections=0)
            status = response.status
        else:
            need(self.http._verified is True, "maintenance_identity_unverified")
            response = self.http.session.request("GET", uri, timeout=(5, 30), allow_redirects=False, stream=True)
            try:
                status = response.status_code
                raw = response.raw.read(prior.MAX_BYTES + 1, decode_content=True)
            finally:
                response.close()
        need(type(raw) is bytes and len(raw) <= prior.MAX_BYTES and type(status) is int
             and not 300 <= status < 400, "principal_response_bound")
        if status != 200:
            raise MetadataHttpError(status, raw)
        result = json.loads(raw)
        need(type(result) is dict, "principal_response_shape")
        return result

    def preflight(self):
        profile = self.identity_profile
        profile.verify_principals(self.identity)
        project = self._read("crm_project_read", lambda: self.crm.projects.Get(self.cm.CloudresourcemanagerProjectsGetRequest(projectId=prior.PROJECT)))
        resource = prior.PARENT + "/services/hirewiz-document-inspector"
        service = self.service(resource)
        policy = self._read("run_service_policy_read", lambda: self.run.projects_locations_services.GetIamPolicy(
            self.rm.RunProjectsLocationsServicesGetIamPolicyRequest(resource=resource, options_requestedPolicyVersion=3)))
        account = self._read("iam_account_read", lambda: self.iam.projects_serviceAccounts.Get(self.im.IamProjectsServiceAccountsGetRequest(
            name="projects/" + prior.PROJECT + "/serviceAccounts/" + prior.ACCOUNT)))
        keys = self._read("iam_keys_read", lambda: self.iam.projects_serviceAccounts_keys.List(self.im.IamProjectsServiceAccountsKeysListRequest(
            name="projects/" + prior.PROJECT + "/serviceAccounts/" + prior.ACCOUNT,
            keyTypes=[self.im.IamProjectsServiceAccountsKeysListRequest.KeyTypesValueValuesEnum.USER_MANAGED])))
        own = self._read("iam_account_policy_read", lambda: self.iam.projects_serviceAccounts.GetIamPolicy(self.im.IamProjectsServiceAccountsGetIamPolicyRequest(
            resource="projects/" + prior.PROJECT + "/serviceAccounts/" + prior.ACCOUNT, options_requestedPolicyVersion=3)))
        inherited = self._read("crm_project_policy_read", lambda: self.crm.projects.GetIamPolicy(self.cm.CloudresourcemanagerProjectsGetIamPolicyRequest(
            resource=prior.PROJECT, getIamPolicyRequest=self.cm.GetIamPolicyRequest(
                options=self.cm.GetPolicyOptions(requestedPolicyVersion=3)))))
        configuration = prior.load_source("hirewiz_document_refresh_worker_contract", prior.HERE / "document_worker.py")
        _, has_maintenance = configuration.service_bindings_without_maintenance(policy, "hirewiz-document-inspector")
        maintenance = self.maintenance_snapshot() if has_maintenance else {}
        if has_maintenance:
            configuration.validate_maintenance_identity(maintenance, expected_uid=getattr(self.http, "uid", None))
        legacy = profile.legacy_policy_component("hirewiz-document-inspector", policy)
        prior.validate_runtime(project, service, legacy, account, keys, own, inherited, maintenance=maintenance)
        coordinator = "hirewiz-resume-coordinator@" + prior.PROJECT + ".iam.gserviceaccount.com"
        upload = self._read("iam_account_read", lambda: self.iam.projects_serviceAccounts.Get(self.im.IamProjectsServiceAccountsGetRequest(
            name="projects/" + prior.PROJECT + "/serviceAccounts/" + coordinator)))
        upload_keys = self._read("iam_keys_read", lambda: self.iam.projects_serviceAccounts_keys.List(self.im.IamProjectsServiceAccountsKeysListRequest(
            name="projects/" + prior.PROJECT + "/serviceAccounts/" + coordinator,
            keyTypes=[self.im.IamProjectsServiceAccountsKeysListRequest.KeyTypesValueValuesEnum.USER_MANAGED])))
        actual = {"service": service, "service_iam": policy, "project_iam": inherited,
            "service_account": account, "service_account_keys": keys,
            "upload_coordinator_service_account": upload,
            "upload_coordinator_service_account_keys": upload_keys, **maintenance}
        component = copy.deepcopy(actual)
        component["service_iam"] = legacy
        prior.validate_worker_snapshot(self, component,
            historical_v1=prior.historical_v1_for_serving(service, service["template"]["containers"][0]["image"]))
        latest = self._read("run_service_policy_read", lambda: self.run.projects_locations_services.GetIamPolicy(
            self.rm.RunProjectsLocationsServicesGetIamPolicyRequest(resource=resource, options_requestedPolicyVersion=3)))
        need(latest == policy, "document_policy_changed_during_proof")
        self.authorization = {"kind": KIND + "_inspector_component", "identity_profile_sha256": profile.digest,
            "actual_inspector_policy": copy.deepcopy(policy), "actual_policy_sha256": rolling.sha(policy),
            "actual_etag": identities.rehearsal._etag(policy), "legacy_component_sha256": rolling.sha(legacy),
            "scope": "current_caller_authorization", "native_approval": False}
        return actual


class SourceProbe(scanner.SourceProbe):
    def __init__(self, build, profile, *, read_budget=None, **kwargs):
        origin()
        super().__init__(build, **kwargs)
        need(type(profile) in {identities.Profile, identities.PublishedProfile, identities.StagedProfile} and self._v5, "forward_v5_profile_required")
        self.identity_profile = profile
        self.read_budget = read_budget

    def clients(self):
        return Clients(self.identity_profile, read_only_release=self.read_only_release, read_budget=self.read_budget)

    def verify_current_successor(self, image, metadata):
        # This NativeAcceptance is only the immutable binary/corpus component.
        # Full changed-policy authorization is the distinct receipt above.
        return current_probe(self.identity_profile, image, metadata,
                             read_only_release=self.read_only_release, read_budget=self.read_budget)._verify_current_source(image, metadata)


def current_probe(profile, image, metadata, *, read_only_release=False, read_budget=None):
    original = scanner.current_probe(image, metadata, read_only_release=read_only_release)
    return SourceProbe(original.build, profile, root=original.root, read_only_release=read_only_release,
                       serving_read=True, read_budget=read_budget)


def run_candidate(target, journal):
    """Same two stock Jobs, original once records and budgets; new authorization."""
    need(type(target) is SourceProbe and not target.read_only_release and target._v5,
         "forward_candidate_scope")
    build, image = target.build, target.build["immutable_image"]
    need(journal.is_absolute() and journal.resolve() == journal and not journal.exists(), "forward_fresh_native_journal")
    runtime = target.source_contract()
    need(target.build_identity(read_only_release=False, build_id=build["build_id"], nonce=build["nonce"], image=image) == build,
         "forward_candidate_build_changed")
    journal.mkdir(mode=0o700)
    write = prior.build_module().write_new
    write(journal / "binding.json", {"version": 5, "image": image, "build_identity": build})
    clients = Clients(target.identity_profile, read_budget=diagnostics.ReadBudget(seconds=1800))
    try:
        for mode, handle in target.handles(build).items():
            clients.preflight()
            body = target.candidate.desired(image, handle["nonce"], mode)
            try:
                clients.job(body["name"])
            except diagnostics.ForwardDenied as error:
                need(error.http_status == 404, "forward_job_absence_unverified")
            else:
                need(False, "forward_job_exists_reconcile_only")
            write(journal / (mode + ".create.started.json"), {"body_sha256": target.candidate.sha(body), "automatic_replay": False})
            created = clients.create(body)
            write(journal / (mode + ".create.acknowledged.json"), {"name": created.get("name")})
            deadline = time.monotonic() + 900
            prior.wait_operation(clients, created, "Job", deadline)
            job = clients.job(body["name"])
            target.validate_job(job, {"image": image, "mode": mode, "job_nonce": handle["nonce"]})
            need(prior.integer(job.get("executionCount", 0), 0) and job.get("latestCreatedExecution") in (None, {}),
                 "forward_job_previously_executed")
            write(journal / (mode + ".run.started.json"), {"name": job["name"], "uid": job["uid"],
                  "etag": job["etag"], "automatic_replay": False})
            started = clients.start(job)
            write(journal / (mode + ".run.acknowledged.json"), {"name": started.get("name")})
            prior.wait_operation(clients, started, "Execution", deadline)
            _, _, observed = target._observation(clients, mode, build)
            if mode == "inventory":
                need(target.validate_inventory(observed, image, runtime) == {
                    "renderer_version": target.profile.RENDERER, "font_manifest_sha256": target.profile.FONT},
                    "forward_renderer_changed")
        accepted = target.verify_successor(image, build, journal)
        write(journal / "acceptance.json", asdict(accepted))
        write(journal / "acceptance-scope.json", {"scope": "document_binary_corpus_only",
            "identity_profile_sha256": target.identity_profile.digest, "native_approval": False})
        return accepted
    finally:
        clients.close()


def verify_document_profile(observer, source, profile):
    """Fresh native metadata, actual-policy delta, binary proof, TeX and routing."""
    origin()
    budget = diagnostics.ReadBudget()
    profile.verify_principals(lambda email: budget.read("iam_account_read", lambda: identities.read_identity(observer, email)))
    documents, tex, routing = rolling.release.documents, rolling.release.native_tex, rolling.release.document_routing
    def reader(kind, resource):
        return budget.read("run_service_read" if "/revisions/" not in resource else "run_revision_read",
                           lambda: observer._get(kind, resource))
    def cloud(*args):
        label = ("run_service_policy_read" if args[:3] == ("run", "services", "get-iam-policy")
                 else "crm_project_policy_read" if args[:2] == ("projects", "get-iam-policy")
                 else "iam_keys_read" if args[:4] == ("iam", "service-accounts", "keys", "list")
                 else "iam_account_read" if args[:2] == ("iam", "service-accounts")
                 else "document_routing_read")
        return budget.read(label, lambda: observer._cloud(*args))
    worker = reader("run", documents.RESOURCE)
    need(worker.get("uri") == documents.ACCEPTED_ORIGIN, "inspector_origin")
    image = worker["template"]["containers"][0]["image"]
    probe = current_probe(profile, image, worker, read_only_release=True, read_budget=budget)
    binary = probe._verify_current_source(image, worker)
    need(binary.contract_version == 5 and binary.required_case_count == 17, "binary_profile_changed")
    policies = {name: cloud("run", "services", "get-iam-policy", name,
                "--region", rolling.release.REGION) for name in identities.readback.DOCUMENTS}
    def snapshot(service, resource, account):
        value = reader("run", resource)
        result = {"service": value, "revision": reader("run", value["latestReadyRevision"]),
            "service_iam": policies[service], "project_iam": cloud("projects", "get-iam-policy", rolling.release.PROJECT),
            "service_account": cloud("iam", "service-accounts", "describe", account),
            "service_account_keys": {"keys": cloud("iam", "service-accounts", "keys", "list", "--iam-account", account)}}
        result["upload_coordinator_service_account"] = cloud("iam", "service-accounts", "describe", documents.UPLOAD_COORDINATOR)
        result["upload_coordinator_service_account_keys"] = {"keys": cloud(
            "iam", "service-accounts", "keys", "list", "--iam-account", documents.UPLOAD_COORDINATOR)}
        _, maintenance = documents.service_bindings_without_maintenance(policies[service], service)
        if maintenance:
            result["maintenance_service_account"] = cloud("iam", "service-accounts", "describe", documents.MAINTENANCE_ACCOUNT)
            result["maintenance_service_account_keys"] = {"keys": cloud(
                "iam", "service-accounts", "keys", "list", "--iam-account", documents.MAINTENANCE_ACCOUNT)}
            result["maintenance_role"] = cloud("iam", "roles", "describe", documents.MAINTENANCE_ROLE.rsplit("/", 1)[-1])
        return result
    inspector = snapshot(documents.SERVICE, documents.RESOURCE, documents.SERVICE_ACCOUNT)
    need(inspector["service"] == worker, "worker_changed")
    legacy_inspector = copy.deepcopy(inspector)
    legacy_inspector["service_iam"] = profile.legacy_policy_component(documents.SERVICE, policies[documents.SERVICE])
    documents.check_snapshot(legacy_inspector, image, source.release, api_phase=True, coordinator_phase=True, source_v5=True)
    native_tex = snapshot(tex.SERVICE, tex.RESOURCE, tex.SERVICE_ACCOUNT)
    need(native_tex["service"].get("uri") == tex.ACCEPTED_ORIGIN, "tex_origin")
    tex.api_environment(tex.ACCEPTED_ORIGIN, tex.ACCEPTED_IMAGE)
    legacy_tex = copy.deepcopy(native_tex)
    legacy_tex["service_iam"] = profile.legacy_policy_component(tex.SERVICE, policies[tex.SERVICE])
    tex.check_snapshot(legacy_tex, tex.ACCEPTED_IMAGE, source.release, runtime_phase=True, coordinator_phase=True)
    routing.verify_network(cloud("compute", "networks", "describe", routing.NETWORK),
        cloud("compute", "networks", "subnets", "describe", routing.SUBNET, "--region", rolling.release.REGION),
        cloud("dns", "managed-zones", "describe", routing.DNS_ZONE),
        cloud("dns", "record-sets", "list", "--zone", routing.DNS_ZONE, "--limit", "5"),
        cloud("compute", "routes", "list", "--filter", "network=" + routing.NETWORK_URL, "--limit", "65"))
    latest = {name: cloud("run", "services", "get-iam-policy", name,
              "--region", rolling.release.REGION) for name in identities.readback.DOCUMENTS}
    need(latest == policies and reader("run", documents.RESOURCE) == worker, "authorization_snapshot_changed")
    result = {"kind": KIND, "identity_profile_sha256": profile.digest, "actual_policies": policies,
        "actual_policy_sha256": rolling.sha(policies), "actual_etags": {name: identities.rehearsal._etag(value)
            for name, value in policies.items()}, "caller_authorization_verified": True,
        "binary_corpus_component": {"scope": "retained_document_binary_and_corpus", "acceptance": asdict(binary),
                                   "sha256": rolling.sha(asdict(binary))},
        "legacy_policy_components_sha256": {name: rolling.sha(profile.legacy_policy_component(name, value))
            for name, value in policies.items()}, "native_approval": False}
    return validate_receipt(profile, result)
