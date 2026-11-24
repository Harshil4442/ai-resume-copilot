"""Close only new native admission on the verified restored application.

The original restore is immutable history. API saved/status reads stay enabled;
new uploads/legacy parsing and coordinator processing close. No image, identity,
credential, document proof, schema, purchase or model call can be changed here.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from urllib.parse import urlsplit

from infra.gcp import schema27_staged_restore as restore

rolling, owned = restore.rolling, restore.owned
ROOT = Path(__file__).resolve().parents[2]
API, ANALYSIS, COORDINATOR = "ai-resume-parser", "hirewiz-analysis-worker", "hirewiz-resume-coordinator"
READS, WRITES = (API, ANALYSIS, COORDINATOR), (API, COORDINATOR)
FLAGS = {API: ("RESUME_UPLOAD_ADMISSION_ENABLED",), COORDINATOR: (
    "RESUME_DIRECT_UPLOAD_ENABLED", "RESUME_UPLOAD_COORDINATOR_SCAN_ENABLED")}
KIND, PROOF = "hirewiz_native_admission_closure_v1", "hirewiz_native_admission_closed_v1"
SOURCE_FILES = (*owned.SOURCE_FILES, "infra/gcp/schema27_staged_restore.py",
                "infra/gcp/schema27_native_admission_closure.py")


def need(condition, code):
    rolling.need(condition, "native_admission_closure_" + code)


def source():
    value = owned.source_state()
    for name in SOURCE_FILES[-2:]:
        value["files"][name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    return value


def historical_source(pin):
    need(set(pin) == {"release", "tree", "files"} and set(pin["files"]) == set(SOURCE_FILES)
         and all(re.fullmatch(r"[a-f0-9]{40}", pin[k]) for k in ("release", "tree"))
         and all(rolling.HASH.fullmatch(v) for v in pin["files"].values()), "source_scope")
    need(rolling.release._json(rolling.canonical(pin)) == pin, "source_shape")
    tree = subprocess.check_output(["git", "rev-parse", pin["release"] + "^{tree}"], cwd=ROOT,
                                   stdin=subprocess.DEVNULL, timeout=10).decode().strip()
    need(tree == pin["tree"], "source_tree")
    for name, stamp in pin["files"].items():
        retained = subprocess.check_output(["git", "show", pin["release"] + ":" + name], cwd=ROOT,
                                          stdin=subprocess.DEVNULL, timeout=10)
        need(hashlib.sha256(retained).hexdigest() == stamp
             and hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == stamp, "source_bytes")


def parent_read(parent, *, current=False):
    """Run the unchanged original interpreter as a bounded read-only consumer."""
    need(set(parent) == {"root", "plan", "plan_sha256", "journal", "complete_sha256"}, "parent_scope")
    root = Path(parent["root"])
    need(root.is_absolute() and root.resolve() == root, "parent_root")
    raw = restore._private_read(parent["plan"], parent["plan_sha256"])
    plan = restore.load(raw, parent["plan_sha256"])
    state = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root,
                                    stdin=subprocess.DEVNULL, timeout=10).decode().strip()
    need(state == plan.source["release"] and not subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=root,
        stdin=subprocess.DEVNULL, timeout=10), "frozen_parent_changed")
    for name, stamp in plan.source["files"].items():
        need(hashlib.sha256((root / name).read_bytes()).hexdigest() == stamp, "parent_interpreter_changed")
    complete_path = Path(parent["journal"]) / "complete.json"
    complete = json.loads(restore._private_read(complete_path, parent["complete_sha256"]))
    code = ("import json,sys;from pathlib import Path;from infra.gcp import schema27_staged_restore as r;"
            "p=r.load(r._private_read(Path(sys.argv[1]),sys.argv[2]),sys.argv[2]);"
            "v=r.VerifiedRestore(p,r.owned.Journal(Path(sys.argv[3]),p));"
            "print(json.dumps({'custody':v.verify_custody(),'current':v.verify_current() if sys.argv[4]=='yes' else None}))")
    env = dict(os.environ, PYTHONPATH=str(root) + ":" + str(root / "backend"), PYTHONDONTWRITEBYTECODE="1",
               PYTHONNOUSERSITE="1", CLOUDSDK_CORE_ACCOUNT="harshilyogeshbhai886@gmail.com",
               CLOUDSDK_CORE_PROJECT="ai-resume-parser-482412", CLOUDSDK_BILLING_QUOTA_PROJECT="ai-resume-parser-482412",
               CLOUDSDK_CORE_UNIVERSE_DOMAIN="googleapis.com", CLOUDSDK_CORE_DISABLE_FILE_LOGGING="true",
               CLOUDSDK_CORE_LOG_HTTP="false")
    result = subprocess.run([sys.executable, "-B", "-c", code, parent["plan"], parent["plan_sha256"],
        parent["journal"], "yes" if current else "no"], cwd=root, env=env, stdin=subprocess.DEVNULL,
        capture_output=True, timeout=240, check=False)
    need(result.returncode == 0 and len(result.stdout) <= 131072, "parent_custody_unavailable")
    observed = rolling.release._json(result.stdout)
    need(observed["custody"] == complete and complete["all_homogeneous_source70"] is True,
         "genuine_parent_complete")
    if current:
        need(observed["current"] == complete["services"], "parent_current_changed")
    return plan, complete


def normalize(template, service):
    value = copy.deepcopy(template)
    if "revision" in value:
        value["revision"] = rolling.short_revision(value["revision"], service)
    return value


def projection(value, service):
    return {"service_uid": value["uid"], "generation": int(value["generation"]), "etag": value["etag"],
        "template_public_sha256": rolling.sha(restore._public_template(normalize(value["template"], service))),
        "static_public_sha256": rolling.sha({k: v for k, v in value.items() if k not in rolling.OUTPUTS | {"urls", "labels", "annotations"}}),
        "traffic": rolling.pinned_traffic(service, value),
        "created": rolling.short_revision(value["latestCreatedRevision"], service),
        "ready": rolling.short_revision(value["latestReadyRevision"], service)}


@dataclass(frozen=True)
class Pin:
    service: str
    before: dict
    prior_uid: str
    template_keys: tuple[str, ...]
    source: str
    image: str
    account: str
    revision: str


@dataclass(frozen=True)
class Plan:
    parent: dict
    services: tuple[Pin, ...]
    analysis: dict
    nonce: str
    source: dict
    journal_location: dict
    kind: str = KIND

    @property
    def digest(self):
        return rolling.sha(asdict(self))

    @property
    def tag(self):
        return "native-close-" + self.nonce[:12]

    def validate(self):
        need(type(self) is Plan and self.kind == KIND and re.fullmatch(r"[a-f0-9]{32}", self.nonce)
             and tuple(p.service for p in self.services) == WRITES, "plan_scope")
        need(set(self.parent) == {"root", "plan", "plan_sha256", "journal", "complete_sha256"}
             and all(rolling.HASH.fullmatch(self.parent[k]) for k in ("plan_sha256", "complete_sha256")), "parent_scope")
        for pin in self.services:
            need(type(pin) is Pin and pin.before["service_uid"] == restore.identities.readback.application.UIDS[pin.service]
                 and pin.before["created"] == pin.before["ready"] and pin.template_keys
                 and pin.revision == pin.service + "-native-close-" + self.nonce[:12]
                 and pin.revision != pin.before["created"] and rolling.HASH.fullmatch(pin.before["template_public_sha256"])
                 and re.fullmatch(r"[a-f0-9-]{36}", pin.prior_uid)
                 and re.fullmatch(r"[a-f0-9]{40}", pin.source)
                 and re.fullmatch(r"[A-Za-z0-9._/:-]+@sha256:[a-f0-9]{64}", pin.image)
                 and pin.account == (owned.NEW_API_EMAIL if pin.service == API else restore.identities.readback.COORDINATOR_ACCOUNT)
                 and type(pin.before["generation"]) is int and pin.before["generation"] > 0
                 and type(pin.before["etag"]) is str and pin.before["etag"]
                 and len(set(pin.template_keys)) == len(pin.template_keys) <= 50, "service_pin")
        need(self.analysis["service_uid"] == restore.identities.readback.application.UIDS[ANALYSIS], "analysis_scope")


def load(raw, digest):
    need(hashlib.sha256(raw).hexdigest() == digest and len(raw) <= 131072, "plan_file")
    value = rolling.release._json(raw)
    need(type(value) is dict and set(value) == {f.name for f in fields(Plan)}, "plan_shape")
    plan = Plan(**(value | {"services": tuple(Pin(**(p | {"template_keys": tuple(p["template_keys"])}))
                                              for p in value["services"])}))
    plan.validate()
    need(rolling.canonical(asdict(plan)) == rolling.canonical(value), "plan_canonical")
    return plan


def ready(value, service):
    restore.identities.readback.application.ready(value, service)
    need(projection(value, service)["created"] == projection(value, service)["ready"], "settled_revision_required")


def prior(boundary, pin):
    value = boundary.revision(pin.service, pin.before["created"])
    need(value is not None and value["uid"] == pin.prior_uid
         and any(r.get("type") == "Ready" and r.get("state") == "CONDITION_SUCCEEDED"
                 for r in value.get("conditions", [])), "prior_revision_changed")
    need(set(value) <= set(pin.template_keys) | restore.REVISION_READ_OUTPUTS | {"revision"}, "revision_fields_changed")
    template = {k: copy.deepcopy(value.get(k)) for k in pin.template_keys if k != "revision"}
    template["revision"] = pin.before["created"]
    env = rolling.environment(template)
    need(template["containers"][0]["image"] == pin.image and template["serviceAccount"] == pin.account
         and env["APP_RELEASE"].get("value") == pin.source, "prior_source_or_identity_changed")
    return value, template


def desired(boundary, pin):
    _, template = prior(boundary, pin)
    env = rolling.environment(template)
    for name in FLAGS[pin.service]:
        env[name] = {"name": name, "value": "false"}
    template["containers"][0]["env"] = list(env.values())
    template["revision"] = pin.revision
    return template


def traffic(plan, pin, phase):
    rows = copy.deepcopy(pin.before["traffic"])
    if phase == "route":
        rows = [r | {"revision": pin.revision} for r in rows]
    rows.append({"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": pin.revision, "tag": plan.tag})
    return sorted(rows, key=rolling.canonical)


def capture(parent, boundary, nonce, journal_directory):
    need(type(boundary) is NativeBoundary, "exact_boundary")
    base, complete = parent_read(parent, current=True)
    source_pin = source()
    rows = {s: boundary.service(s) for s in READS}
    for s, row in rows.items():
        ready(row, s)
    for s in (API, ANALYSIS):
        old = complete["services"][s]
        observed = projection(rows[s], s)
        need({k: observed[k] for k in old if k in observed} == {k: old[k] for k in old if k in observed},
             "restored_caller_changed")
    analysis = projection(rows[ANALYSIS], ANALYSIS)
    ae = rolling.environment(rows[ANALYSIS]["template"])
    need(ae.get("RESUME_DIRECT_UPLOAD_ENABLED") == {"name": "RESUME_DIRECT_UPLOAD_ENABLED", "value": "false"}
         and ae.get("OPTIONAL_AI_GENERATION_ENABLED") == {"name": "OPTIONAL_AI_GENERATION_ENABLED", "value": "false"},
         "analysis_admission_changed")
    pins = []
    for s in WRITES:
        row = rows[s]; before = projection(row, s); revision = before["created"]
        revisions = {r["revision"]: boundary.revision(s, r["revision"]) for r in before["traffic"]}
        need(revision in revisions and all(v is not None for v in revisions.values()), "reachable_revision_missing")
        template = normalize(row["template"], s)
        for value in revisions.values():
            need(restore.identities.readback.application.template(value, revision=True)
                 == {k: v for k, v in template.items() if k != "revision"}, "homogeneous_preimage_required")
        if s == COORDINATOR:
            projected = restore.identities.readback.project_service(s, row, revisions)
            need(projected == base.application.identities.promotion.before["services"][s], "coordinator_predecessor_changed")
        env = rolling.environment(template)
        need(env.get("OPTIONAL_AI_GENERATION_ENABLED", {"value": "false"}).get("value") == "false", "ai_admission_changed")
        if s == API:
            need(env.get("RESUME_DIRECT_UPLOAD_ENABLED") == {"name": "RESUME_DIRECT_UPLOAD_ENABLED", "value": "true"},
                 "saved_read_capability_changed")
        pins.append(Pin(s, before, revisions[revision]["uid"], tuple(template), env["APP_RELEASE"]["value"],
                        template["containers"][0]["image"], template["serviceAccount"], s + "-native-close-" + nonce[:12]))
    directory = Path(journal_directory)
    need(directory.is_absolute() and directory.resolve() == directory and directory.is_dir()
         and not any(directory.iterdir()), "new_journal_required")
    plan = Plan(parent, tuple(pins), analysis, nonce, source_pin,
                restore.scope.journal_location(type("L", (), {"directory": directory})()))
    plan.validate()
    for pin in plan.services:
        need(not any(r.get("tag") == plan.tag for r in pin.before["traffic"]), "new_tag_required")
        need(boundary.revision(pin.service, pin.revision) is None, "new_revision_required")
    verify_current(plan, boundary, None)
    return plan


def record(plan, journal, pin, phase):
    key = phase + "." + pin.service
    values = tuple(journal.read(key + suffix) for suffix in (".started", ".ack", ".checked"))
    started, ack, checked = values
    need(not journal.claimed(key + ".started") or started is not None, "incomplete_intent_reconcile_only")
    if started is None:
        need(ack is None and checked is None, "orphan_action")
    else:
        previous = pin.before if phase == "stage" else journal.read("stage." + pin.service + ".checked")
        need(previous is not None, "stage_health_required")
        expected = {"previous_generation": previous["generation"], "previous_etag": previous["etag"],
            "expected_generation": previous["generation"] + 1, "revision": pin.revision,
            "mask": "template,traffic" if phase == "stage" else "traffic", "traffic": traffic(plan, pin, phase),
            "replay_authorized": False, "plan_sha256": plan.digest}
        need(started == expected and ack is not None and set(ack) == {"operation", "intent_sha256", "plan_sha256"}
             and ack["intent_sha256"] == rolling.sha(started) and rolling.OPERATION.fullmatch(ack["operation"]),
             "missing_or_changed_native_ack_reconcile_only")
    return values


def verify_current(plan, boundary, journal):
    plan.validate(); historical_source(plan.source); parent_read(plan.parent)
    analysis = boundary.service(ANALYSIS)
    need(projection(analysis, ANALYSIS) == plan.analysis, "analysis_changed")
    result = {}
    for pin in plan.services:
        value = boundary.service(pin.service); ready(value, pin.service)
        _, baseline = prior(boundary, pin)
        stage = record(plan, journal, pin, "stage") if journal else (None, None, None)
        route = record(plan, journal, pin, "route") if journal else (None, None, None)
        active = route if route[0] else stage
        if active[0] is None:
            need(projection(value, pin.service) == pin.before
                 and normalize(value["template"], pin.service) == baseline, "initial_preimage_changed")
        else:
            assert active[1] is not None
            operation = boundary.operation(active[1]["operation"])
            need(operation.get("done") is True and "error" not in operation, "operation_not_complete")
            expected = desired(boundary, pin)
            need(value["uid"] == pin.before["service_uid"]
                 and projection(value, pin.service)["static_public_sha256"] == pin.before["static_public_sha256"]
                 and projection(value, pin.service)["created"] == pin.revision
                 and int(value["generation"]) == pin.before["generation"] + (2 if route[0] else 1)
                 and normalize(value["template"], pin.service) == expected
                 and rolling.pinned_traffic(pin.service, value) == traffic(plan, pin, "route" if route[0] else "stage"),
                 "native_preimage_changed")
            revision = boundary.revision(pin.service, pin.revision)
            need(revision is not None and any(r.get("type") == "Ready" and r.get("state") == "CONDITION_SUCCEEDED"
                 for r in revision.get("conditions", [])) and restore.identities.readback.application.template(revision, revision=True)
                 == {k: v for k, v in expected.items() if k != "revision"}, "closed_revision_changed")
        result[pin.service] = projection(value, pin.service)
        if active[0] is not None:
            result[pin.service].update(revision_uid=revision["uid"], prior_revision_uid=pin.prior_uid)
        if active[2] is not None:
            need(active[2] == result[pin.service] | {"health_verified": True, "plan_sha256": plan.digest}, "checked_receipt_changed")
    return result


def require_unpaused(journal):
    try:
        (journal.directory / "operator.pause-requested").lstat()
    except FileNotFoundError:
        return
    need(False, "operator_pause_requested")


def execute(mode, plan, boundary, journal):
    need(mode in {"close", "reconcile", "observe"} and type(boundary) is NativeBoundary
         and type(journal) is owned.Journal and journal.plan_hash == plan.digest, "exact_owner")
    need(source() == plan.source, "mutator_source_changed")
    with journal.locked():
        for pin in plan.services:
            for phase in ("stage", "route"):
                started, ack, _ = record(plan, journal, pin, phase)
                if started:
                    operation = boundary.operation(ack["operation"])
                    need("error" not in operation, "native_operation_failed_reconcile_only")
                    if operation.get("done") is not True:
                        return {"status": "same_native_operation_pending", "native_mutations": 0, "service": pin.service, "phase": phase}
        verify_current(plan, boundary, journal)
        mutations = 0
        for pin in plan.services:
            for phase in ("stage", "route"):
                key = phase + "." + pin.service
                started, _, checked = record(plan, journal, pin, phase)
                if started is None:
                    if mode != "close":
                        break
                    require_unpaused(journal)
                    verify_current(plan, boundary, journal)
                    before = boundary.service(pin.service)
                    previous = pin.before if phase == "stage" else journal.read("stage." + pin.service + ".checked")
                    need(previous is not None and projection(before, pin.service) == {k: previous[k] for k in pin.before},
                         "prewrite_changed")
                    body = {"name": before["name"], "etag": before["etag"], "traffic": traffic(plan, pin, phase)}
                    mask = "traffic"
                    if phase == "stage":
                        body["template"] = desired(boundary, pin); mask = "template,traffic"
                    need(boundary.service(pin.service) == before, "prewrite_changed")
                    started = {"previous_generation": previous["generation"], "previous_etag": previous["etag"],
                        "expected_generation": previous["generation"] + 1, "revision": pin.revision,
                        "mask": mask, "traffic": body["traffic"], "replay_authorized": False}
                    require_unpaused(journal)
                    journal.record(key + ".started", started); started = journal.read(key + ".started")
                    require_unpaused(journal)
                    ack = boundary.patch(pin.service, body, mask)
                    need(set(ack) == {"name"} and rolling.OPERATION.fullmatch(ack["name"]), "native_ack_unknown_reconcile_only")
                    journal.record(key + ".ack", {"operation": ack["name"], "intent_sha256": rolling.sha(started)})
                    mutations += 1
                    operation = boundary.operation(ack["name"])
                    need("error" not in operation, "native_operation_failed_reconcile_only")
                    if operation.get("done") is not True:
                        return {"status": "same_native_operation_pending", "native_mutations": mutations, "service": pin.service, "phase": phase}
                if checked is None and mode != "observe":
                    snapshot = verify_current(plan, boundary, journal)[pin.service]
                    boundary.health(plan, pin)
                    need(verify_current(plan, boundary, journal)[pin.service] == snapshot, "health_preimage_changed")
                    journal.record(key + ".checked", snapshot | {"health_verified": True})
                if journal.read(key + ".checked") is None:
                    break
        states = verify_current(plan, boundary, journal)
        complete = all(journal.read("route." + p.service + ".checked") for p in plan.services)
        proof = proof_record(plan, states, complete)
        if complete:
            if journal.read("complete") is None:
                need(mode != "observe", "completion_not_recorded"); journal.record("complete", proof)
            need(journal.read("complete") == proof | {"plan_sha256": plan.digest}, "completion_changed")
        return proof | {"native_mutations": mutations}


def proof_record(plan, states, complete):
    return {"kind": PROOF, "parent_restore_plan_sha256": plan.parent["plan_sha256"],
        "parent_restore_complete_sha256": plan.parent["complete_sha256"], "services": states,
        "analysis_unchanged": plan.analysis, "new_admission_closed": complete,
        "required_flags": {API: {"RESUME_UPLOAD_ADMISSION_ENABLED": False, "RESUME_DIRECT_UPLOAD_ENABLED": True},
            COORDINATOR: {"RESUME_DIRECT_UPLOAD_ENABLED": False, "RESUME_UPLOAD_COORDINATOR_SCAN_ENABLED": False}},
        "saved_api_reads_preserved": complete, "document_processing_enabled": False,
        "payment_activation_authorized": False, "replay_authorized": False}


class AdmissionRun(restore.native.NativeRun):
    SERVICES = READS

    def patch(self, service, body, mask):
        need(service in WRITES, "write_scope")
        return super().patch(service, body, mask)


class NativeBoundary(restore.native.NativeRollingBoundary):
    def __init__(self):
        super().__init__(ROOT)
        self._run: AdmissionRun | None = None

    @property
    def run(self):
        if self._run is None:
            self._run = AdmissionRun()
        return self._run

    def patch(self, service, body, mask):
        need(service in WRITES, "write_scope")
        return self.run.patch(service, body, mask)

    def health(self, plan, pin):
        if pin.service == API:
            return self.observer._verify_http_health(type("P", (), {"release": pin.source})(), API, pin.revision,
                                                      candidate_tag=plan.tag)
        import ssl
        import urllib.request

        import certifi

        current = self.service(COORDINATOR)
        tagged = [r for r in current.get("trafficStatuses", []) if r.get("tag") == plan.tag
                  and rolling.short_revision(r.get("revision", ""), COORDINATOR) == pin.revision]
        need(len(tagged) == 1, "health_destination")
        target, audience = tagged[0].get("uri", ""), current.get("uri", "")
        for value in (target, audience):
            url = urlsplit(value)
            need(url.scheme == "https" and url.hostname and url.hostname.endswith(".run.app")
                 and not(url.username or url.password or url.query or url.fragment) and url.port in {None, 443}
                 and url.path in {"", "/"}, "health_destination")
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_args, **_kwargs):
                return None
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); context.load_verify_locations(cafile=certifi.where())
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPSHandler(context=context))
        token = self.observer._health_id_token(audience)
        request = urllib.request.Request(target.rstrip("/") + "/api/health", headers={"Authorization": "Bearer " + token})
        try:
            with opener.open(request, timeout=30) as response:
                value = json.load(response)
        except Exception:  # noqa: BLE001 - never expose a URL or bearer token.
            raise rolling.RollingDenied("native_admission_closure_health_unavailable") from None
        need(value.get("ok") is True and value.get("role") == "resume-upload-coordinator"
             and value.get("scan_enabled") is False, "health_identity")

    def close(self):
        if self._run is not None:
            self._run.stock.session.close()


class ClosedAdmission:
    """Read-only historical receipt consumer, distinct from a current mutator."""
    def __init__(self, plan, journal):
        need(type(plan) is Plan and type(journal) is owned.Journal, "closed_proof_type")
        self.plan, self.journal = plan, journal
        self.verify_custody()

    @property
    def parent_restore_plan_sha256(self):
        return self.plan.parent["plan_sha256"]

    @property
    def parent_restore_complete_sha256(self):
        return self.plan.parent["complete_sha256"]

    def verify_custody(self):
        self.plan.validate(); historical_source(self.plan.source); parent_read(self.plan.parent); self.journal.verify()
        need(self.journal.plan_hash == self.plan.digest, "closure_owner_changed")
        complete = self.journal.read("complete")
        need(complete is not None and complete.get("kind") == PROOF and complete.get("new_admission_closed") is True
             and complete.get("parent_restore_plan_sha256") == self.parent_restore_plan_sha256
             and complete.get("parent_restore_complete_sha256") == self.parent_restore_complete_sha256, "closure_incomplete")
        states = {}
        for pin in self.plan.services:
            checks = {}
            for phase in ("stage", "route"):
                started, ack, checked = record(self.plan, self.journal, pin, phase)
                need(started is not None and ack is not None and checked is not None
                     and checked.get("health_verified") is True
                     and checked.get("service_uid") == pin.before["service_uid"]
                     and checked.get("generation") == pin.before["generation"] + (1 if phase == "stage" else 2)
                     and checked.get("created") == checked.get("ready") == pin.revision
                     and checked.get("traffic") == traffic(self.plan, pin, phase)
                     and checked.get("prior_revision_uid") == pin.prior_uid
                     and checked.get("static_public_sha256") == pin.before["static_public_sha256"],
                     "closure_checked_required")
                checks[phase] = checked
            need(checks["stage"]["revision_uid"] == checks["route"]["revision_uid"]
                 and checks["stage"]["template_public_sha256"] == checks["route"]["template_public_sha256"],
                 "closure_revision_changed")
            route = checks["route"]
            need(complete["services"][pin.service] == {k: v for k, v in route.items()
                 if k not in {"health_verified", "plan_sha256"}}, "closure_complete_changed")
            states[pin.service] = complete["services"][pin.service]
        need(complete == proof_record(self.plan, states, True) | {"plan_sha256": self.plan.digest}, "closure_proof_scope")
        return complete

    def verify_current(self, boundary=None):
        self.verify_custody(); own = boundary is None
        boundary = NativeBoundary() if own else boundary
        try:
            states = verify_current(self.plan, boundary, self.journal)
            need(states == self.journal.read("complete")["services"], "closure_current_changed")
            return states
        finally:
            if own:
                boundary.close()


def load_closed(*, plan_path, plan_sha256, journal_path):
    plan = load(restore._private_read(plan_path, plan_sha256), plan_sha256)
    return ClosedAdmission(plan, owned.Journal(Path(journal_path), plan))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("capture", "close", "reconcile", "observe"))
    parser.add_argument("--plan", type=Path, required=True); parser.add_argument("--plan-sha256")
    parser.add_argument("--journal", type=Path, required=True); parser.add_argument("--parent", type=Path)
    parser.add_argument("--parent-sha256"); parser.add_argument("--nonce")
    args = parser.parse_args(); boundary = NativeBoundary()
    try:
        if args.mode == "capture":
            parent = rolling.release._json(restore._private_read(args.parent, args.parent_sha256))
            plan = capture(parent, boundary, args.nonce, args.journal)
            owned.Journal(args.journal, plan, create=True)
            result = {"status": "native_admission_closure_captured", "native_mutations": 0,
                      "plan_sha256": restore.mixed.app.bridge._write_plan(args.plan, plan)}
        else:
            plan = load(restore._private_read(args.plan, args.plan_sha256), args.plan_sha256)
            result = execute(args.mode, plan, boundary, owned.Journal(args.journal, plan))
        print(rolling.canonical(result).decode()); return 0
    except Exception as error:  # noqa: BLE001 - emit only the fixed nonsecret diagnostic.
        result = restore.mixed.diagnostics.diagnostic(error, args.mode)
        if isinstance(error, rolling.RollingDenied) and re.fullmatch(r"native_admission_closure_[a-z_]{1,90}", str(error)):
            result["denial_code"] = str(error)
        print(rolling.canonical(result).decode()); return 1
    finally:
        boundary.close()


if __name__ == "__main__":
    raise SystemExit(main())
