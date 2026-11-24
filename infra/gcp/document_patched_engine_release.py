"""ClamAV1.4.7 qualification over unchanged wire-five/seventeen-case evidence.

The new native Build kind, policy, source archive and measured engine are exact.
Old V5 remains read-only predecessor evidence. A-pinned source/identity helpers
remain unchanged; no old receipt or native resource is relabelled.
"""
from __future__ import annotations

import copy
import re
import time
from dataclasses import asdict

from infra.gcp import document_refresh_probe as prior
from infra.gcp import document_source_candidate_v5 as v5
from infra.gcp import document_source_patched_engine as engine
from infra.gcp import document_source_payload_v5 as payload
from infra.gcp import document_source_receipt_v5 as receipt
from infra.gcp import document_source_release as source
from infra.gcp import schema27_forward_diagnostics as diagnostics
from infra.gcp import schema27_mixed_document_proof as authorization
from infra.gcp import schema27_mixed_identity_profile as identities

rolling, need = identities.rolling, identities.need
KIND = authorization.KIND


def validate_worker_snapshot(clients, snapshot, *, retained_v5=False):
    service = snapshot["service"]
    image = service.get("template", {}).get("containers", [{}])[0].get("image")
    need(type(image) is str and prior.IMAGE.fullmatch(image), "patched_engine_worker_image")
    snapshot["revision"] = prior.validate_serving(clients, service, image, "hirewiz-document-inspector", prior.SERVICE_UID)
    configuration = prior.load_source("hirewiz_patched_engine_worker_contract", prior.HERE / "document_worker.py")
    try:
        configuration.check_snapshot(snapshot, image, service.get("template", {}).get("labels", {}).get("hirewiz-source"),
                                     api_phase=True, coordinator_phase=True,
                                     **({"retained_source_v5": True} if retained_v5 else {"source_patched_engine": True}))
    except configuration.ConfigurationDenied:
        raise prior.NativeProofDenied("document_patched_engine_worker_configuration_changed") from None
    return image


class Clients(authorization.Clients):
    def preflight(self):
        observed = self.service(prior.PARENT + "/services/hirewiz-document-inspector")
        policy_rows = [row for row in observed.get("template", {}).get("containers", [{}])[0].get("env", [])
                       if row.get("name") == "DOCUMENT_WORKER_POLICY_SHA256"]
        if policy_rows == [{"name": "DOCUMENT_WORKER_POLICY_SHA256", "value": v5.POLICY_SHA256}]:
            retained_v5 = True
        else:
            retained_v5 = False
        need(retained_v5 or policy_rows == [{"name": "DOCUMENT_WORKER_POLICY_SHA256", "value": engine.POLICY_SHA256}],
             "patched_engine_worker_profile")
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
        validate_worker_snapshot(self, component, retained_v5=retained_v5)
        latest = self._read("run_service_policy_read", lambda: self.run.projects_locations_services.GetIamPolicy(
            self.rm.RunProjectsLocationsServicesGetIamPolicyRequest(resource=resource, options_requestedPolicyVersion=3)))
        need(latest == policy, "document_policy_changed_during_proof")
        self.authorization = {"kind": KIND + "_inspector_component", "identity_profile_sha256": profile.digest,
            "actual_inspector_policy": copy.deepcopy(policy), "actual_policy_sha256": rolling.sha(policy),
            "actual_etag": identities.rehearsal._etag(policy), "legacy_component_sha256": rolling.sha(legacy),
            "scope": "current_caller_authorization", "native_approval": False}
        return actual


class Probe(source.SourceProbe):
    """New source qualification identity; V5 denotes only its unchanged wire."""
    CONTRACT_VERSION, REQUIRED_CASE_COUNT = 5, 17

    def __init__(self, build, profile, *, read_only_release=False, serving_read=False,
                 root=engine.ROOT, read_budget=None):
        need(type(profile) is identities.Profile and type(build) is dict
             and type(read_only_release) is bool and type(serving_read) is bool,
             "patched_engine_probe_scope")
        profile.verify_custody()
        keys = {"version", "kind", "nonce", "build_id", "build_resource", "builder_service_account",
            "source_bucket", "source_object", "source_generation", "source_archive_sha256",
            "refresh_dockerfile_sha256", "fixed_renderer_base", "immutable_image", "policy_sha256",
            "source_sha256", "source_commit", "qualification_sha256"}
        need(set(build) == keys | ({"local_native_proof_sha256"} if "local_native_proof_sha256" in build else set())
             and build.get("version") == 1 and type(build.get("version")) is int
             and build.get("kind") == engine.KIND and build.get("fixed_renderer_base") == source.courier.IMAGE
             and type(build.get("immutable_image")) is str and prior.IMAGE.fullmatch(build["immutable_image"]),
             "patched_engine_build_kind")
        self.root, self.build = root, build
        self._source_profile, self._payload, self._receipt = engine, payload, receipt
        self.source = engine.committed(build.get("source_commit"), root=root)
        self.POLICY = self.source.policy
        need(build.get("source_sha256") == self.source.sources and build.get("policy_sha256") == engine.POLICY_SHA256
             and self.POLICY == engine.POLICY_SHA256
             and build.get("qualification_sha256") == self.source.qualification_sha256
             and (serving_read and "local_native_proof_sha256" not in build
                  or type(build.get("local_native_proof_sha256")) is str
                  and re.fullmatch(r"[a-f0-9]{64}", build["local_native_proof_sha256"])),
             "patched_engine_build_sources")
        self._v5 = True  # Wire protocol, not the old source/Build qualification kind.
        self.read_only_release, self.serving_read = read_only_release, serving_read
        self.identity_profile, self.read_budget = profile, read_budget
        self.profile, self.candidate = source.courier, self

    def clients(self):
        return Clients(self.identity_profile, read_only_release=self.read_only_release, read_budget=self.read_budget)

    def policy_for_serving(self, metadata, image):
        containers = metadata.get("template", {}).get("containers", [])
        need(type(containers) is list and len(containers) == 1 and containers[0].get("image") == image,
             "patched_engine_serving_image")
        rows = [row for row in containers[0].get("env", []) if row.get("name") == "DOCUMENT_WORKER_POLICY_SHA256"]
        allowed = [{"name": "DOCUMENT_WORKER_POLICY_SHA256", "value": engine.POLICY_SHA256}]
        if image != self.build["immutable_image"]:
            allowed.append({"name": "DOCUMENT_WORKER_POLICY_SHA256", "value": v5.POLICY_SHA256})
        need(len(rows) == 1 and rows[0] in allowed,
             "patched_engine_serving_policy")
        return rows[0]["value"]

    def verify_caller_pins(self, clients, image, *, policy_sha256=None, caller_serving_validator=None):
        if policy_sha256 == v5.POLICY_SHA256:
            metadata = clients.service(prior.PARENT + "/services/hirewiz-document-inspector")
            original = source.current_probe(image, metadata)
            old = HistoricalV5Probe(original.build, self.identity_profile, root=original.root, serving_read=True)
            return old.verify_caller_pins(clients, image, policy_sha256=policy_sha256,
                                         caller_serving_validator=caller_serving_validator)
        return super().verify_caller_pins(clients, image, policy_sha256=policy_sha256,
                                         caller_serving_validator=caller_serving_validator)

    def _verified_evidence(self, clients):
        result = super()._verified_evidence(clients)
        scans = [row["scan"] for row in result[3]["cases"] if "scan" in row]
        need(len(scans) == 9 and all(row.get("engine") == "ClamAV"
             and row.get("version") == engine.ENGINE_VERSION for row in scans), "patched_engine_measured_version")
        return result

    def verify_current_successor(self, image, metadata):
        return self._verify_current_source(image, metadata)


def current_probe(profile, image, metadata, *, read_only_release=False, read_budget=None):
    labels = metadata.get("labels", {})
    builder = prior.build_module()
    verify = builder.verify_current_build_release if read_only_release else builder.verify_current_build
    build = verify(labels.get("hirewiz-refresh-build"), labels.get("hirewiz-refresh-nonce"), image)
    return Probe(build, profile, read_only_release=read_only_release, serving_read=True, read_budget=read_budget)


class HistoricalV5Probe(authorization.SourceProbe):
    def clients(self):
        return Clients(self.identity_profile, read_only_release=self.read_only_release, read_budget=self.read_budget)


def historical_predecessor(profile, image, metadata):
    from infra.gcp import schema27_expired_v5_recovery as recovery

    original = source.current_probe(image, metadata)
    need(original._v5 and original.POLICY == v5.POLICY_SHA256,
         "patched_engine_structural_parent")
    probe = HistoricalV5Probe(original.build, profile, root=original.root, serving_read=True)
    observed = probe._verify_current_source(image, metadata, monitor=True)
    need(type(observed) is source.ExpiredSourcePredecessor and observed.contract_version == 5
         and observed.required_case_count == 17 and observed.policy_sha256 == v5.POLICY_SHA256,
         "patched_engine_expired_structural_parent")
    return recovery.ExpiredV5Predecessor(**asdict(observed))


class MixedSourceBoundary(identities.MixedSourceBoundary):
    """Only this explicit new caller consumes the new qualification profile."""
    def verify_document_configuration(self, application):
        self.last_document_authorization = verify_document_profile(self, application, self.identity_profile)
        return self.last_document_authorization


def _phase_read(path):
    build = prior.build_module()
    from infra.gcp import document_refresh_recovery as custody

    return build.decode(custody.read(path, build))


def phase_receipt(target, clients, journal, mode):
    """Join original once intents/ACKs to the same native Job/execution/logs."""
    need(mode in {"inventory", "corpus"}, "patched_engine_stage_required")
    build, image = target.build, target.build["immutable_image"]
    handle = target.handles(build)[mode]
    body = target.desired(image, handle["nonce"], mode)
    need(_phase_read(journal / (mode + ".create.started.json")) == {
        "body_sha256": target.sha(body), "automatic_replay": False}, "patched_engine_job_intent_changed")
    created = prior.operation_result(clients, _phase_read(journal / (mode + ".create.acknowledged.json")), "Job")
    started = _phase_read(journal / (mode + ".run.started.json"))
    launched = prior.operation_result(clients, _phase_read(journal / (mode + ".run.acknowledged.json")), "Execution")
    job, execution, observed = target._observation(clients, mode, build)
    need(type(created) is dict and type(launched) is dict and created.get("name") == job["name"]
         and created.get("uid") == job["uid"] and launched.get("name") == execution["name"]
         and launched.get("uid") == execution["uid"] and type(started) is dict
         and set(started) == {"name", "uid", "etag", "automatic_replay"}
         and started.get("name") == job["name"] and started.get("uid") == job["uid"]
         and type(started.get("etag")) is str and bool(started["etag"])
         and started.get("automatic_replay") is False, "patched_engine_job_ack_changed")
    if mode == "inventory":
        need(target.validate_inventory(observed, image, target.source_contract()) == {
            "renderer_version": target.profile.RENDERER, "font_manifest_sha256": target.profile.FONT},
            "forward_renderer_changed")
    return {"stage": mode, "build_identity_sha256": target.sha(build), "image": image,
        "job_name": job["name"], "job_uid": job["uid"], "execution_name": execution["name"],
        "execution_uid": execution["uid"], "receipt_sha256": target.sha(observed),
        "execution_lifecycle": {key: execution.get(key) for key in ("createTime", "startTime", "completionTime")},
        "job_limits": {"task_count": job["template"]["taskCount"], "parallelism": job["template"]["parallelism"],
            "timeout": job["template"]["template"]["timeout"], "max_retries": job["template"]["template"]["maxRetries"],
            "resources": job["template"]["template"]["containers"][0]["resources"]},
        "native_acceptance": False, "automatic_replay": False}


def run_candidate(target, journal, *, stage=None, require_active=None):
    """Run exactly one stock Job. Operator cost review separates invocations."""
    need(type(target) is Probe and not target.read_only_release and target._v5
         and stage in {"inventory", "corpus"} and callable(require_active), "patched_engine_explicit_job_stage_required")
    require_active()
    build, image = target.build, target.build["immutable_image"]
    need(journal.is_absolute() and journal.resolve() == journal, "forward_fresh_native_journal")
    target.source_contract()
    need(target.build_identity(read_only_release=False, build_id=build["build_id"], nonce=build["nonce"], image=image) == build,
         "forward_candidate_build_changed")
    write = prior.build_module().write_new
    from infra.gcp import document_refresh_recovery as custody

    if stage == "inventory":
        need(not journal.exists(), "forward_fresh_native_journal")
        journal.mkdir(mode=0o700)
        write(journal / "binding.json", {"version": 5, "image": image, "build_identity": build})
    else:
        custody.private(journal, prior.build_module())
        need(_phase_read(journal / "binding.json") == {"version": 5, "image": image, "build_identity": build},
             "patched_engine_stage_binding_changed")
    clients = Clients(target.identity_profile, read_budget=diagnostics.ReadBudget(seconds=1800))
    try:
        if stage == "corpus":
            need(_phase_read(journal / "inventory.verified.json") == phase_receipt(target, clients, journal, "inventory"),
                 "patched_engine_inventory_prerequisite_changed")
        mode, handle = stage, target.handles(build)[stage]
        need(not any((journal / (mode + suffix)).exists() for suffix in (
            ".create.started.json", ".create.acknowledged.json", ".run.started.json",
            ".run.acknowledged.json", ".verified.json")), "patched_engine_existing_stage_reconcile_only")
        clients.preflight()
        body = target.candidate.desired(image, handle["nonce"], mode)
        try:
            clients.job(body["name"])
        except diagnostics.ForwardDenied as error:
            need(error.http_status == 404, "forward_job_absence_unverified")
        else:
            need(False, "forward_job_exists_reconcile_only")
        require_active()
        write(journal / (mode + ".create.started.json"), {"body_sha256": target.candidate.sha(body), "automatic_replay": False})
        created = clients.create(body)
        write(journal / (mode + ".create.acknowledged.json"), {"name": created.get("name")})
        deadline = time.monotonic() + 900
        prior.wait_operation(clients, created, "Job", deadline)
        job = clients.job(body["name"])
        target.validate_job(job, {"image": image, "mode": mode, "job_nonce": handle["nonce"]})
        need(prior.integer(job.get("executionCount", 0), 0) and job.get("latestCreatedExecution") in (None, {}),
             "forward_job_previously_executed")
        require_active()
        write(journal / (mode + ".run.started.json"), {"name": job["name"], "uid": job["uid"],
              "etag": job["etag"], "automatic_replay": False})
        started = clients.start(job)
        write(journal / (mode + ".run.acknowledged.json"), {"name": started.get("name")})
        prior.wait_operation(clients, started, "Execution", deadline)
        fact = phase_receipt(target, clients, journal, mode)
        write(journal / (mode + ".verified.json"), fact)
        if stage == "corpus":
            accepted = target.verify_successor(image, build, journal)
            write(journal / "acceptance.json", asdict(accepted))
            write(journal / "acceptance-scope.json", {"scope": "document_binary_corpus_only",
                "identity_profile_sha256": target.identity_profile.digest, "native_approval": False})
        return fact
    finally:
        clients.close()


def verify_document_profile(observer, source, profile):
    """Fresh native metadata, actual-policy delta, binary proof, TeX and routing."""
    authorization.origin()
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
    documents.check_snapshot(legacy_inspector, image, source.release, api_phase=True, coordinator_phase=True, source_patched_engine=True)
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
    return authorization.validate_receipt(profile, result)
