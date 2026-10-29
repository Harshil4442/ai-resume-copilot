"""Source-revision adapter for the existing native Job and transition controllers.

No image, Build, Job or acceptance is invented here. All evidence is freshly read
from the exact native source Build and singleton inventory/corpus executions.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta

from infra.gcp import document_candidate as courier
from infra.gcp import document_candidate_probe as corpus
from infra.gcp import document_candidate_release as v3
from infra.gcp import document_refresh_probe as prior
from infra.gcp import document_refresh_transition as transition
from infra.gcp import document_source_candidate as profile
from infra.gcp import document_source_payload as payload
from infra.gcp import document_source_receipt as receipt


class SourceProbe:
    POLICY = profile.POLICY_SHA256
    BASE_IMAGE, SERVICE_UID = prior.BASE_IMAGE, prior.SERVICE_UID
    NativeProofDenied = prior.NativeProofDenied
    CONTRACT_VERSION, REQUIRED_CASE_COUNT = 4, 16
    validate_serving = staticmethod(prior.validate_serving)
    sha = staticmethod(corpus.sha)

    def __init__(self, build, *, read_only_release=False, serving_read=False, root=profile.ROOT):
        keys = {"version", "kind", "nonce", "build_id", "build_resource", "builder_service_account",
            "source_bucket", "source_object", "source_generation", "source_archive_sha256",
            "refresh_dockerfile_sha256", "fixed_renderer_base", "immutable_image", "policy_sha256",
            "source_sha256", "source_commit", "qualification_sha256"}
        prior.need(type(read_only_release) is bool and type(serving_read) is bool
                   and type(build) is dict and set(build) == keys | ({"local_native_proof_sha256"}
                       if "local_native_proof_sha256" in build else set())
                   and type(build.get("version")) is int and build["version"] == 1
                   and build.get("kind") in {profile.KIND, profile.DEFINITIONS_KIND}
                   and (build.get("kind") == profile.KIND and build.get("fixed_renderer_base") is None
                        or build.get("kind") == profile.DEFINITIONS_KIND
                        and type(build.get("fixed_renderer_base")) is str
                        and prior.IMAGE.fullmatch(build["fixed_renderer_base"])
                        and build["fixed_renderer_base"] not in {courier.IMAGE, build.get("immutable_image")}),
                   "document_source_revision_wrong_build_kind")
        self.root = root
        self.source = profile.committed(build.get("source_commit"), root=root)
        prior.need(build.get("source_sha256") == self.source.sources
                   and build.get("policy_sha256") == self.source.policy
                   and build.get("qualification_sha256") == self.source.qualification_sha256
                   and (serving_read and "local_native_proof_sha256" not in build
                        or type(build.get("local_native_proof_sha256")) is str
                        and re.fullmatch(r"[a-f0-9]{64}", build["local_native_proof_sha256"])),
                   "document_source_revision_build_sources_changed")
        self.build = build
        self.read_only_release = read_only_release
        self.serving_read = serving_read
        self.profile, self.candidate = courier, self

    def source_contract(self):
        current = profile.committed(self.source.commit, root=self.root)
        prior.need(current.identity == self.source.identity, "document_source_revision_sources_changed")
        if not self.serving_read:
            profile.require_working_contract(current, self.root)
        return current.runtime

    def _rendered(self, image):
        retained = (profile.git(self.root, "show", self.source.commit + ":infra/gcp/document_source_payload.py")
                    if self.serving_read else None)
        return payload.rendered(image, self.source, root=self.root, retained_template=retained)

    def build_identity(self, *, read_only_release, build_id, nonce, image):
        builder = prior.build_module()
        verify = builder.verify_current_build_release if read_only_release else builder.verify_current_build
        actual = verify(build_id, nonce, image)
        prior.need(actual.get("kind") == self.build["kind"]
                   and all(actual.get(key) == value for key, value in self.build.items()
                           if key != "local_native_proof_sha256"),
                   "document_source_revision_build_readback_changed")
        return actual | ({"local_native_proof_sha256": self.build["local_native_proof_sha256"]}
                         if "local_native_proof_sha256" in self.build else {})

    def handles(self, build):
        prior.need(build == self.build and re.fullmatch(r"[a-f0-9]{32}", build.get("nonce", "")),
                   "document_source_revision_build_changed")
        return {"inventory": {"nonce": hashlib.sha256(("inventory:" + build["nonce"]).encode()).hexdigest()[:32]},
                "corpus": {"nonce": build["nonce"]}}

    def desired(self, image, nonce, mode):
        prior.need(image == self.build["immutable_image"] and mode in corpus.MODES,
                   "document_source_revision_job_scope_changed")
        body = corpus.desired(image, nonce, mode)
        body["labels"]["hirewiz-probe"] = "document-source-" + mode
        container = body["template"]["template"]["containers"][0]
        container["args"][2] = self._rendered(image)
        container["env"][1]["value"] = self.POLICY
        return body

    def validate_job(self, job, plan):
        corpus.validate_job_template(job, self.desired(plan["image"], plan["job_nonce"], plan["mode"]))

    def validate_execution(self, execution, job, plan):
        corpus.validate_execution_template(execution, job, self.desired(plan["image"], plan["job_nonce"], plan["mode"]))

    def validate_inventory(self, value, image, runtime):
        observed = corpus.validate_inventory(value, image, runtime, policy=self.POLICY, event=payload.INVENTORY_EVENT)
        prior.need(observed == {"renderer_version": courier.RENDERER, "font_manifest_sha256": courier.FONT},
                   "document_source_revision_renderer_requires_review")
        return observed

    def _observation(self, clients, mode, build=None):
        prior.need(build in (None, self.build), "document_source_revision_build_changed")
        handle = self.handles(self.build)[mode]
        plan = {"image": self.build["immutable_image"], "mode": mode, "job_nonce": handle["nonce"]}
        body = self.desired(plan["image"], handle["nonce"], mode)
        job = clients.job(body["name"])
        self.validate_job(job, plan)
        execution_name = prior.execution_reference(job, job.get("latestCreatedExecution", {}).get("name"))
        execution = clients.execution(execution_name)
        self.validate_execution(execution, job, plan)
        event = payload.INVENTORY_EVENT if mode == "inventory" else payload.CORPUS_EVENT
        return job, execution, corpus.native_payload(clients, job, execution, mode, event=event)

    def _acceptance(self, clients, *, fresh=True):
        runtime = self.source_contract()
        _, _, inventory = self._observation(clients, "inventory", self.build)
        identity = self.validate_inventory(inventory, self.build["immutable_image"], runtime)
        job, execution, value = self._observation(clients, "corpus", self.build)
        try:
            complete = receipt.validate(value, self.build["immutable_image"], self.POLICY, runtime)
        except (ValueError, TypeError, KeyError, OverflowError):
            raise prior.NativeProofDenied("document_source_revision_corpus_invalid") from None
        prior.need(complete is True and all(value["identity_preflight"][key] == observed
                   for key, observed in identity.items()), "document_source_revision_native_identity_changed")
        scans = [row["scan"] for row in value["cases"] if "scan" in row]
        prior.need(len(scans) == 8 and all(scan == scans[0] for scan in scans), "document_source_revision_scans_disagree")
        number, issued = scans[0]["definitions"].split("/", 1)
        stamp = datetime.fromisoformat(issued)
        prior.need(re.fullmatch(r"[0-9]{1,10}", number) and stamp.tzinfo is not None
                   and timedelta(0) <= datetime.now(UTC) - stamp < timedelta(days=1 if fresh else 3),
                   "document_source_revision_definitions_stale")
        build = self.build
        return prior.NativeAcceptance(build["immutable_image"], build["build_id"], build["build_resource"],
            build["source_archive_sha256"], build["refresh_dockerfile_sha256"], build["source_sha256"],
            job["name"], job["uid"], execution["name"], execution["uid"],
            hashlib.sha256(self._rendered(build["immutable_image"]).encode()).hexdigest(),
            corpus.sha(value), self.POLICY, courier.FONT, courier.RENDERER,
            (stamp + timedelta(days=3)).isoformat(), self.CONTRACT_VERSION, self.REQUIRED_CASE_COUNT)

    def verify_successor(self, image, build, journal):
        prior.need(not self.serving_read and build == self.build and image == build["immutable_image"],
                   "document_source_revision_candidate_changed")
        binding = journal / "binding.json"
        prior.need(binding.is_file() and not binding.is_symlink() and binding.stat().st_size <= 65536
                   and json.loads(binding.read_bytes()) == {"version": 4, "image": image, "build_identity": build},
                   "document_source_revision_journal_changed")
        self.build_identity(read_only_release=self.read_only_release, build_id=build["build_id"], nonce=build["nonce"], image=image)
        clients = prior.Clients(read_only_release=self.read_only_release)
        try:
            clients.preflight()
            return self._acceptance(clients)
        finally:
            clients.close()

    @staticmethod
    def policy_for_serving(metadata, image):
        containers = metadata.get("template", {}).get("containers", []) if type(metadata) is dict else []
        if len(containers) == 1 and containers[0].get("image") == image:
            rows = [row for row in containers[0].get("env", []) if row.get("name") == "DOCUMENT_WORKER_POLICY_SHA256"]
            if rows == [{"name": "DOCUMENT_WORKER_POLICY_SHA256", "value": profile.POLICY_SHA256}]:
                prior.need(prior.IMAGE.fullmatch(image) and image != courier.IMAGE, "document_source_revision_cross_profile_image")
                return profile.POLICY_SHA256
        return prior.policy_for_serving(metadata, image)

    def verify_current_successor(self, image, metadata):
        if self.policy_for_serving(metadata, image) != self.POLICY:
            return v3.verify_current_successor(image, metadata) if prior.policy_for_serving(metadata, image) == v3.POLICY else prior.verify_current_successor(image, metadata)
        labels = metadata.get("labels", {})
        build = self.build_identity(read_only_release=self.read_only_release,
            build_id=labels.get("hirewiz-refresh-build"), nonce=labels.get("hirewiz-refresh-nonce"), image=image)
        prior.need(labels.get("hirewiz-refresh-job") == "hirewiz-doc-six-" + self.handles(build)["corpus"]["nonce"][:12],
                   "document_source_revision_serving_labels_changed")
        clients = prior.Clients(read_only_release=self.read_only_release)
        try:
            actual = clients.preflight()["service"]
            fields = ("uid", "generation", "observedGeneration", "etag", "template", "traffic", "trafficStatuses",
                      "labels", "latestCreatedRevision", "latestReadyRevision")
            prior.need(all(actual.get(key) == metadata.get(key) for key in fields)
                       and actual.get("uid") == self.SERVICE_UID,
                       "document_source_revision_serving_changed")
            return self._acceptance(clients, fresh=False)
        finally:
            clients.close()

    def verify_caller_pins(self, clients, image, *, policy_sha256=None, caller_serving_validator=None):
        policy = self.POLICY if policy_sha256 is None else policy_sha256
        if policy != self.POLICY:
            if policy == v3.POLICY:
                return v3.verify_caller_pins(clients, image, policy_sha256=policy,
                                             caller_serving_validator=caller_serving_validator)
            return prior.verify_caller_pins(clients, image, policy_sha256=policy,
                                            caller_serving_validator=caller_serving_validator)
        expected = {"DOCUMENT_WORKER_URL": prior.WORKER_URL, "DOCUMENT_WORKER_IMAGE_DIGEST": image,
            "DOCUMENT_WORKER_POLICY_SHA256": self.POLICY, "DOCUMENT_WORKER_DOCX_RENDERER_VERSION": courier.RENDERER,
            "DOCUMENT_WORKER_DOCX_FONT_MANIFEST_SHA256": courier.FONT}
        serving = caller_serving_validator or prior.validate_serving
        for name in transition.CALLERS:
            service = clients.service(prior.PARENT + "/services/" + name)
            containers = service.get("template", {}).get("containers", [])
            prior.need(len(containers) == 1, "document_source_revision_caller_shape")
            revision = serving(clients, service, containers[0].get("image"), name)
            for row in (containers[0], revision["containers"][0]):
                prior.need(all([item for item in row.get("env", []) if item.get("name") == key]
                               == [{"name": key, "value": value}] for key, value in expected.items()),
                           "document_source_revision_caller_pins_changed")


def run_successor(build, journal, *, root=profile.ROOT):
    return v3.run_successor(build, journal, adapter=SourceProbe(build, root=root))


def promote(build, native_journal, transition_journal, *, application_context, root=profile.ROOT):
    """Reuse exact native proof/controller; unknown mutation intents never replay."""
    prior.need(application_context is not None and not transition_journal.exists(),
               "document_source_revision_fresh_transition_required")
    probe = SourceProbe(build, root=root)
    if native_journal.exists():
        accepted = probe.verify_successor(build["immutable_image"], build, native_journal)
    else:
        accepted = v3.run_successor(build, native_journal, adapter=probe)
    transition.require_current_acceptance(accepted, build["immutable_image"], probe)
    # An exclusive new-source intent prevents choosing another transition path
    # after a lost acknowledgement. Original journals are never rewritten.
    prior.build_module().write_new(native_journal / "transition.started.json", {
        "build_identity_sha256": corpus.sha(build), "transition_journal": str(transition_journal),
        "automatic_replay": False, "unknown_outcome": "read_only_reconcile_exact_transition_only"})
    http = prior.build_module().native_http()
    try:
        result = transition.transition(build["immutable_image"], build, native_journal, transition_journal,
            http=http, probe=probe, application_context=application_context)
    finally:
        http.close()
    return result | {"source_commit": build["source_commit"], "renderer_contract_version": 4,
        "required_case_count": 16, "definition_expires_at": accepted.definition_expires_at,
        "prior_evidence_retained": True, "prior_jobs_retained": True}


def current_probe(image, metadata, *, read_only_release=False):
    labels = metadata.get("labels", {})
    builder = prior.build_module()
    verify = builder.verify_current_build_release if read_only_release else builder.verify_current_build
    build = verify(labels.get("hirewiz-refresh-build"), labels.get("hirewiz-refresh-nonce"), image)
    return SourceProbe(build, read_only_release=read_only_release, serving_read=True)


def verify_current_successor(image, metadata, *, read_only_release=False):
    return current_probe(image, metadata, read_only_release=read_only_release).verify_current_successor(image, metadata)


def verify_caller_pins(clients, image, *, caller_serving_validator=None):
    worker = clients.service(prior.PARENT + "/services/hirewiz-document-inspector")
    probe = current_probe(image, worker, read_only_release=getattr(clients, "read_only_release", False))
    return probe.verify_caller_pins(clients, image, caller_serving_validator=caller_serving_validator)


def parent_before_build(image, current_source):
    """An observed current source profile is mandatory before a child Build."""
    clients = prior.Clients()
    try:
        metadata = clients.preflight()["service"]
        prior.need(metadata.get("template", {}).get("containers", [{}])[0].get("image") == image,
                   "document_source_revision_parent_not_current")
        probe = current_probe(image, metadata)
        accepted = probe.verify_current_successor(image, metadata)
        prior.need(accepted.image == image and accepted.policy_sha256 == current_source.policy
                   and accepted.source_sha256 == current_source.sources
                   and accepted.contract_version == 4 and accepted.required_case_count == 16
                   and accepted.renderer_version == courier.RENDERER and accepted.font_manifest_sha256 == courier.FONT,
                   "document_source_revision_parent_profile_changed")
    finally:
        clients.close()
