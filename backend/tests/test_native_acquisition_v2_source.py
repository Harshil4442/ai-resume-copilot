"""Owned source/isolation metadata only; no corpus, cloud or native approval."""
from __future__ import annotations

import copy
import hashlib
import json
import marshal
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_native_acquisition_v2_engine import reviewed_toys as reviewed_toys
from test_native_acquisition_v2_engine import transport_toys as transport_toys

from evaluation.native_acquisition_v2 import channel as c
from evaluation.native_acquisition_v2 import engine as e
from evaluation.native_acquisition_v2 import isolation as i
from evaluation.native_acquisition_v2 import job as j
from evaluation.native_acquisition_v2 import qualification as q


@pytest.fixture
def bootstrap_source(tmp_path):
    """Two committed Python sources only; never clone/materialize a corpus."""
    root = (tmp_path / "owned-bootstrap-source").resolve()
    for name in (q.PREFIX + "qualification.py", q.PORT_PATH):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((q.ROOT / name).read_bytes())
    environment = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_NO_REPLACE_OBJECTS": "1"}

    def git(*args):
        return subprocess.run(["/usr/bin/git", "-c", "core.fsmonitor=false", "-C", str(root), *args],
            env=environment, stdin=subprocess.DEVNULL, capture_output=True, timeout=10, check=True)

    git("init", "-q")
    git("add", q.PREFIX + "qualification.py", q.PORT_PATH)
    git("-c", "user.name=Owned", "-c", "user.email=owned@example.invalid", "commit", "-qm", "Owned two-source bootstrap")
    return SimpleNamespace(root=root, git=git, temporary=tmp_path,
        marker=tmp_path / "untrusted-helper-executed")


def _fresh_bootstrap(state, mode, *, ports_only=False, prefix=True):
    private = (state.temporary / "private-child").resolve()
    private.mkdir(mode=0o700)
    script = state.root / q.PREFIX / "qualification.py"
    program = '''import sys,socket,subprocess,importlib.util,json
from pathlib import Path
root=Path(OWNED_ROOT)
counts={'network':0,'unexpected_process':0,'git':0,'local_bind_denied':0,'evaluation_data_open':0}
def denied(*a,**k):
 counts['network']+=1
 raise AssertionError('owned bootstrap denies network')
socket.create_connection=socket.getaddrinfo=socket.gethostbyname=denied
socket.socket.connect=socket.socket.connect_ex=socket.socket.listen=denied
def bind(*a,**k):
 counts['local_bind_denied']+=1
 raise OSError('owned IPv6 capability probe denied without bind')
socket.socket.bind=bind
from sqlalchemy.engine import Engine
Engine.connect=Engine.raw_connection=denied
real=subprocess.Popen
def process(argv,*a,**k):
 if not isinstance(argv,(tuple,list)) or not argv or argv[0]!='/usr/bin/git':
  counts['unexpected_process']+=1
  raise AssertionError('owned bootstrap denies process')
 if '-C' not in argv or argv[argv.index('-C')+1]!=str(root):
  counts['unexpected_process']+=1
  raise AssertionError('owned bootstrap denies foreign Git')
 counts['git']+=1
 return real(argv,*a,**k)
subprocess.Popen=process
def audit(event,args):
 if event=='open' and isinstance(args[0],(str,bytes)) and '/backend/evals/' in str(args[0]):
  counts['evaluation_data_open']+=1
  raise AssertionError('owned bootstrap denies evaluation data')
sys.addaudithook(audit)
sys.argv=[SCRIPT,'--root',str(root)]+(['--source-child'] if MODE in {'child','runtime_entry'} else [])
sys.path.insert(0,str(root/'backend'))
spec=importlib.util.spec_from_file_location('owned_heldout_qualifier',SCRIPT)
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module
try:
 spec.loader.exec_module(module)
 if PORTS_ONLY:
  ports=module._ports()
  result={'entry':ports.ENTRY,'helper':ports.__file__,'child_bootstrapped':module._CHILD_ROOT==root}
 elif MODE=='runtime_entry':
  module._ports()
  import importlib
  result=importlib.import_module('evaluation.development40_transport_v2')
 elif MODE=='standalone':
  from evaluation.native_acquisition_v2 import isolation
  plan=isolation.Plan.model_validate_json((root/'owned-plan.json').read_bytes())
  result=isolation.verify_development_transport_origin(plan,(root/'owned-lock.json').read_bytes(),root,
   runner_root=root,controller_root=root,sdk_library=root/'not-opened-sdk',account='owned@example.invalid')
 else:
  result=module._source_child(root) if MODE=='child' else module.source_identity(root)
 print(json.dumps({'result':result,'counts':counts}))
except Exception as error:
 print(json.dumps({'refused':type(error).__name__,'code':str(error),'counts':counts}))
'''.replace("OWNED_ROOT", repr(str(state.root))).replace("SCRIPT", repr(str(script))).replace(
        "MODE", repr(mode)).replace("PORTS_ONLY", repr(ports_only))
    command = [sys.executable, "-I", "-B"]
    if prefix:
        command += ["-X", "pycache_prefix=" + str(private / "cache")]
    result = subprocess.run([*command, "-"], input=program, text=True, cwd=private,
        env={"PATH": "/usr/bin:/bin", "HOME": str(private), "XDG_CONFIG_HOME": str(private), "LANG": "C.UTF-8",
             "PYTHONDONTWRITEBYTECODE": "1", "CLOUDSDK_CORE_DISABLE_FILE_LOGGING": "true", "CLOUDSDK_CORE_LOG_HTTP": "false"},
        capture_output=True, timeout=30, check=False)
    assert result.returncode == 0 and not result.stderr, (result.stdout, result.stderr)
    payload = json.loads(result.stdout)
    assert payload["counts"]["network"] == payload["counts"]["unexpected_process"] == 0
    assert payload["counts"]["evaluation_data_open"] == 0
    assert payload["counts"]["local_bind_denied"] <= 1
    return payload


@pytest.mark.parametrize("mode", ["parent", "child"])
def test_pinned_helper_bootstraps_from_source_in_fresh_interpreter(bootstrap_source, mode):
    payload = _fresh_bootstrap(bootstrap_source, mode, ports_only=True, prefix=mode == "child")
    assert payload["result"] == {"entry": "backend/scripts/native_acquisition_job.py",
        "helper": str(bootstrap_source.root / q.PORT_PATH), "child_bootstrapped": mode == "child"}


@pytest.mark.parametrize("mode", ["parent", "child"])
@pytest.mark.parametrize("change", ["dirty_helper", "committed_helper", "ignored_namespace", "linked_namespace",
    "sourceless_namespace", "valid_helper_bytecode"])
def test_fresh_source_paths_never_execute_untrusted_helper_or_namespace(bootstrap_source, mode, change):
    t = bootstrap_source
    marker = "from pathlib import Path\nPath(" + repr(str(t.marker)) + ").write_text('untrusted')\n"
    helper = t.root / q.PORT_PATH
    if change in {"dirty_helper", "committed_helper"}:
        helper.write_text(helper.read_text() + "\n" + marker)
        if change == "committed_helper":
            t.git("add", q.PORT_PATH)
            t.git("-c", "user.name=Owned", "-c", "user.email=owned@example.invalid", "commit", "-qm", "Owned helper successor")
    elif change in {"ignored_namespace", "linked_namespace"}:
        (t.root / ".git/info/exclude").write_text("backend/scripts/__init__.py\n")
        initializer = t.root / "backend/scripts/__init__.py"
        if change == "linked_namespace":
            outside = t.temporary / "outside-package.py"
            outside.write_text(marker)
            initializer.symlink_to(outside)
        else:
            initializer.write_text(marker)
    else:
        # Timestamp-valid cache whose co_filename claims the legitimate helper.
        import importlib.util
        import struct

        cache = t.root / "backend/scripts.pyc" if change == "sourceless_namespace" else (
            Path(importlib.util.cache_from_source(str(helper))))
        cache.parent.mkdir(exist_ok=True)
        stat = helper.stat()
        header = importlib.util.MAGIC_NUMBER + struct.pack("<III", 0, int(stat.st_mtime), stat.st_size)
        cache.write_bytes(header + marshal.dumps(compile(marker, str(helper), "exec")))
        (t.root / ".git/info/exclude").write_text("**/__pycache__/\n")
    payload = _fresh_bootstrap(t, mode,
        prefix=not (mode == "parent" and change in {"sourceless_namespace", "valid_helper_bytecode"}))
    assert "refused" in payload and not t.marker.exists()


def test_fresh_child_requires_private_cache_before_helper_execution(bootstrap_source):
    payload = _fresh_bootstrap(bootstrap_source, "child", ports_only=True, prefix=False)
    assert "refused" in payload and not bootstrap_source.marker.exists()


def test_fresh_child_never_executes_transport_runtime_byte_input(bootstrap_source):
    t = bootstrap_source
    source = t.root / q.RUNTIME_BYTE_INPUTS[0]
    source.parent.mkdir(exist_ok=True)
    source.write_text("from pathlib import Path\nPath(" + repr(str(t.marker)) + ").write_text('untrusted')\n")
    t.git("add", q.RUNTIME_BYTE_INPUTS[0])
    t.git("-c", "user.name=Owned", "-c", "user.email=owned@example.invalid", "commit", "-qm", "Owned runtime source data")
    payload = _fresh_bootstrap(t, "runtime_entry")
    assert payload["code"] == "held_out_source_bootstrap_import_refused" and not t.marker.exists()


@pytest.mark.parametrize("committed", [False, True])
def test_standalone_native_origin_gate_authenticates_helper_before_producer_import(
    bootstrap_source, transport_toys, isolated_metadata, committed,
):
    t = bootstrap_source
    # Derive only Python runtime imports as source data. This explicit toy
    # reader is not a committed-source or native provenance substitute.
    ports = SimpleNamespace(git=lambda root, _show, selector: (root / selector.split(":", 1)[1]).read_bytes())
    runtime = q._imports(q.ROOT, q.IMAGE_SEEDS, "owned", ports)
    names = sorted(set(runtime) | set(q.PACKAGE_PATHS))
    for name in names:
        path = t.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((q.ROOT / name).read_bytes())
    producer = t.root / q.CONTROLLER_ONLY_PATHS[0]
    producer_marker = t.temporary / "untrusted-producer-executed"
    producer.write_text("from scripts import qualify_native_quality_acquisition\nfrom pathlib import Path\nPath("
        + repr(str(producer_marker)) + ").write_text('untrusted')\n")
    t.git("add", *names, q.CONTROLLER_ONLY_PATHS[0])
    t.git("-c", "user.name=Owned", "-c", "user.email=owned@example.invalid", "commit", "-qm", "Owned standalone source closure")
    raw_lock = (transport_toys.root / "native-lock.json").read_bytes()
    plan = isolated_metadata.plan.model_copy(update={"native_development_lock_sha256": e.packet.digest(raw_lock)})
    (t.root / "owned-plan.json").write_text(plan.model_dump_json())
    (t.root / "owned-lock.json").write_bytes(raw_lock)
    helper = t.root / q.PORT_PATH
    helper.write_text(helper.read_text() + "\nfrom pathlib import Path\nPath("
        + repr(str(t.marker)) + ").write_text('untrusted')\n")
    if committed:
        t.git("add", q.PORT_PATH)
        t.git("-c", "user.name=Owned", "-c", "user.email=owned@example.invalid", "commit", "-qm", "Owned standalone helper successor")
    payload = _fresh_bootstrap(t, "standalone")
    expected = "held_out_source_bootstrap_helper_changed" if committed else "held_out_source_bootstrap_import_changed"
    assert payload["code"] == expected
    assert not t.marker.exists() and not producer_marker.exists()


def reference(name, *, size=1, sha256="1" * 64, generation="1"):
    return c.ObjectRef(name=name, generation=generation, size=size, sha256=sha256)


def runner_bytes(**changes):
    return e.packet.canonical(dict(schema_version="held-out-native-qualified-runner-v2",
        repository="https://github.com/Harshil4442/ai-resume-copilot.git", release="c" * 40,
        ci_run_id=1, build_id="11111111-1111-1111-1111-111111111111",
        image=i.RUNNER_REPOSITORY + "@sha256:" + "d" * 64,
        qualification_contract="hirewiz_schema26_app_rolling_v3", qualification_sha256="e" * 64,
        app_files=289, app_sha256="f" * 64, entry_path=i.ENTRY,
        entry_sha256=e.packet.file_digest(Path(j.__file__)), runtime_dependencies_sha256="2" * 64,
        proof_inputs_sha256="3" * 64, component_sha256="4" * 64,
        observed_at=datetime.now(UTC).isoformat(), ci_metadata_sha256="5" * 64,
        build_metadata_sha256="6" * 64) | changes)


def catalogue(rows, namespace, lock_sha="7" * 64):
    rows = sorted(copy.deepcopy(rows), key=lambda row: (row["group"], row["path"]))
    for index, row in enumerate(rows):
        row["object"]["name"] = f"input.common.{index:04}"
    return i.InputCatalog(schema_version="held-out-native-input-catalog-v2",
        provenance="mirrored_bytes_original_references_retained", namespace=namespace,
        native_development_lock_sha256=lock_sha, common_files=rows)


def native_metadata():
    return [{"group": "native", "path": path, "kind": "source",
             "object": reference("input.pending", sha256=hashlib.sha256(path.encode()).hexdigest()).model_dump()}
            for path in ["materialization-manifest.json", *(f"source-{index:03}.pdf" for index in range(160))]]


@pytest.fixture
def isolated_metadata():
    _, documents, tex, source = i._ports()
    now = datetime.now(UTC)
    experiment = "owned-source-metadata"
    namespace = f"gs://{i.BUCKET}/quality-held-out/{experiment}/"
    controls = [("native-lock.json", "7" * 64), ("funding.json", "8" * 64), ("runner-funding.json", "9" * 64)]
    common = catalogue([*native_metadata(), *({"group": "authority", "path": path, "kind": "control",
        "object": reference("input.pending", sha256=sha).model_dump()} for path, sha in controls)], namespace)
    native_files = [j.Input(path=row.path, object=row.object) for row in common.common_files if row.group == "native"]
    authority_files = [j.AuthorityInput(path=row.path, kind=row.kind, object=row.object)
                       for row in common.common_files if row.group == "authority"]
    review = reference("input.review.00.json", sha256="a" * 64)
    authority_files.append(j.AuthorityInput(path="dispatch-review.json", kind="control", object=review))
    inspector_image = f"{i.REGION}-docker.pkg.dev/{i.PROJECT}/cloud-run-source-deploy/{documents.SERVICE}@sha256:" + "a" * 64
    compiler_image = tex.REPOSITORY + "@sha256:" + "b" * 64
    auth = e.Authorization(schema_version=e.VERSION, purpose="unreviewed_held_out_native_capture_no_model_no_retail",
        state="authorized", authorized_by="owned-toy-not-native-authority", experiment_id=experiment,
        journal_path=str(j.WORKSPACE / "journal"), split="held_out", valid_from=now - timedelta(minutes=2),
        expires_at=now + timedelta(hours=1), packet_manifest_sha256=e.inputs.MANIFEST_SHA,
        native_manifest_sha256=next(row.object.sha256 for row in native_files if row.path == "materialization-manifest.json"),
        native_development_lock=e.Artifact(path="native-lock.json", sha256="7" * 64),
        funding_evidence=e.Artifact(path="funding.json", sha256="8" * 64), request_limit=320,
        reserved_minor_per_request=1, maximum_native_reserved_minor=320, currency="INR",
        inspector=e.development.Binding(url=documents.ACCEPTED_ORIGIN + "/inspect", image=inspector_image, policy=source.POLICY_SHA256),
        compiler=e.development.Binding(url=tex.ACCEPTED_ORIGIN + "/compile", image=compiler_image, policy=tex.POLICY_SHA256))
    dispatch = c.Dispatch(schema_version="reviewed-held-out-native-dispatch-v2",
        purpose="unreviewed_held_out_capture_external_authority_required", reviewed_by="toy-no-authority",
        external_authority_review=e.Artifact(path="dispatch-review.json", sha256=review.sha256),
        channel_binding=reference("channel-binding.json"), acknowledgment=reference("dispatch.00.json"),
        batch_index=0, before_checkpoint_sha256="1" * 64, parent_terminal=None, maximum_requests=1)
    bundle = j.Bundle(schema_version=j.VERSION, purpose="unreviewed_held_out_capture_no_model_no_retail",
        entry_sha256=e.packet.file_digest(Path(j.__file__)), job_name="hirewiz-quality-held-out-owned-metadata",
        configuration=c.Binding(schema_version=c.VERSION, namespace=namespace, acquisition_plan_sha256="1" * 64,
            channel_source_sha256=e.packet.file_digest(Path(c.__file__)), engine_source_sha256=e.packet.file_digest(Path(e.__file__)),
            maximum_output_bytes=c.MAX_BYTES, maximum_objects=c.MAX_OBJECTS), authorization=auth, dispatch=dispatch,
        runner_funding_evidence=e.Artifact(path="runner-funding.json", sha256="9" * 64),
        native_files=native_files, authority_files=authority_files)
    raw = e.packet.canonical(bundle.model_dump(mode="json"))
    stored = reference("input.bundle.00.json", size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    budget = i.InputBudget(schema_version="held-out-native-input-budget-v2",
        catalog_sha256=i.digest(common.model_dump(mode="json")), reviews=[i.InputSlot(batch_index=0, object=review)],
        bundles=[i.InputSlot(batch_index=0, object=stored)], unknown_slots=[])
    raw_runner = runner_bytes()
    plan = i.Plan(purpose="unreviewed_held_out_capture_no_model_no_retail", evaluator=i.EVALUATOR,
        evaluator_uid="109100000000000000001", bucket=i.BUCKET, bucket_metageneration="1",
        experiment_id=experiment, valid_from=now - timedelta(minutes=1), expires_at=now + timedelta(minutes=30),
        controller_sha256="d" * 64, job_name=bundle.job_name,
        qualified_runner_receipt_sha256=hashlib.sha256(raw_runner).hexdigest(),
        inspector_service_uid="owned-service", inspector_revision_uid="owned-revision",
        compiler_service_uid="owned-service", compiler_revision_uid="owned-revision",
        inspector_image=inspector_image, compiler_image=compiler_image, inspector_release="c" * 40, compiler_release="c" * 40,
        funding_evidence_sha256=auth.funding_evidence.sha256,
        acquisition_authorization_sha256=i.digest(auth.model_dump(mode="json")), native_development_lock_sha256="7" * 64,
        native_dispatch_review_sha256=review.sha256, input_catalog_sha256=i.digest(common.model_dump(mode="json")),
        input_budget_sha256=i.digest(budget.model_dump(mode="json")), bundle_name=stored.name,
        bundle_generation=stored.generation, bundle_sha256=stored.sha256)
    return SimpleNamespace(plan=plan, runner=raw_runner, bundle=bundle, raw=raw, catalog=common, budget=budget)


def worker_metadata(module, expected):
    _, documents, _, _ = i._ports()
    service = copy.deepcopy(expected["service"])
    rev = module.RESOURCE + "/revisions/" + module.SERVICE + "-owned-toy"
    service.update(uri=module.ACCEPTED_ORIGIN, uid="owned-service", generation="1", observedGeneration="1",
        reconciling=False, latestReadyRevision=rev, terminalCondition={"state": "CONDITION_SUCCEEDED"},
        trafficStatuses=[{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST", "revision": rev, "percent": 100}])
    revision = copy.deepcopy(service["template"])
    revision.update(name=rev, service=module.RESOURCE, uid="owned-revision",
        conditions=[{"type": "Ready", "state": "CONDITION_SUCCEEDED"}])
    return {"service": service, "revision": revision,
        "service_iam": {"version": 3, "bindings": [{"role": "roles/run.invoker", "members": sorted(
            "serviceAccount:" + name for name in (documents.API_INVOKER, documents.UPLOAD_COORDINATOR, i.EVALUATOR))}]},
        "project_iam": {"version": 3, "bindings": []},
        "service_account": {"email": module.SERVICE_ACCOUNT, "disabled": False}, "service_account_keys": {"keys": []},
        "upload_coordinator_service_account": {"name": f"projects/{i.PROJECT}/serviceAccounts/{documents.UPLOAD_COORDINATOR}",
            "projectId": i.PROJECT, "email": documents.UPLOAD_COORDINATOR, "uniqueId": documents.UPLOAD_COORDINATOR_UID, "disabled": False},
        "upload_coordinator_service_account_keys": {"keys": []}}


def isolation_snapshot(plan):
    _, documents, tex, _ = i._ports()
    return {"evaluator_service_account": {"name": f"projects/{i.PROJECT}/serviceAccounts/{i.EVALUATOR}",
        "projectId": i.PROJECT, "email": i.EVALUATOR, "uniqueId": plan.evaluator_uid, "disabled": False},
        "evaluator_service_account_keys": {"keys": []}, "project_iam": {"version": 3, "bindings": []},
        "bucket": {"name": i.BUCKET, "projectNumber": i.PROJECT_NUMBER, "location": "US-CENTRAL1", "storageClass": "STANDARD",
            "metageneration": "1", "iamConfiguration": {"uniformBucketLevelAccess": {"enabled": True}, "publicAccessPrevention": "enforced"}},
        "object_role": {"name": i.ROLE, "stage": "GA", "includedPermissions": ["storage.objects.get", "storage.objects.create"]},
        "bucket_iam": {"version": 3, "bindings": [{"role": i.ROLE, "members": ["serviceAccount:" + i.EVALUATOR], "condition": plan.condition}]},
        "inspector": worker_metadata(documents, documents.render(plan.inspector_image, plan.inspector_release, source_revision=True)),
        "compiler": worker_metadata(tex, tex.render(plan.compiler_image, plan.compiler_release, coordinator_phase=True))}


def job_snapshot(t):
    return {"name": f"projects/{i.PROJECT}/locations/{i.REGION}/jobs/{t.plan.job_name}", "uid": "owned-job",
        "generation": "1", "observedGeneration": "1", "reconciling": False,
        "terminalCondition": {"state": "CONDITION_SUCCEEDED"}, "template": i.expected_job(t.plan, t.runner, batch=0)}


def test_offline_isolation_and_future_bundle_leave_native_and_memory_unqualified(isolated_metadata):
    t = isolated_metadata
    snapshot = isolation_snapshot(t.plan)
    before = copy.deepcopy(snapshot)
    out = i.check(t.plan, snapshot, controller_sha256=t.plan.controller_sha256)
    i.check_job(t.plan, t.runner, job_snapshot(t), batch=0)
    i.check_future_bundle(t.plan, t.runner, t.raw, batch=0, catalog=t.catalog, budget=t.budget)
    assert snapshot == before and out["configuration_matches"]
    assert not out["native_approval"] and not out["processing_authorized"]
    assert not out["effective_iam_proven"] and not out["memory_qualified"] and not out["authorizes_retail"]
    assert out["provider_calls"] == out["model_calls"] == 0
    assert i.expected_job(t.plan, t.runner, batch=0)["template"]["containers"][0]["args"][:3] == ["-I", "-B", "/app/" + i.ENTRY]


@pytest.mark.parametrize("change", [
    lambda s: s["evaluator_service_account"].update(uniqueId="109100000000000000002"),
    lambda s: s["evaluator_service_account_keys"].update(keys=[{"keyType": "USER_MANAGED"}]),
    lambda s: s["bucket_iam"]["bindings"][0]["condition"].update(expression="true"),
    lambda s: s["bucket_iam"]["bindings"][0]["condition"].update(expression='resource.name.startsWith("quality-development/")'),
    lambda s: s["bucket_iam"]["bindings"].append({"role": i.ROLE, "members": ["serviceAccount:" + i.EVALUATOR], "condition": {"title": "old-dev", "expression": "true"}}),
    lambda s: s["object_role"]["includedPermissions"].append("storage.objects.delete"),
    lambda s: s["compiler"]["service"].update(uid="unrelated"),
    lambda s: s["inspector"]["service_iam"]["bindings"][0]["members"].append("serviceAccount:unrelated@example.test"),
])
def test_old_prefix_extra_rights_or_wrong_worker_uid_refuse(isolated_metadata, change):
    t = isolated_metadata
    snapshot = isolation_snapshot(t.plan)
    change(snapshot)
    with pytest.raises(ValueError):
        i.check(t.plan, snapshot, controller_sha256=t.plan.controller_sha256)


@pytest.mark.parametrize("change", [
    lambda value: value["template"].update(taskCount=2),
    lambda value: value["template"]["template"].update(maxRetries=1),
    lambda value: value["template"]["template"].update(volumes=[{"secret": "owned-toy"}]),
    lambda value: value["template"]["template"]["containers"][0]["resources"]["limits"].update(memory="4Gi"),
    lambda value: value["template"]["template"]["containers"][0]["env"][0].update(value="false"),
    lambda value: value["template"]["template"]["containers"][0]["env"].append({"name": "DATABASE_URL", "value": "owned-toy"}),
    lambda value: value["template"]["template"]["containers"].append({"image": "unrelated"}),
    lambda value: value["template"]["template"]["containers"][0].update(command=["sh"]),
    lambda value: value.update(overrides={"containerOverrides": []}),
])
def test_job_replay_credentials_4gi_or_run_overrides_refuse(isolated_metadata, change):
    t = isolated_metadata
    value = job_snapshot(t)
    change(value)
    with pytest.raises(ValueError):
        i.check_job(t.plan, t.runner, value, batch=0)


@pytest.mark.parametrize("changes", [{"schema_version": "native-quality-qualified-runner-v1"},
    {"entry_path": "backend/scripts/native_acquisition_job.py"}, {"app_sha256": "not-a-hash"}, {"ci_run_id": True}])
def test_old_or_malformed_runner_metadata_never_qualifies_v2(isolated_metadata, changes):
    t = isolated_metadata
    raw = runner_bytes(**changes)
    plan = t.plan.model_copy(update={"qualified_runner_receipt_sha256": hashlib.sha256(raw).hexdigest()})
    with pytest.raises(ValueError):
        i.bound_runner(plan, raw)


def test_catalog_reserves_all33_review_and_bundle_slots_before_they_exist(isolated_metadata):
    t = isolated_metadata
    empty = i.InputBudget(schema_version="held-out-native-input-budget-v2", catalog_sha256=i.digest(t.catalog.model_dump(mode="json")),
        reviews=[], bundles=[], unknown_slots=[])
    result = i.check_input_budget(t.catalog, empty)
    assert result["maximum_retained_input_bytes"] == sum(row.object.size for row in t.catalog.common_files) + 33 * (2 * 1024**2 + 256 * 1024)
    assert result["maximum_input_objects"] == 1903
    assert result["maximum_logical_input_read_bytes"] == 66 * 1024**3
    assert result["conservative_unenforced_retention_bytes"] == 66 * 1024**3
    assert not result["input_upload_enforcement_proven"]
    assert not result["memory_qualified"] and not result["reserved_expense_released"]


def test_full_kind_ceiling_proves1903_total_input_slots_without_payload_reads(isolated_metadata):
    t = isolated_metadata
    rows = native_metadata()
    for kind, (count, _) in e.KINDS.items():
        rows.extend({"group": "authority", "path": f"review/{kind}/{index:04}.dat", "kind": kind,
            "object": reference("input.pending").model_dump()} for index in range(count - (kind == "control")))
    value = catalogue(rows, t.plan.namespace)
    assert len(value.common_files) == 1837
    budget = i.InputBudget(schema_version="held-out-native-input-budget-v2", catalog_sha256=i.digest(value.model_dump(mode="json")),
        reviews=[i.InputSlot(batch_index=index, object=reference(f"input.review.{index:02}.json", size=e.MAX_METADATA)) for index in range(33)],
        bundles=[i.InputSlot(batch_index=index, object=reference(f"input.bundle.{index:02}.json", size=e.MAX_BUNDLE_BYTES)) for index in range(33)],
        unknown_slots=[])
    result = i.check_input_budget(value, budget)
    assert len(value.common_files) + len(budget.reviews) + len(budget.bundles) == result["maximum_input_objects"] == 1903
    with pytest.raises(ValueError):
        i.InputBudget.model_validate(budget.model_dump() | {"bundles": [*budget.bundles, budget.bundles[0]]})


def test_first_and_last_job_templates_keep_exact_schedule_resources_and_environment(isolated_metadata):
    t = isolated_metadata
    for batch in (0, 1, 32):
        plan = t.plan.model_copy(update={"bundle_name": f"input.bundle.{batch:02}.json"})
        result = i.expected_job(plan, t.runner, batch=batch)
        container = result["template"]["containers"][0]
        assert result["taskCount"] == result["parallelism"] == 1 and result["template"]["maxRetries"] == 0
        assert result["template"]["timeout"] == ("240s" if batch == 0 else "1200s")
        assert container["resources"]["limits"] == {"cpu": "2", "memory": "8Gi"}
        assert container["env"] == [{"name": j.NO_BUCKET_METADATA, "value": "true"}]
    with pytest.raises(ValueError):
        i.expected_job(t.plan, t.runner, batch=33)


def test_global_input_union_exact2gib_boundary_does_not_allocate_payloads(isolated_metadata):
    t = isolated_metadata
    rows = [row.model_dump() for row in t.catalog.common_files]
    for row in rows:
        row["object"]["size"] = (e.MAX_METADATA if row["group"] == "authority" or row["path"] == "materialization-manifest.json"
                                 else e.inspection.MAX_SOURCE_BYTES)
    for index in range(79):
        rows.append({"group": "authority", "path": f"review/page-{index:03}.png", "kind": "page",
                     "object": reference("input.pending", size=e.MAX_ARTIFACT if index < 78 else 11 * 1024**2 // 4).model_dump()})
    value = catalogue(rows, t.plan.namespace)
    assert sum(row.object.size for row in value.common_files) + i.RESERVED_VARIABLE_INPUT_BYTES == e.MAX_INPUT_BYTES
    rows[-1]["object"]["size"] += 1
    with pytest.raises(ValueError, match="experiment_input_union_bound"):
        catalogue(rows, t.plan.namespace)


@pytest.mark.parametrize("change", ["name", "generation", "omit", "unknown_clear", "unknown_rename"])
def test_input_generations_and_unknown_reservations_cannot_be_forgotten(isolated_metadata, change):
    t = isolated_metadata
    previous = t.budget.model_copy(update={"unknown_slots": ["input.bundle.01.json"]})
    data = previous.model_dump()
    if change == "name":
        data["bundles"][0]["object"]["name"] = "input.bundle.alternate.json"
    elif change == "generation":
        data["bundles"][0]["object"]["generation"] = "2"
    elif change == "omit":
        data["bundles"] = []
    elif change == "unknown_clear":
        data["unknown_slots"] = []
    else:
        data["unknown_slots"] = ["input.bundle.02.json"]
    with pytest.raises(ValueError):
        i.check_input_budget(t.catalog, i.InputBudget.model_validate(data), previous=previous)


@pytest.mark.parametrize("change", ["origin_mode", "path_alias", "remote_name", "extra_native", "control_saturation"])
def test_catalog_exact_membership_and_original_reference_mode_are_closed(isolated_metadata, change):
    data = isolated_metadata.catalog.model_dump()
    if change == "origin_mode":
        data["provenance"] = "retarget_original_references"
    elif change == "path_alias":
        data["common_files"][1]["path"] = data["common_files"][0]["path"]
    elif change == "remote_name":
        data["common_files"][1]["object"]["name"] = data["common_files"][0]["object"]["name"]
    elif change == "extra_native":
        data["common_files"].append(copy.deepcopy(data["common_files"][-1]))
    else:
        rows = data["common_files"]
        rows.extend({"group": "authority", "path": f"control-extra-{index:02}.json", "kind": "control",
                     "object": reference("input.pending").model_dump()} for index in range(29))
        with pytest.raises(ValueError, match="catalog_authority_count"):
            catalogue(rows, data["namespace"])
        return
    with pytest.raises(ValueError):
        i.InputCatalog.model_validate(data)


@pytest.mark.parametrize("change", ["review_generation", "native_generation", "native_lock", "unknown", "budget_hash"])
def test_future_bundle_cannot_switch_mirrors_or_input_budget(isolated_metadata, change):
    t = isolated_metadata
    data = t.bundle.model_dump(mode="json")
    plan, budget = t.plan, t.budget
    if change == "review_generation":
        next(row for row in data["authority_files"] if row["path"] == "dispatch-review.json")["object"]["generation"] = "2"
    elif change == "native_generation":
        data["native_files"][0]["object"]["generation"] = "2"
    elif change == "native_lock":
        data["authorization"]["native_development_lock"]["sha256"] = "0" * 64
    elif change == "unknown":
        budget = budget.model_copy(update={"unknown_slots": ["input.bundle.01.json"]})
        plan = plan.model_copy(update={"input_budget_sha256": i.digest(budget.model_dump(mode="json"))})
    else:
        plan = plan.model_copy(update={"input_budget_sha256": "0" * 64})
    raw = e.packet.canonical(data)
    plan = plan.model_copy(update={"bundle_sha256": hashlib.sha256(raw).hexdigest()})
    if change not in {"unknown", "budget_hash"}:
        stored = reference(plan.bundle_name, size=len(raw), sha256=plan.bundle_sha256)
        budget = budget.model_copy(update={"bundles": [i.InputSlot(batch_index=0, object=stored)]})
        plan = plan.model_copy(update={"input_budget_sha256": i.digest(budget.model_dump(mode="json"))})
    with pytest.raises(ValueError):
        i.check_future_bundle(plan, t.runner, raw, batch=0, catalog=t.catalog, budget=budget)


@pytest.fixture
def owned_source_view(tmp_path, monkeypatch):
    root = tmp_path / "owned-source"
    root.mkdir()
    for path in (*q.COMPONENT_PATHS, *q.SOURCE_ASSETS, *q.RUNTIME_BYTE_INPUTS, *q.CONTROLLER_ONLY_PATHS):
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text('"""Owned code fixture; no input dataset or provider access."""\n')
    (root / q.PREFIX / "engine.py").write_text("from . import bridge\n")
    (root / q.PREFIX / "isolation.py").write_text(
        "def owned_external_gate():\n    from evaluation import development40_qualification_v2\n")
    (root / q.RUNTIME_BYTE_INPUTS[0]).write_text("raise AssertionError('runtime source is data, never an import')\n")
    dependencies = {}
    for path in ("Dockerfile", "backend/pyproject.toml", "backend/uv.lock", ".github/workflows/ci.yml",
                 "backend/app/__init__.py", "backend/app/owned_helper.py"):
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text('"""Owned finite dependency."""\n')
        dependencies[path] = hashlib.sha256(destination.read_bytes()).hexdigest()
    for path, constants in q.LITERAL_INPUTS.items():
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("\n".join(name + " = " + repr(289 if name == "CANDIDATE_APP_FILES" else "owned-data") for name in constants))
    state = SimpleNamespace(root=root, dirty=False, head="a" * 40, dependencies=dependencies,
        tracked={*q.COMPONENT_PATHS, *q.SOURCE_ASSETS, *q.RUNTIME_BYTE_INPUTS,
            *q.CONTROLLER_ONLY_PATHS, *q.LITERAL_INPUTS, *dependencies})

    def git(where, *args):
        assert where == root
        if args[0] == "status":
            return b" M owned\n" if state.dirty else b""
        if args == ("rev-parse", "HEAD"):
            return state.head.encode()
        assert args[0] == "show" and args[1].startswith(state.head + ":")
        name = args[1].split(":", 1)[1]
        if name not in state.tracked:
            raise ValueError("owned_untracked_source")
        return (root / name).read_bytes()

    ports = SimpleNamespace(git=git, ABSENT_ANCESTORS=("backend/__init__.py",), HISTORICAL_RELEASE="0" * 40, source_identity=lambda where: {
        "execution_dependencies": dependencies, "component_paths": {}, "proof_inputs": {}, "current_proof_inputs": {}})
    monkeypatch.setattr(q, "_ports", lambda: ports)
    return state


def test_source_owns_exact8_package_files3_test_groups_and_dynamic_entry(owned_source_view):
    t = owned_source_view
    result = q.source_identity(t.root)
    assert len(q.PACKAGE_PATHS) == 8 and len(q.SUITES) == 3 and len(q.COMPONENT_PATHS) == 12
    assert set(result["component_paths"]) == set(q.COMPONENT_PATHS)
    assert result["entry_sha256"] == hashlib.sha256((t.root / q.ENTRY).read_bytes()).hexdigest()
    assert result["component_sha256"] == q.digest({name: result["component_paths"][name] for name in q.PACKAGE_PATHS})
    assert result["runtime_dependencies_sha256"] == q.digest({"files": result["execution_dependencies"],
        "literal_data": result["literal_image_inputs"]})
    bridge = q.PREFIX + "bridge.py"
    assert result["execution_dependencies"][bridge] == result["component_paths"][bridge] == result["proof_inputs"][bridge]
    assert result["runtime_byte_inputs"] == {name: result["execution_dependencies"][name] for name in q.RUNTIME_BYTE_INPUTS}
    assert set(q.CONTROLLER_ONLY_PATHS) <= set(result["controller_dependencies"])
    assert not set(q.CONTROLLER_ONLY_PATHS) & set(result["execution_dependencies"])
    assert not set(q.RUNTIME_BYTE_INPUTS) & set(result["controller_dependencies"])


def test_bridge_bytes_change_runtime_components_and_proof_together(owned_source_view):
    t = owned_source_view
    before = q.source_identity(t.root)
    bridge = t.root / q.PREFIX / "bridge.py"
    bridge.write_text(bridge.read_text() + "\nOWNED_BRIDGE_CHECK = True\n")
    t.head = "b" * 40
    after = q.source_identity(t.root)
    assert all(after[name] != before[name] for name in (
        "controller_sha256", "runtime_dependencies_sha256", "component_sha256", "proof_inputs_sha256"))


def test_producer_entry_bytes_are_bound_without_importing_or_executing_them(owned_source_view):
    t = owned_source_view
    before = q.source_identity(t.root)
    entry = t.root / q.RUNTIME_BYTE_INPUTS[0]
    entry.write_text(entry.read_text() + "\n# Owned changed transport source data.\n")
    t.head = "b" * 40
    after = q.source_identity(t.root)
    assert after["runtime_dependencies_sha256"] != before["runtime_dependencies_sha256"]
    assert after["runtime_byte_inputs"] != before["runtime_byte_inputs"]
    assert after["controller_sha256"] != before["controller_sha256"]
    assert after["component_sha256"] == before["component_sha256"]
    assert after["proof_inputs_sha256"] == before["proof_inputs_sha256"]


@pytest.mark.parametrize("change", ["untracked", "missing", "linked"])
def test_runtime_byte_input_requires_exact_committed_canonical_file(owned_source_view, change):
    t = owned_source_view
    entry = t.root / q.RUNTIME_BYTE_INPUTS[0]
    if change == "untracked":
        t.tracked.remove(q.RUNTIME_BYTE_INPUTS[0])
    else:
        entry.unlink()
        if change == "linked":
            entry.symlink_to(t.root / q.PREFIX / "bridge.py")
    with pytest.raises(ValueError):
        q.source_identity(t.root)


def test_development_qualifier_is_a_reached_controller_dependency_not_a_ninth_member(owned_source_view):
    t = owned_source_view
    before = q.source_identity(t.root)
    sibling = t.root / q.CONTROLLER_ONLY_PATHS[0]
    sibling.write_text(sibling.read_text() + "\nOWNED_EXTERNAL_GUARD = True\n")
    t.head = "b" * 40
    after = q.source_identity(t.root)
    assert after["controller_sha256"] != before["controller_sha256"]
    assert after["runtime_dependencies_sha256"] == before["runtime_dependencies_sha256"]
    assert after["component_sha256"] == before["component_sha256"]
    assert after["proof_inputs_sha256"] == before["proof_inputs_sha256"]
    assert q.CONTROLLER_ONLY_PATHS[0] not in q.PACKAGE_PATHS


@pytest.mark.parametrize("change", ["bridge_not_reached", "controller_not_reached", "runtime_controller_import",
    "runtime_byte_import", "controller_byte_import", "other_sibling"])
def test_source_roles_require_actual_finite_import_edges(owned_source_view, change):
    t = owned_source_view
    engine, isolation = (t.root / q.PREFIX / name for name in ("engine.py", "isolation.py"))
    if change == "bridge_not_reached":
        engine.write_text('"""No bridge import."""\n')
    elif change == "controller_not_reached":
        isolation.write_text('"""No external controller import."""\n')
    elif change == "runtime_controller_import":
        engine.write_text(engine.read_text() + "from evaluation import development40_qualification_v2\n")
    elif change == "runtime_byte_import":
        engine.write_text(engine.read_text() + "from evaluation import development40_transport_v2\n")
    elif change == "controller_byte_import":
        isolation.write_text(isolation.read_text() + "from evaluation import development40_transport_v2\n")
    else:
        sibling = "backend/evaluation/unreviewed_controller.py"
        (t.root / sibling).write_text('"""Owned unreviewed sibling."""\n')
        t.tracked.add(sibling)
        isolation.write_text(isolation.read_text() + "from evaluation import unreviewed_controller\n")
    with pytest.raises(ValueError):
        q.source_identity(t.root)


@pytest.mark.parametrize("change", ["extra", "extra_binary", "missing", "initializer", "backend_initializer", "dirty", "new_import", "test_import"])
def test_untracked_package_initializer_or_undeclared_helper_never_qualifies(owned_source_view, change):
    t = owned_source_view
    if change == "extra":
        (t.root / q.PREFIX / "unbound.py").write_text('"""Unbound."""')
    elif change == "extra_binary":
        (t.root / q.PREFIX / "unbound.so").write_bytes(b"OWNED-TOY-NOT-A-LIBRARY")
    elif change == "missing":
        (t.root / q.ENTRY).unlink()
    elif change == "initializer":
        (t.root / q.INITIALIZERS[1]).write_text('"""Owned."""\nraise AssertionError("never execute")\n')
    elif change == "backend_initializer":
        (t.root / "backend/__init__.py").write_text('"""New ancestor."""')
    elif change == "dirty":
        t.dirty = True
    elif change == "test_import":
        (t.root / q.ENTRY).write_text("from tests import owned_fixture\n")
    else:
        (t.root / q.ENTRY).write_text("from evaluation.unbound import helper\n")
    with pytest.raises(ValueError):
        q.source_identity(t.root)


@pytest.mark.parametrize("changed", ["old_entry", "old_helper", "new_guarded_source"])
def test_unchanged_v1_checker_rejects_new_v2_imports_in_its_execution_closure(tmp_path, monkeypatch, changed):
    old = q._ports()
    root = tmp_path / "owned-old-source"
    root.mkdir()
    helper = "backend/app/domains/analysis/native_input_acquisition.py"
    names = sorted({*old.RUNNER_PATHS, *old.CURRENT_EXECUTION_PATHS, helper})
    original = {}
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'"""Owned inert historical source."""\n')
        original[name] = path.read_bytes()
    current = set(names) | set(q.PACKAGE_PATHS)
    head = "f" * 40

    def readonly(where, *args):
        assert where == root
        if args == ("rev-parse", "HEAD"):
            return head.encode()
        if args == ("ls-tree", "-r", "--name-only", "HEAD"):
            return "\n".join(sorted(current)).encode()
        if len(args) == 2 and args[0] == "show" and args[1] in {
                head + ":" + name for name in old.CURRENT_EXECUTION_PATHS}:
            return (root / args[1].split(":", 1)[1]).read_bytes()
        assert args == ("ls-tree", "-r", "-z", "--full-tree", old.HISTORICAL_RELEASE)
        return b"".join(b"100644 blob " + hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw,
            usedforsecurity=False).hexdigest().encode() + b"\t" + name.encode() + b"\0" for name, raw in original.items())

    # Only the neutral read-only Git port uses this explicit owned source view;
    # checker constants/code and every V1 authorization type remain unchanged.
    monkeypatch.setattr(old, "git", readonly)
    old.dependency_hashes(root)
    if changed == "new_guarded_source":
        current.add("backend/scripts/unbound_v2.py")
    else:
        target = root / (old.ENTRY if changed == "old_entry" else helper)
        target.write_bytes(target.read_bytes() + b"\nimport evaluation.native_acquisition_v2.job\n")
    with pytest.raises(ValueError):
        old.dependency_hashes(root)


def test_local_reached_helper_must_be_bound_and_relative_import_is_resolved(owned_source_view):
    t = owned_source_view
    (t.root / q.ENTRY).write_text("from . import engine\nfrom app import owned_helper\n")
    q.source_identity(t.root)
    t.tracked.remove("backend/app/owned_helper.py")
    with pytest.raises(ValueError, match="untracked"):
        q.source_identity(t.root)


def test_reference_and_proof_changes_do_not_become_image_runtime_changes(owned_source_view):
    t = owned_source_view
    before = q.source_identity(t.root)
    (t.root / q.RUNBOOK).write_text("Owned changed operator documentation.\n")
    t.head = "b" * 40
    t.dependencies["backend/app/unused_reference.py"] = "0" * 64
    after = q.source_identity(t.root)
    assert after["controller_sha256"] != before["controller_sha256"]
    assert after["proof_inputs_sha256"] != before["proof_inputs_sha256"]
    assert after["runtime_dependencies_sha256"] == before["runtime_dependencies_sha256"]
    assert after["component_sha256"] == before["component_sha256"]
    assert "backend/app/unused_reference.py" not in after["execution_dependencies"]
    assert not after["legacy_reference"]["runtime_authority"]


@pytest.mark.parametrize("foreign_member", ["engine", "bridge"])
def test_runtime_imports_have_declared_file_origin_and_hash(monkeypatch, foreign_member):
    closure = q.package_members(q.ROOT)
    q._runtime_modules(closure)
    real_import = q.importlib.import_module

    def foreign(name):
        if name == "evaluation.native_acquisition_v2." + foreign_member:
            return SimpleNamespace(__file__=str(q.ROOT / "backend/tests/test_native_acquisition_v2_source.py"))
        return real_import(name)

    monkeypatch.setattr(q.importlib, "import_module", foreign)
    with pytest.raises(ValueError, match="runtime_module_provenance"):
        q._runtime_modules(closure)


def test_source_child_command_has_private_home_no_credentials_and_exact_entry(monkeypatch):
    observed = {"release": "c" * 40, "qualification_sha256": "1" * 64, "app_sha256": "2" * 64,
        "app_files": 289, "entry_sha256": "3" * 64, "runtime_dependencies_sha256": "4" * 64,
        "proof_inputs_sha256": "5" * 64, "component_sha256": "6" * 64}
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(observed).encode(), stderr=b"")

    monkeypatch.setattr(q.subprocess, "run", fake_run)
    assert q.runner_source(q.ROOT) == observed
    command, options = calls[0]
    assert command[1:3] == ["-I", "-B"] and str(Path(q.__file__).resolve()) in command
    assert options["timeout"] == 45 and options["stdin"] == subprocess.DEVNULL
    assert set(options["env"]) == {"PATH", "HOME", "XDG_CONFIG_HOME", "LANG", "PYTHONDONTWRITEBYTECODE",
        "CLOUDSDK_CORE_DISABLE_FILE_LOGGING", "CLOUDSDK_CORE_LOG_HTTP"}
    assert options["env"]["HOME"] == options["env"]["XDG_CONFIG_HOME"]


@pytest.fixture
def fresh_metadata_ports(isolated_metadata, monkeypatch):
    t = isolated_metadata
    ports = q._ports()
    native, contract, _ = ports._source_modules()
    ci = {"headSha": "c" * 40, "workflowName": "CI", "status": "completed", "conclusion": "success",
        "jobs": [{"name": name, "status": "completed", "conclusion": "success", "steps": [
            {"name": step, "status": "completed", "conclusion": "success"} for step in
            (contract.COMPATIBILITY_STEP, contract.PROTOCOL_STEP, contract.RETAINED_STEP)] if name == "backend" else []}
            for name in sorted(native.rolling.release.CI_JOBS)]}
    build = {"id": "11111111-1111-1111-1111-111111111111", "owned_toy": "not native provider evidence"}
    raw = runner_bytes(ci_metadata_sha256=q.digest(ci), build_metadata_sha256=q.digest(build))
    runner = i.QualifiedRunner.model_validate_json(raw)
    observed = {name: getattr(runner, name) for name in ("release", "qualification_sha256", "app_sha256", "app_files",
        "entry_sha256", "runtime_dependencies_sha256", "proof_inputs_sha256", "component_sha256")}
    identity = {name: observed[name] for name in ("entry_sha256", "runtime_dependencies_sha256", "proof_inputs_sha256", "component_sha256")}
    identity["controller_sha256"] = t.plan.controller_sha256
    state = SimpleNamespace(**vars(t), raw_runner=raw, ci=ci, build=build, identity=identity, observed=observed, calls=[])
    state.plan = t.plan.model_copy(update={"qualified_runner_receipt_sha256": hashlib.sha256(raw).hexdigest()})

    class Observer:
        def verify_source_image(self, **kwargs):
            state.calls.append(("source_image", kwargs))

        def _command(self, *args):
            state.calls.append(("metadata", args))
            if args[:2] == ("gh", "api"):
                return json.dumps({"path": ".github/workflows/ci.yml", "head_sha": "c" * 40,
                    "event": "push", "repository": {"full_name": native.rolling.release.REPOSITORY}}).encode()
            return json.dumps(state.ci).encode()

        def _cloud(self, *args):
            state.calls.append(("build", args))
            return state.build

    facade = SimpleNamespace(_source_modules=ports._source_modules, _successful_ci=ports._successful_ci,
        git=lambda root, *args: b"c" * 40)
    monkeypatch.setattr(q, "_ports", lambda: facade)
    monkeypatch.setattr(q, "_observer", lambda root: Observer())
    monkeypatch.setattr(q, "source_identity", lambda root: state.identity)
    monkeypatch.setattr(q, "runner_source", lambda root: state.observed)
    monkeypatch.setenv("CLOUDSDK_CORE_DISABLE_FILE_LOGGING", "true")
    monkeypatch.setenv("CLOUDSDK_CORE_LOG_HTTP", "false")
    return state


def test_fresh_verifier_requires_actual_source_build_all5ci_and_rechecks(fresh_metadata_ports):
    t = fresh_metadata_ports
    result = q.verify_qualified_runner(t.plan, t.raw_runner, runner_root=q.ROOT, controller_ci_run_id=1)
    assert result["fresh_source_CI_Build_verified"]
    assert len([call for call in t.calls if call[0] == "source_image"]) == 1
    assert len([call for call in t.calls if call[0] == "build"]) == 1
    assert not result["processing_authorized"] and not result["native_approval"] and not result["memory_qualified"]


def test_current_proof_role_does_not_rewrite_retained_runner_receipt(fresh_metadata_ports):
    t = fresh_metadata_ports
    original = bytes(t.raw_runner)
    t.identity["proof_inputs_sha256"] = "0" * 64
    result = q.verify_qualified_runner(t.plan, t.raw_runner, runner_root=q.ROOT, controller_ci_run_id=1)
    assert t.raw_runner == original and result["runner_receipt_sha256"] == hashlib.sha256(original).hexdigest()
    assert result["fresh_source_CI_Build_verified"] and not result["processing_authorized"]


@pytest.mark.parametrize("change", ["missing_job", "failed_job", "missing_step", "wrong_head", "build_hash", "source_entry", "source_component"])
def test_failed_incomplete_or_changed_source_metadata_cannot_qualify(fresh_metadata_ports, change):
    t = fresh_metadata_ports
    if change == "missing_job":
        t.ci["jobs"].pop()
    elif change == "failed_job":
        t.ci["jobs"][0]["conclusion"] = "failure"
    elif change == "missing_step":
        next(job for job in t.ci["jobs"] if job["name"] == "backend")["steps"].pop()
    elif change == "wrong_head":
        t.ci["headSha"] = "0" * 40
    elif change == "build_hash":
        t.build["id"] = "changed"
    else:
        t.observed["entry_sha256" if change == "source_entry" else "component_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        q.verify_qualified_runner(t.plan, t.raw_runner, runner_root=q.ROOT, controller_ci_run_id=1)


def test_default_ci_explicitly_checks_new_namespace_with_locked_static_tools(tmp_path):
    """These paths are outside the unchanged workflow's explicit static scopes."""
    backend = q.ROOT / "backend"
    environment = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path),
        "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "MYPY_CACHE_DIR": str(tmp_path / "mypy")}
    targets = ["evaluation", *(str(Path(path).relative_to("backend")) for path in q.SUITES)]
    for module, args in (("ruff", ["check"]), ("mypy", [])):
        result = subprocess.run([sys.executable, "-I", "-B", "-m", module, *args, *targets], env=environment,
            cwd=backend, stdin=subprocess.DEVNULL, capture_output=True, timeout=60, check=False)
        assert result.returncode == 0, (module, result.stdout[-65536:].decode(), result.stderr[-65536:].decode())
        assert len(result.stdout) + len(result.stderr) <= 1024**2


@pytest.fixture
def development_origin_ports(transport_toys, isolated_metadata, monkeypatch, tmp_path):
    """Owned metadata ports; real producer Job checker, no SDK/auth/GETs."""
    from evaluation import development40_qualification_v2 as development_source

    t = transport_toys
    raw_lock = (t.root / "native-lock.json").read_bytes()
    state = SimpleNamespace(toys=t, raw_lock=raw_lock,
        plan=isolated_metadata.plan.model_copy(update={"native_development_lock_sha256": e.packet.digest(raw_lock)}),
        runner_root=tmp_path / "owned-runner-source", controller_root=tmp_path / "owned-controller-source",
        sdk_library=tmp_path / "owned-sdk-not-opened", account="owned-reader@example.invalid", calls=[], gets=[],
        controller={"release": t.origin["controller_release"], "qualification_sha256": t.origin["controller_source_sha256"]},
        observed=copy.deepcopy(t.origin["runner_source"]), compatibility=t.runner_data["app_compatibility_sha256"],
        ci=json.loads((t.root / t.lock["ci_metadata"]["path"]).read_bytes()),
        controller_ci=copy.deepcopy(t.origin["controller_ci_metadata"]),
        build=json.loads((t.root / t.lock["build_metadata"]["path"]).read_bytes()),
        jobs={value["name"]: copy.deepcopy(value) for value in t.transport_jobs}, executions={}, after_reads=lambda: None)
    for batch in t.evidence["native_batches"]:
        value = json.loads((t.root / batch["execution"]["path"]).read_bytes())
        state.executions[value["name"]] = value

    def source_identity(root):
        assert root == state.controller_root
        state.calls.append(("controller_source", root))
        return copy.deepcopy(state.controller)

    def runner_source(root):
        assert root == state.runner_root
        state.calls.append(("runner_source", root))
        return copy.deepcopy(state.observed)

    class Observer:
        def __init__(self, root):
            assert root in {state.runner_root, state.controller_root}
            self.root = root

        def verify_source_image(self, **kwargs):
            state.calls.append(("source_image", kwargs))

        def _cloud(self, *args):
            assert args == ("builds", "describe", t.runner_data["build_id"], "--region", i.REGION)
            state.calls.append(("build", args))
            return copy.deepcopy(state.build)

    def fingerprint(root, prior):
        assert root == state.runner_root and prior == {"owned": b"source-only"}
        state.calls.append(("compatibility", root))
        return state.compatibility

    def prior_files(root, prior):
        assert root == state.runner_root and prior == "0" * 40
        return {"owned": b"source-only"}

    def successful_ci(observer, root, head, run_id):
        assert type(observer) is Observer and observer.root == root
        state.calls.append(("ci", root, head, run_id))
        if root == state.runner_root:
            assert (head, run_id) == (t.runner_data["release"], t.runner_data["ci_run_id"])
            return copy.deepcopy(state.ci)
        assert (head, run_id) == (t.origin["controller_release"], t.origin["controller_ci_run_id"])
        return copy.deepcopy(state.controller_ci)

    class Reads:
        def get(self, kind, name, options):
            assert kind == "run" and options == {}
            state.gets.append(name)
            if name in state.jobs:
                value = state.jobs[name]
            else:
                assert name in state.executions
                value = state.executions[name]
            if len(state.gets) == 18:
                state.after_reads()
            return copy.deepcopy(value)

    def reads(sdk_library, account):
        assert (sdk_library, account) == (state.sdk_library, state.account)
        state.calls.append(("read_port", sdk_library, account))
        return Reads()

    monkeypatch.setattr(development_source, "source_identity", source_identity)
    monkeypatch.setattr(development_source, "runner_source", runner_source)
    monkeypatch.setattr(development_source.q, "_source_modules", lambda: (
        SimpleNamespace(Source26Boundary=Observer),
        SimpleNamespace(PRIOR_RELEASE="0" * 40, source_fingerprint=fingerprint),
        SimpleNamespace(prior_app_files=prior_files)))
    monkeypatch.setattr(development_source.q, "_successful_ci", successful_ci)
    monkeypatch.setattr(i, "_development_reads", reads)
    monkeypatch.setenv("CLOUDSDK_CORE_DISABLE_FILE_LOGGING", "true")
    monkeypatch.setenv("CLOUDSDK_CORE_LOG_HTTP", "false")
    return state


def _verify_owned_development_origin(state):
    return i.verify_development_transport_origin(state.plan, state.raw_lock, state.toys.root,
        runner_root=state.runner_root, controller_root=state.controller_root,
        sdk_library=state.sdk_library, account=state.account)


def test_development_origin_external_gate_requires18_exact_reads_and_keeps_all_authority_false(development_origin_ports):
    t = development_origin_ports
    result = _verify_owned_development_origin(t)
    wanted: list[str] = []
    for batch in t.toys.origin["batches"]:
        name = "projects/ai-resume-parser-482412/locations/us-central1/jobs/" + batch["resource_plan"]["job_name"]
        execution = json.loads((t.toys.root / batch["execution"]["path"]).read_bytes())
        wanted.extend((name, execution["name"]))
    assert t.gets == wanted and len(t.gets) == 18
    assert result["fresh_source_CI_Build_and_18_exact_native_GETs"]
    assert all(result[name] is False for name in (
        "memory_qualified", "native_approval", "processing_authorized", "funding_authorized", "authorizes_retail"))
    assert len([call for call in t.calls if call[0] == "source_image"]) == 1
    assert len([call for call in t.calls if call[0] == "build"]) == 1
    assert len([call for call in t.calls if call[0] == "controller_source"]) == 2
    assert len([call for call in t.calls if call[0] == "runner_source"]) == 2


@pytest.mark.parametrize("change", ["job_uid", "job_generation", "job_template", "execution", "execution_bool",
    "controller_source", "runner_source", "compatibility", "runner_ci", "controller_ci", "build", "source_after_reads"])
def test_development_origin_changed_native_or_source_proof_never_verifies(development_origin_ports, change):
    t = development_origin_ports
    job = next(iter(t.jobs.values()))
    execution = next(iter(t.executions.values()))
    if change == "job_uid":
        job["uid"] = "ffffffff-1234-4321-abcd-123456789012"
    elif change == "job_generation":
        job["generation"] = job["observedGeneration"] = "2"
    elif change == "job_template":
        job["template"]["template"]["containers"][0]["resources"]["limits"]["cpu"] = "4"
    elif change == "execution":
        execution["uid"] = "ffffffff-1234-4321-abcd-123456789012"
    elif change == "execution_bool":
        execution["taskCount"] = True
    elif change == "controller_source":
        t.controller["qualification_sha256"] = "0" * 64
    elif change == "runner_source":
        t.observed["entry_sha256"] = "0" * 64
    elif change == "compatibility":
        t.compatibility = "0" * 64
    elif change == "runner_ci":
        t.ci["headSha"] = "0" * 40
    elif change == "controller_ci":
        t.controller_ci["headSha"] = "0" * 40
    elif change == "build":
        t.build["id"] = "ffffffff-1234-4321-abcd-123456789012"
    else:
        t.after_reads = lambda: t.controller.update(qualification_sha256="0" * 64)
    with pytest.raises(ValueError):
        _verify_owned_development_origin(t)


@pytest.mark.parametrize("change", ["expired_plan", "logging", "raw_lock_hash"])
def test_development_origin_inactive_or_unbound_input_stops_before_source_and_native_ports(
    development_origin_ports, monkeypatch, change,
):
    t = development_origin_ports
    if change == "expired_plan":
        t.plan = t.plan.model_copy(update={"valid_from": datetime.now(UTC) - timedelta(hours=2),
            "expires_at": datetime.now(UTC) - timedelta(hours=1)})
    elif change == "logging":
        monkeypatch.setenv("CLOUDSDK_CORE_LOG_HTTP", "true")
    else:
        t.raw_lock += b" "
    with pytest.raises(ValueError):
        _verify_owned_development_origin(t)
    assert not t.calls and not t.gets
