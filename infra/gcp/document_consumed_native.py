"""Read-only native authority for one consumed document-transition history.

No Build, Job, claim, PATCH, heartbeat or retry entrypoint exists here. Historical
consumption is proved separately from the application's current serving state.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import document_application_context as apps
import document_consumed_history as history
import document_refresh_final_readback as readback
import document_refresh_transition as transition


class ReadOnlyHttp:
    def __init__(self, delegate):
        self.delegate = delegate

    def request(self, uri, method="GET", body=None, headers=None, redirections=0, **kwargs):
        from document_refresh_auth import allowed

        target = urlsplit(uri)
        history.need(method == "GET" and body is None and redirections == 0 and not kwargs
                     and headers in (None, {"Content-Type": "application/json"})
                     and allowed(method, uri) and target.hostname in {
                         "run.googleapis.com", "storage.googleapis.com", "cloudbuild.googleapis.com"},
                     "audit_write_or_origin_refused")
        return self.delegate.request(uri, method=method, body=None, headers=headers, redirections=0)


class NativeAudit:
    def __init__(self, root, bundle, clients, http, probe):
        self.root, self.bundle, self.clients, self.probe = root, bundle, clients, probe
        self.http = ReadOnlyHttp(http)
        if bundle is not None:
            complete = history.read_tree(bundle / "40811330747")
            uids = {name: history.decoded(complete, "source-transition/close." + name + ".started.json")["service_uid"]
                    for name in apps.CALLERS}
            history.need(uids == apps.UIDS, "historical_caller_uid_changed")
        self.applications = apps.Applications(root, clients, self.get_json, apps.UIDS)

    def get_json(self, uri):
        response, raw = self.http.request(uri, method="GET", redirections=0)
        if response.status == 403:
            history.need(False, "cloudbuild_metadata_read_denied" if urlsplit(uri).hostname == "cloudbuild.googleapis.com"
                         else "run_metadata_read_denied")
        history.need(response.status == 200 and type(raw) is bytes and 0 < len(raw) <= 1048576,
                     "native_read_unavailable")
        value = json.loads(raw)
        history.need(type(value) is dict, "native_read_invalid")
        return value

    def verify_claims(self, complete):
        final = readback.final
        original = self.bundle / "40811330747/source-transition"
        partial = self.bundle / "40811330747/partial-transition"
        failed = self.bundle / "40811330747/final-transition"
        acceptance = history.sha(history.canonical(history.decoded(complete, "native-proof/acceptance.json")))
        first = final.partial.original_claim(transition, self.http, original)
        second, _ = final.second_claim(transition, self.http, partial, first, acceptance)
        third = readback._third_claim(transition, self.http, failed, first, second, acceptance)
        third_sha = history.sha(history.canonical(third))
        history.need((first, second, third_sha) == (
            "2f1cb2ee3ce7b66a229a8ce1b7b122077bfb4cab1a7797994bafa9a10f34b706",
            "f5888eafc2805c681ea25e3729a36113d6d4528094527b07526d0b4c222e88e1",
            "551652d7948cc5ffa44b843b3ecca912c397aba3d94aa89342fccf33f19514b9"),
            "exact_spent_claims_required")
        return {"first": first, "second": second, "third": third_sha}

    def verify_original_api_restore(self):
        plan, records = history.restore_evidence(self.root)
        pin = next(row for row in plan.services if row.name == apps.CALLERS[0])
        started = records["service.ai-resume-parser.restore.started.json"]
        acknowledged = records["service.ai-resume-parser.restore.acknowledged.json"]
        checked = records["service.ai-resume-parser.restore.checked.json"]
        reader = self
        class Revisions:
            @staticmethod
            def revision(service, name):
                return reader.clients.revision(apps.PARENT + "/services/" + service + "/revisions/" + name)
        template, traffic = history.maintenance.desired(plan, pin, Revisions(), "restore")
        close = self.get_json("https://run.googleapis.com/v2/" + readback.partial.MAINTENANCE_CLOSE_OPERATION)
        operation = self.get_json("https://run.googleapis.com/v2/" + acknowledged["operation"])
        prior, restored = close.get("response", {}), operation.get("response", {})
        name = apps.PARENT + "/services/" + pin.name
        body = {"name": name, "etag": prior.get("etag"), "template": template, "traffic": traffic}
        history.need(close.get("name") == readback.partial.MAINTENANCE_CLOSE_OPERATION
                     and close.get("done") is True and "error" not in close and prior.get("name") == name
                     and prior.get("uid") == pin.uid and str(prior.get("generation")) == "344"
                     and operation.get("name") == acknowledged["operation"] and operation.get("done") is True
                     and "error" not in operation and restored.get("name") == name and restored.get("uid") == pin.uid
                     and str(restored.get("generation")) == "345"
                     and restored.get("latestCreatedRevision") == checked["revision"]
                     and history.maintenance.coordinator.template_hash(restored.get("template", {}))
                         == history.maintenance.coordinator.template_hash(template)
                     and sorted(restored.get("traffic", []), key=history.canonical) == traffic
                     and history.sha(history.canonical(body)) == started["request_sha256"],
                     "historical_api_restore_operation_changed")
        revision = self.clients.revision(checked["revision"])
        history.maintenance.revision_ready(revision, pin.name, plan.revision(pin.name, "restore"))
        history.need(set(revision) <= set(pin.template_keys) | history.maintenance.REVISION_READ_OUTPUTS
                     and all(revision.get(key) == template.get(key) for key in pin.template_keys),
                     "historical_api_restore_revision_changed")
        return {"plan_sha256": history.RESTORE_PLAN, "operation": acknowledged["operation"],
                "revision": checked["revision"], "generation": 345, "request_sha256": started["request_sha256"]}

    def verify_current_applications(self):
        return self.applications.observe()

    def verify_current_document(self, complete):
        # The exact artifact hash establishes historical 581 acceptance. A later
        # accepted refresh can retire that old Job; verify the CURRENT native
        # Build/Job/Execution/log proof instead of depending forever on 581.
        worker = self.clients.preflight()["service"]
        image = worker["template"]["containers"][0]["image"]
        accepted = self.probe.verify_current_successor(image, worker)
        policy = self.probe.policy_for_serving(worker, image)
        self.probe.verify_caller_pins(self.clients, image, policy_sha256=policy,
                                     caller_serving_validator=self.applications.serving)
        history.need(self.clients.service(apps.PARENT + "/services/hirewiz-document-inspector") == worker,
                     "current_document_changed")
        return {"current_native_acceptance": asdict(accepted), "worker_generation": str(worker["generation"])}
