"""Exact ordinary release methods with inert metadata; no app/provider imports."""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("document_release_contract", ROOT / "infra/gcp/document_worker.py")
assert spec and spec.loader
documents = importlib.util.module_from_spec(spec)
spec.loader.exec_module(documents)
spec = importlib.util.spec_from_file_location("document_release_routing", ROOT / "infra/gcp/document_network.py")
assert spec and spec.loader
routing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(routing)
spec = importlib.util.spec_from_file_location("native_tex_release_contract", ROOT / "infra/gcp/native_tex_worker.py")
assert spec and spec.loader
native_tex = importlib.util.module_from_spec(spec)
with patch.dict("sys.modules", {"document_worker": documents}):
    spec.loader.exec_module(native_tex)


class Denied(RuntimeError):
    pass


def selected_methods(path, class_name, methods, namespace):
    """Compile only the named ordinary methods, preserving their exact bodies."""
    tree = ast.parse((ROOT / path).read_bytes())
    original = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    chosen = [node for node in original.body if isinstance(node, ast.FunctionDef) and node.name in methods]
    assert {node.name for node in chosen} == set(methods)
    cls = ast.ClassDef(name="Selected", bases=[], keywords=[], body=chosen, decorator_list=[])
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), cls], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(ROOT / path), "exec"), namespace)
    return namespace["Selected"]


def snapshots(worker=documents, *, runtime_phase=True):
    rendered = worker.render(worker.ACCEPTED_IMAGE, "a" * 40)
    service = copy.deepcopy(rendered["service"])
    revision_name = worker.RESOURCE + "/revisions/" + worker.SERVICE + "-00004-synthetic"
    service.update(uid="synthetic-service", generation="4", observedGeneration="4",
                   reconciling=False, uri=worker.ACCEPTED_ORIGIN,
                   latestReadyRevision=revision_name,
                   terminalCondition={"state": "CONDITION_SUCCEEDED"},
                   trafficStatuses=[{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST",
                                     "revision": revision_name, "percent": 100}])
    revision = copy.deepcopy(service["template"])
    revision.update(name=revision_name, uid="synthetic-revision", service=worker.RESOURCE,
                    conditions=[{"type": "Ready", "state": "CONDITION_SUCCEEDED"}])
    iam_phase = ("after_isolation_service_iam" if worker is documents else "runtime_service_iam") if runtime_phase else "initial_service_iam"
    return {"service": service, "revision": revision,
            "service_iam": rendered[iam_phase],
            "project_iam": {"bindings": []},
            "service_account": {"email": worker.SERVICE_ACCOUNT, "disabled": False},
            "service_account_keys": {"keys": []}}


def native_configuration(snapshot, tex_snapshot=None):
    tex_snapshot = snapshots(native_tex) if tex_snapshot is None else tex_snapshot
    release = SimpleNamespace(documents=documents, ReleaseDenied=Denied, REGION=documents.REGION,
                              PROJECT=documents.PROJECT, document_routing=routing, native_tex=native_tex)
    cls = selected_methods("infra/gcp/fresh_monetary_release.py", "FreshNativeBoundary",
                           {"verify_document_configuration", "verify_document_preparation", "_verify_document_workers"}, {"release": release})
    boundary = cls()
    reads = []

    def get(api, path):
        reads.append((api, path))
        for worker, selected in ((documents, snapshot), (native_tex, tex_snapshot)):
            if path == worker.RESOURCE:
                return selected["service"]
            if path == selected["service"]["latestReadyRevision"]:
                return selected["revision"]
        pytest.fail("Only the exact document and TeX metadata resources may be read")

    def cloud(*args):
        reads.append(args)
        if args[:3] == ("compute", "networks", "describe"):
            return {"name": routing.NETWORK, "selfLink": routing.NETWORK_URL, "autoCreateSubnetworks": False}
        if args[:4] == ("compute", "networks", "subnets", "describe"):
            return {"name": routing.SUBNET, "selfLink": routing.SUBNET_URL, "network": routing.NETWORK_URL,
                    "region": routing.REGION_URL, "ipCidrRange": routing.IP_RANGE, "privateIpGoogleAccess": True}
        if args[:3] == ("dns", "managed-zones", "describe"):
            return {"name": routing.DNS_ZONE, "dnsName": routing.DNS_NAME, "visibility": "private",
                    "privateVisibilityConfig": {"networks": [{"networkUrl": routing.NETWORK_URL}]}}
        if args[:3] == ("dns", "record-sets", "list"):
            assert args[-2:] == ("--limit", "5")
            return [{"name": "run.app.", "type": "A", "ttl": 300, "rrdatas": list(routing.PRIVATE_GOOGLE_IPS)},
                    {"name": "*.run.app.", "type": "CNAME", "ttl": 300, "rrdatas": ["run.app."]}]
        if args[:3] == ("compute", "routes", "list"):
            assert args[-2:] == ("--limit", "65")
            assert args[args.index("--filter") + 1] == "network=" + routing.NETWORK_URL
            return [{"network": routing.NETWORK_URL, "destRange": "0.0.0.0/0", "nextHopGateway": routing.GATEWAY_URL}]
        if args[:4] == ("iam", "service-accounts", "keys", "list"):
            selected = snapshot if args[-1] == documents.SERVICE_ACCOUNT else tex_snapshot
            assert args[-1] in {documents.SERVICE_ACCOUNT, native_tex.SERVICE_ACCOUNT}
            return selected["service_account_keys"]["keys"]
        if args[:3] == ("run", "services", "get-iam-policy"):
            selected = snapshot if args[3] == documents.SERVICE else tex_snapshot
            assert args[3] in {documents.SERVICE, native_tex.SERVICE}
            return selected["service_iam"]
        if args[:2] == ("projects", "get-iam-policy"):
            return snapshot["project_iam"]
        assert args[:3] == ("iam", "service-accounts", "describe")
        selected = snapshot if args[3] == documents.SERVICE_ACCOUNT else tex_snapshot
        assert args[3] in {documents.SERVICE_ACCOUNT, native_tex.SERVICE_ACCOUNT}
        return selected["service_account"]

    boundary._get, boundary._cloud = get, cloud
    return boundary, reads


def test_native_metadata_is_reread_without_claiming_invocation_or_isolation():
    boundary, reads = native_configuration(snapshots())
    source = SimpleNamespace(runtime_service_account=documents.API_INVOKER, release="a" * 40)
    for _ in range(2):
        report = boundary.verify_document_configuration(source)
        assert report["native_authenticated_metadata_read"] is True
        assert report["runtime_isolation_proven"] is report["effective_iam_proven"] is False
        assert report["api_wiring_enabled"] is False
        assert report["native_tex"]["effective_iam_proven"] is report["native_tex"]["native_compile_proven"] is report["native_tex"]["native_http_proven"] is False
    assert len(reads) == 32
    for worker in (documents, native_tex):
        assert reads.count(("run", worker.RESOURCE)) == 2


@pytest.mark.parametrize("worker", [documents, native_tex], ids=["document", "native-tex"])
@pytest.mark.parametrize("change", [
    lambda snap: snap["service"].update(uri="https://other.run.app"),
    lambda snap: snap["revision"]["containers"][0].update(image=snap["revision"]["containers"][0]["image"][:-1]
        + ("1" if snap["revision"]["containers"][0]["image"].endswith("0") else "0")),
    lambda snap: snap["revision"]["containers"][0]["env"][1].update(value="f" * 64),
    lambda snap: snap["service_iam"]["bindings"][0].update(members=["serviceAccount:hirewiz-api@" + documents.PROJECT + ".iam.gserviceaccount.com"]),
    lambda snap: snap["service"].update(ingress="INGRESS_TRAFFIC_ALL"),
    lambda snap: snap["service_account_keys"]["keys"].append({"keyType": "USER_MANAGED"}),
])
def test_live_worker_drift_refuses(change, worker):
    snapshot, tex_snapshot = snapshots(), snapshots(native_tex)
    change(snapshot if worker is documents else tex_snapshot)
    boundary, _ = native_configuration(snapshot, tex_snapshot)
    with pytest.raises(Denied):
        boundary.verify_document_configuration(SimpleNamespace(runtime_service_account=documents.API_INVOKER, release="a" * 40))


def test_old_runtime_refuses_before_any_native_read():
    boundary, reads = native_configuration(snapshots())
    with pytest.raises(Denied, match="fresh_document_runtime_identity_mismatch"):
        boundary.verify_document_configuration(SimpleNamespace(runtime_service_account="hirewiz-api@" + documents.PROJECT + ".iam.gserviceaccount.com", release="a" * 40))
    assert reads == []


def test_native_metadata_reader_is_required_without_configuration_fallback():
    boundary, _ = native_configuration(snapshots())
    del boundary._get
    with pytest.raises(Denied, match="fresh_document_native_metadata_reader_missing"):
        boundary.verify_document_configuration(SimpleNamespace(runtime_service_account=documents.API_INVOKER, release="a" * 40))


@pytest.mark.parametrize("method", ["stage_fresh", "verify_fresh_revision", "verify_fresh_serving"])
@pytest.mark.parametrize("refused", [False, True])
def test_native_document_gate_precedes_stage_revision_and_serving(method, refused):
    cls = selected_methods("infra/gcp/fresh_monetary_release.py", "FreshNativeBoundary", {method},
                           {"release": SimpleNamespace(Plan=object), "runtime_plan": lambda source, target: source})
    boundary, events = cls(), []

    def gate(source):
        events.append("document-configuration")
        if refused:
            raise Denied("synthetic-document-refusal")

    boundary.verify_document_configuration = gate
    boundary.stage = lambda *args: events.append("stage") or "synthetic-revision"
    boundary.verify_revision = lambda *args: events.append("revision")
    boundary.verify_serving = lambda *args: events.append("serving")
    args = (object(), object(), "synthetic-service")
    if method != "stage_fresh":
        args += ("synthetic-revision",)
    if refused:
        with pytest.raises(Denied, match="synthetic-document-refusal"):
            getattr(boundary, method)(*args)
        assert events == ["document-configuration"]
    else:
        getattr(boundary, method)(*args)
        assert events == ["document-configuration", {"stage_fresh": "stage", "verify_fresh_revision": "revision", "verify_fresh_serving": "serving"}[method]]


@pytest.mark.parametrize("execute,refused", [(True, True), (True, False), (False, True)])
def test_fresh_forward_sequence_requires_document_configuration_before_first_mutation(execute, refused):
    events = []

    class Source:
        release = "a" * 40

        def validate(self):
            events.append("source-validate")

    class Target:
        def validate(self, source):
            events.append("target-validate")

    class Transfer:
        def validate(self):
            events.append("tools-validate")

    release_tree = ast.parse((ROOT / "infra/gcp/monetary_release.py").read_bytes())
    constants = {}
    for node in release_tree.body:
        if isinstance(node, ast.Assign):
            for name in ("CUTOVER_CHAIN", "SERVICES"):
                if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                    constants[name] = ast.literal_eval(node.value)
    module = SimpleNamespace(Plan=Source, FreshTargetPlan=Target, ReleaseDenied=Denied,
                             CHAIN=constants["CUTOVER_CHAIN"], **constants)
    tree = ast.parse((ROOT / "infra/gcp/fresh_monetary_release.py").read_bytes())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "stage_fresh_release")
    namespace = {"release": module, "FreshSourcePlan": type("UnusedSource", (), {}), "TransferPlan": Transfer,
                 "_source_gate": lambda *args: events.append("source-gate"),
                 "_target_gate": lambda *args: events.append("target-gate")}
    selected = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), function], type_ignores=[])
    exec(compile(ast.fix_missing_locations(selected), "stage_fresh_release", "exec"), namespace)
    mutations = {"create_backup", "transfer_fresh", "grant_fresh_runtime", "migrate_fresh", "stage_fresh"}

    class Boundary:
        def verify_document_configuration(self, source):
            events.append("document-configuration")
            if refused:
                raise Denied("synthetic-document-refusal")

        def __getattr__(self, name):
            def call(*args):
                events.append(name)
                if name == "stage_fresh":
                    return args[2] + "-00042-inert"
            return call

    if execute and refused:
        with pytest.raises(Denied, match="synthetic-document-refusal"):
            namespace["stage_fresh_release"](Source(), Target(), Transfer(), Boundary(), execute=True)
        assert not mutations & set(events)
    else:
        result = namespace["stage_fresh_release"](Source(), Target(), Transfer(), Boundary(), execute=execute)
        if execute:
            assert result["staging_complete"] is True
            assert events.index("document-configuration") < events.index("create_backup")
            assert events.index("create_backup") < events.index("transfer_fresh") < events.index("migrate_fresh") < events.index("stage_fresh")
        else:
            assert result["mutation_count"] == 0
            assert "document-configuration" not in events and not mutations & set(events)


def runtime_namespace():
    tree = ast.parse((ROOT / "infra/gcp/monetary_release.py").read_bytes())
    names = {"PROJECT", "REGION", "SERVICES", "SAFE_ENV", "NATIVE_ENV", "MODEL_ENV", "MODEL_ALIASES"}
    chosen = [node for node in tree.body if (isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id in names for target in node.targets))
              or isinstance(node, ast.FunctionDef) and node.name == "runtime_environment"]
    namespace = {"documents": documents, "native_tex": native_tex, "ReleaseDenied": Denied, "document_routing": routing, "re": re}
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *chosen], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), "runtime_environment", "exec"), namespace)
    return namespace


def runtime_plan():
    return SimpleNamespace(release="a" * 40, runtime_secret="synthetic-runtime:1", expense_secret="synthetic-expense:1",
                           model_primary="synthetic-model", model_api_base="synthetic-origin",
                           model_policy_secret="synthetic-policy:1", model_key_secret="synthetic-key:1",
                           runtime_service_account=documents.API_INVOKER, image="synthetic-image@sha256:" + "b" * 64)


def test_exact_runtime_environment_pins_api_and_analysis_only():
    namespace = runtime_namespace()
    plan = runtime_plan()
    pins = {**documents.api_environment(documents.ACCEPTED_ORIGIN),
            **native_tex.api_environment(native_tex.ACCEPTED_ORIGIN, native_tex.ACCEPTED_IMAGE)}
    for index, service in enumerate(namespace["SERVICES"]):
        env, _, removed = namespace["runtime_environment"](plan, service)
        if index < 2:
            assert {key: env[key] for key in pins} == pins
            assert "NATIVE_TEX_IMAGE" in removed
            assert "NATIVE_TEX_IMAGE" not in env
            assert not set(pins) & set(removed)
        else:
            assert not set(pins) & set(env)
            assert set(pins) <= set(removed)
            assert set(namespace["NATIVE_ENV"]) <= set(removed)


def test_stage_attaches_direct_vpc_only_to_document_callers():
    namespace = runtime_namespace()
    cls = selected_methods("infra/gcp/monetary_release.py", "NativeBoundary", {"stage"}, namespace)
    boundary, calls = cls(), []
    for index, service in enumerate(namespace["SERVICES"]):
        boundary._cloud = lambda *args, selected=service: calls.append(args) or {"status": {"latestCreatedRevisionName": selected + "-00042-synthetic"}}
        boundary.stage(runtime_plan(), service)
        assert ("--network=" + routing.NETWORK in calls[-1]) is (index < 2)
        assert ("--subnet=" + routing.SUBNET in calls[-1]) is (index < 2)
        assert ("--vpc-egress=private-ranges-only" in calls[-1]) is (index < 2)


@pytest.mark.parametrize("annotation_change", [
    {}, {"run.googleapis.com/vpc-access-egress": "all-traffic"},
    {"run.googleapis.com/network-interfaces": '[{"network":"other","subnetwork":"other"}]'},
    {"run.googleapis.com/vpc-access-connector": "other"},
])
def test_revision_route_drift_stops_before_health_or_promotion(annotation_change):
    namespace = runtime_namespace()
    cls = selected_methods("infra/gcp/monetary_release.py", "NativeBoundary", {"verify_revision"}, namespace)
    boundary, plan = cls(), runtime_plan()
    service = namespace["SERVICES"][0]
    env, secrets, _ = namespace["runtime_environment"](plan, service)
    entries = [{"name": name, "value": value} for name, value in env.items()]
    for name, reference in secrets.items():
        secret, version = reference.split(":")
        entries.append({"name": name, "valueFrom": {"secretKeyRef": {"name": secret, "key": version}}})
    annotations = routing.caller_annotations()
    if annotation_change:
        annotations.update(annotation_change)
    else:
        annotations = {}
    revision = {"metadata": {"labels": {"serving.knative.dev/service": service}, "annotations": annotations},
                "spec": {"serviceAccountName": documents.API_INVOKER, "containers": [{"env": entries}]},
                "status": {"imageDigest": plan.image, "conditions": [{"type": "Ready", "status": "True"}]}}
    boundary._cloud = lambda *args: revision
    health = []
    boundary._verify_http_health = lambda *args: health.append(args)
    with pytest.raises(Denied, match="joined_document_caller_network_mismatch"):
        boundary.verify_revision(plan, service, service + "-00042-synthetic")
    assert health == []


def need(value, code):
    if not value:
        raise Denied(code)


def document_writer():
    namespace = {"native": SimpleNamespace(release=SimpleNamespace(documents=documents)),
                 "source_sql": SimpleNamespace(verify_cloud_isolated=lambda boundary: boundary.events.append("isolated")),
                 "need": need, "WriteDenied": Denied, "re": re,
                 "PROJECT": documents.PROJECT, "NativeAccount": lambda email, uid: SimpleNamespace(email=email, uid=uid)}
    cls = selected_methods("infra/gcp/fresh_cutover_writes.py", "OfficialWrites",
                           {"grant_document_invoker", "_account"}, namespace)
    writer = cls()
    writer.boundary = SimpleNamespace(events=[])
    writer._once = lambda phase: writer.boundary.events.append(phase)
    runtime = SimpleNamespace(email=documents.API_INVOKER, uid="123456789")
    policy = {"version": 1, "etag": "synthetic-etag", "bindings": []}
    calls = []

    def call(api, method, path, **options):
        calls.append((api, method, path, options))
        if api == "iam":
            return {"email": runtime.email, "uniqueId": runtime.uid, "disabled": False}
        if path == documents.RESOURCE:
            return {"name": path, "uri": documents.ACCEPTED_ORIGIN, "uid": "synthetic-uid", "reconciling": False}
        if method == "POST":
            policy.update(options["body"]["policy"])
        return copy.deepcopy(policy)

    writer._call = call
    return writer, runtime, policy, calls


def test_document_invoker_only_after_isolation_and_exact_etag_readback():
    writer, runtime, policy, calls = document_writer()
    writer.grant_document_invoker(runtime)
    assert writer.boundary.events == ["isolated", "grant-document-runtime"]
    assert policy["bindings"] == [{"role": "roles/run.invoker", "members": ["serviceAccount:" + documents.API_INVOKER]}]
    posts = [call for call in calls if call[1] == "POST"]
    assert len(posts) == 1 and posts[0][2] == documents.RESOURCE + ":setIamPolicy"
    assert posts[0][3]["body"]["policy"]["etag"] == "synthetic-etag"


@pytest.mark.parametrize("bindings", [
    [{"role": "roles/run.invoker", "members": ["allUsers"]}],
    [{"role": "roles/run.invoker", "members": ["serviceAccount:other@example.test"]}],
    [{"role": "roles/run.invoker", "members": ["serviceAccount:" + documents.API_INVOKER], "condition": {"expression": "true"}}],
])
def test_document_invoker_refuses_unknown_initial_grants_without_mutation(bindings):
    writer, runtime, policy, calls = document_writer()
    policy["bindings"] = bindings
    with pytest.raises(Denied, match="write_document_iam_initial_policy_refused"):
        writer.grant_document_invoker(runtime)
    assert not any(call[1] == "POST" for call in calls)


def test_document_invoker_removes_only_known_retired_account():
    writer, runtime, policy, _ = document_writer()
    policy["bindings"] = [{"role": "roles/run.invoker", "members": ["serviceAccount:hirewiz-api@" + documents.PROJECT + ".iam.gserviceaccount.com"]}]
    writer.grant_document_invoker(runtime)
    assert policy["bindings"] == [{"role": "roles/run.invoker", "members": ["serviceAccount:" + documents.API_INVOKER]}]


def test_native_desired_document_iam_is_reconciled_with_only_reads():
    writer, runtime, policy, calls = document_writer()
    policy["bindings"] = [{"role": "roles/run.invoker", "members": ["serviceAccount:" + documents.API_INVOKER]}]
    writer.grant_document_invoker(runtime)
    assert writer.boundary.events == ["isolated"]
    assert all(call[1] == "GET" for call in calls)
    assert [call[2] for call in calls].count(documents.RESOURCE) == 2


def test_document_iam_readback_drift_stops_after_one_mutation():
    writer, runtime, _, calls = document_writer()
    original = writer._call
    mutated = False

    def call(api, method, path, **options):
        nonlocal mutated
        result = original(api, method, path, **options)
        if method == "POST":
            mutated = True
        elif mutated and path.endswith(":getIamPolicy"):
            result["bindings"] = []
        return result

    writer._call = call
    with pytest.raises(Denied, match="write_document_iam_readback_drift"):
        writer.grant_document_invoker(runtime)
    assert sum(call[1] == "POST" for call in calls) == 1


@pytest.mark.parametrize("method,suffix,accepted", [
    ("GET", "", True), ("GET", ":getIamPolicy", True),
    ("POST", ":setIamPolicy", True), ("POST", ":getIamPolicy", False),
    ("GET", ":setIamPolicy", False), ("DELETE", "", False),
    ("GET", "/other-service", False),
])
@pytest.mark.parametrize("worker", [documents, native_tex], ids=["document", "native-tex"])
def test_native_run_route_is_fixed_to_document_service_and_iam(method, suffix, accepted, worker):
    calls = []

    def request(url, **options):
        calls.append((url, options))
        return SimpleNamespace(status=200), b"{}"

    namespace = {"native": SimpleNamespace(release=SimpleNamespace(documents=documents, native_tex=native_tex),
                                          object_pairs=lambda pairs: dict(pairs)),
                 "NEW_ACCOUNTS": ("hirewiz-runtime-20261014",),
                 "PROJECT": documents.PROJECT, "NUMBER": "157225724590",
                 "re": re, "json": json, "urlencode": urlencode,
                 "need": need, "WriteDenied": Denied}
    cls = selected_methods("infra/gcp/fresh_cutover_writes.py", "OfficialWrites", {"_call"}, namespace)
    writer = cls()
    writer.boundary = SimpleNamespace(reads=SimpleNamespace(_http=lambda: SimpleNamespace(request=request)))
    writer.requested_calls = 0
    writer.access_names = set()
    if accepted:
        assert writer._call("run", method, worker.RESOURCE + suffix) == {}
        assert calls[0][0] == "https://run.googleapis.com/v2/" + worker.RESOURCE + suffix
        assert calls[0][1]["redirections"] == 0
    else:
        with pytest.raises(Denied, match="write_fixed_route_or_call_bound_refused"):
            writer._call("run", method, worker.RESOURCE + suffix)
        assert calls == []
