"""One externally reviewed, keyless held-out capture task; not a dispatcher.

No Job/IAM/create/run API, permit issuance, model/SQL access or native source
qualification occurs here. The external controller must qualify and authorize
the exact image, bundle, namespace, identity, native workers and one execution.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

if __package__ in {None, ""}:
    # The fixed direct-file command uses -I; only this image's backend tree is
    # added. Neither an owner checkout nor an arbitrary PYTHONPATH is loaded.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pydantic import Field, model_validator  # noqa: E402

from evaluation.native_acquisition_v2 import channel as c  # noqa: E402
from evaluation.native_acquisition_v2 import engine as e  # noqa: E402

PROJECT = "ai-resume-parser-482412"
VERSION = "held-out-native-job-input-v2"
WORKSPACE = Path("/app/backend/.quality-held-out")
NO_BUCKET_METADATA = "DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA"


def need(value: object, code: str) -> None:
    if not value:
        raise e.AcquisitionDenied("held_out_job_" + code)


class Input(e.Closed):
    path: str = Field(pattern="^" + e.PATH + "$", max_length=512)
    object: c.ObjectRef

    @model_validator(mode="after")
    def original(self) -> Input:
        need(self.object.name.startswith("input."), "input_object_name")
        return self


class AuthorityInput(Input):
    kind: str = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def finite_kind(self) -> AuthorityInput:
        need(self.kind in e.KINDS and self.object.size <= e.KINDS[self.kind][1], "authority_kind_or_size")
        return self


class Bundle(e.Closed):
    schema_version: Literal["held-out-native-job-input-v2"]
    purpose: Literal["unreviewed_held_out_capture_no_model_no_retail"]
    entry_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    job_name: str = Field(pattern=r"^hirewiz-quality-held-out-[a-z0-9-]{8,32}$")
    configuration: c.Binding
    authorization: e.Authorization
    dispatch: c.Dispatch
    runner_funding_evidence: e.Artifact
    native_files: list[Input] = Field(min_length=161, max_length=161)
    authority_files: list[AuthorityInput] = Field(min_length=1, max_length=e.MAX_AUTHORITY_FILES)

    @model_validator(mode="after")
    def finite(self) -> Bundle:
        rows = [*self.native_files, *self.authority_files]
        need(sum(row.object.size for row in rows) + e.MAX_BUNDLE_BYTES <= e.MAX_INPUT_BYTES, "combined_input_bound")
        need(len({row.object.name for row in rows}) == len(rows), "duplicate_input_object")
        for group in (self.native_files, self.authority_files):
            paths = [row.path for row in group]
            need(len(paths) == len(set(paths)) and not any(
                b.startswith(a + "/") for a in paths for b in paths), "input_path_collision")
        need(sum(row.path == "materialization-manifest.json" for row in self.native_files) == 1
             and all(row.object.size <= (e.MAX_METADATA if row.path == "materialization-manifest.json"
                                         else e.inspection.MAX_SOURCE_BYTES) for row in self.native_files), "native_input_bound")
        for kind, (count, _) in e.KINDS.items():
            need(sum(row.kind == kind for row in self.authority_files) <= count, "authority_kind_count")
        need(self.configuration.namespace.endswith("/quality-held-out/" + self.authorization.experiment_id + "/")
             and self.dispatch.maximum_requests == c.batch_limit(self.dispatch.batch_index), "experiment_or_batch")
        return self


def environment_context(environment: dict[str, str]) -> c.JobContext:
    """Observed platform environment is not native Job/identity attestation."""
    prefixes = ("DATABASE", "REDIS", "GEMINI", "OPENAI", "GROQ", "RESEND", "RAZORPAY", "NEON", "UPSTASH",
                "GCE_METADATA", "GOOGLE_API_USE_", "DYLD_", "GIT_", "OTEL_")
    forbidden = {"GOOGLE_APPLICATION_CREDENTIALS", "STORAGE_EMULATOR_HOST", "GOOGLE_CLOUD_UNIVERSE_DOMAIN",
        "GOOGLE_CLOUD_QUOTA_PROJECT", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy",
        "all_proxy", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE", "SSLKEYLOGFILE", "LD_PRELOAD",
        "LD_LIBRARY_PATH", "PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "PYTHONSTARTUP", "PYTHONINSPECT",
        "API_ENDPOINT_OVERRIDE", "ENABLE_GCS_PYTHON_CLIENT_OTEL_TRACES"}
    need(not any(key in forbidden or key.startswith(prefixes)
                 or key.endswith(("API_KEY", "ACCESS_TOKEN", "REFRESH_TOKEN", "_SECRET", "_PASSWORD"))
                 for key in environment), "unexpected_credential_or_redirect_environment")
    need(all(environment.get(key, PROJECT) == PROJECT for key in ("GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT")), "project")
    need(environment.get(NO_BUCKET_METADATA) == "true", "background_metadata_must_be_disabled")
    need(environment.get("CLOUD_RUN_TASK_INDEX") == "0" and environment.get("CLOUD_RUN_TASK_ATTEMPT") == "0"
         and environment.get("CLOUD_RUN_TASK_COUNT") == "1", "single_task_first_attempt")
    return c.JobContext(schema_version="held-out-native-job-context-v2", job=environment.get("CLOUD_RUN_JOB", ""),
        execution=environment.get("CLOUD_RUN_EXECUTION", ""), task_index=0, task_attempt=0, task_count=1,
        custody="environment_observation_not_provider_attestation")


def _populate(store: c.Objects, root: Path, rows: list[Any], *, initialize: bool = True) -> None:
    if initialize:
        root.mkdir(mode=0o700, parents=False, exist_ok=False)
    e.development._private(root, directory=True)
    for row in rows:
        value = store.fetch(row.object.name, generation=row.object.generation, maximum=row.object.size)
        need(value is not None and value.reference == row.object, "input_generation_or_hash_changed")
        assert value is not None
        c._checked(value, row.object.name, row.object.size)
        target = root / row.path
        for parent in reversed(target.parents):
            if parent == root or root in parent.parents:
                parent.mkdir(mode=0o700, exist_ok=True)
                e.development._private(parent, directory=True)
        e.development._new(target.parent, target.name, value.raw)


def _native_membership(bundle: Bundle, packet_root: Path, native: Path) -> list[Input]:
    """Select only after the review gate; validate references before source fetch."""
    _, cases = e.inputs.selected_cases(packet_root, "held_out")
    need(len(cases) == 160 and e.Counter(case["source_format"] for case in cases)
         == dict.fromkeys(("pdf", "docx", "tex", "texzip"), 40), "complete_held_out_population")
    manifest = e._json(e.read_artifact(native, e.Artifact(path="materialization-manifest.json",
                                                       sha256=bundle.authorization.native_manifest_sha256)))
    need(manifest.get("packet_manifest_sha256") == e.inputs.MANIFEST_SHA
         and manifest.get("corpus_sha256") == e.inputs.CORPUS_SHA, "materialization_corpus")
    rows = manifest.get("cases", [])
    need(type(rows) is list and len(rows) == 160, "exact_materialized_cases")
    indexed = {row["case_id"]: row for row in rows}
    need(len(indexed) == 160 and set(indexed) == {case["case_id"] for case in cases}, "exact_materialized_cases")
    sources: dict[str, str] = {}
    for case in cases:
        row = indexed[case["case_id"]]
        need(row.get("input_sha256") == e.inputs.digest(case), "materialized_case_changed")
        source = e.Artifact.model_validate(row["input_artifact"])
        need(source.path not in sources, "duplicate_materialized_source")
        sources[source.path] = source.sha256
    refs = [row for row in bundle.native_files if row.path != "materialization-manifest.json"]
    need({row.path: row.object.sha256 for row in refs} == sources, "exact_held_out_sources")
    return refs


def _exact_authority(bundle: Bundle, root: Path) -> None:
    auth = bundle.authorization
    lock = e.NativeDevelopmentLock.model_validate(e._json(e.read_artifact(root, auth.native_development_lock)))
    expected = {row.path: (row.sha256, row.kind) for row in lock.files}
    controls = [auth.native_development_lock, auth.funding_evidence, bundle.runner_funding_evidence,
                bundle.dispatch.external_authority_review]
    for artifact in controls:
        value = artifact.sha256, "control"
        need(artifact.path not in expected or expected[artifact.path] == value, "authority_reference_collision")
        expected[artifact.path] = value
        e.read_artifact(root, artifact)
    actual = {row.path: (row.object.sha256, row.kind) for row in bundle.authority_files}
    need(actual == expected, "exact_review_and_funding_closure")


def run(bundle: Bundle, store: c.Objects, packet_root: Path, workspace: Path, *,
        environment: dict[str, str], transport: e.NativeTransport | None = None) -> dict[str, Any]:
    """One task on an externally qualified image; no resource dispatch or retry."""
    observed = environment_context(environment)
    bundle = Bundle.model_validate(bundle)
    need(observed.job == bundle.job_name and bundle.entry_sha256 == e.packet.file_digest(Path(__file__))
         and store.namespace == bundle.configuration.namespace, "entry_namespace_or_job")
    e._active(bundle.authorization)
    need(workspace.is_absolute() and workspace.resolve() == workspace
         and bundle.authorization.journal_path == str(workspace / "journal"), "exact_workspace")
    workspace.mkdir(mode=0o700, parents=False, exist_ok=False)
    e.development._private(workspace, directory=True)
    authority, native, journal = workspace / "authority", workspace / "native", workspace / "journal"
    # Native refs and their byte limits were checked above, but NO held-out
    # original bytes are fetched until this complete original40 lock matches.
    _populate(store, authority, bundle.authority_files)
    _exact_authority(bundle, authority)
    e.verify_native_development_lock(packet_root, bundle.authorization, authority)
    e._active(bundle.authorization)
    _populate(store, native, [row for row in bundle.native_files if row.path == "materialization-manifest.json"])
    sources = _native_membership(bundle, packet_root, native)
    e._active(bundle.authorization)
    _populate(store, native, sources, initialize=False)
    report = e.plan(packet_root, native, bundle.authorization, authority)
    need({row.path for row in bundle.native_files} == {"materialization-manifest.json",
        *(row["source"]["path"] for row in report["selected_cases"])}, "exact_held_out_sources")
    channel = c.Channel(store, bundle.configuration, bundle.dispatch,
                        authorization_root=authority, authorization=bundle.authorization)
    channel._prepare(report)
    if bundle.dispatch.batch_index:
        c.restore((packet_root, native, bundle.authorization, authority), journal, channel)
    e._active(bundle.authorization)
    result = e.execute(packet_root, native, bundle.authorization, authority, journal,
        initialize=bundle.dispatch.batch_index == 0, maximum_requests=bundle.dispatch.maximum_requests,
        channel=channel, context=observed, transport=transport)
    return {"state": "completed_unreviewed", "terminal": result["terminal"], "requests_sent": result["requests_sent"],
        "observed_context": observed.model_dump(mode="json"), "native_approval": False,
        "processing_authorized": False, "actual_native_cost_minor": None,
        "reserved_expense_released": False, "authorizes_retail": False}


@contextmanager
def keyless_store(namespace: str):
    """Explicit metadata credentials; storage HTTP never refreshes/retries a POST."""
    from google.auth.compute_engine import Credentials
    from google.auth.transport.requests import AuthorizedSession, Request
    from google.cloud import storage
    from requests import Session
    from requests.adapters import HTTPAdapter

    need(re.fullmatch(r"gs://[a-z0-9][a-z0-9._-]{2,221}/quality-held-out/[a-z0-9][a-z0-9-]{7,79}/", namespace), "namespace")
    # The locked storage SDK otherwise starts asynchronous bucket-metadata
    # requests with independent retries. Use its opt-out, not an SDK patch.
    need(os.environ.get(NO_BUCKET_METADATA) == "true", "background_metadata_must_be_disabled")

    def configure(http: Any) -> None:
        http.trust_env, http.max_redirects = False, 0
        http.mount("https://", HTTPAdapter(max_retries=0))
        http.mount("http://", HTTPAdapter(max_retries=0))

    class BoundedRequest(Request):
        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            kwargs["timeout"] = 5
            return super().__call__(*args, **kwargs)

    auth_http = Session()
    configure(auth_http)
    http = None
    client = None
    try:
        # Avoid the credential property's implicit, separately configured
        # metadata Request during the client's universe compatibility check.
        credentials = Credentials(universe_domain="googleapis.com")
        http = AuthorizedSession(credentials, max_refresh_attempts=0, auth_request=BoundedRequest(session=auth_http))
        configure(http)
        client = storage.Client(project=PROJECT, credentials=credentials, _http=http,
            client_options={"api_endpoint": "https://storage.googleapis.com", "universe_domain": "googleapis.com"})
        yield c.GcsObjects(client, namespace)
    finally:
        try:
            if client is not None:
                client.close()
        finally:
            try:
                if http is not None:
                    http.close()
            finally:
                auth_http.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--bundle-name", required=True)
    parser.add_argument("--bundle-generation", required=True)
    parser.add_argument("--bundle-sha256", required=True)
    parser.add_argument("--workspace", type=Path, default=WORKSPACE)
    args = parser.parse_args(argv)
    try:
        environment = dict(os.environ)
        environment_context(environment)
        need(re.fullmatch(r"input\.[A-Za-z0-9._-]{1,120}\.json", args.bundle_name)
             and re.fullmatch(r"[1-9][0-9]{0,20}", args.bundle_generation)
             and re.fullmatch(r"[a-f0-9]{64}", args.bundle_sha256), "bundle_reference")
        with keyless_store(args.namespace) as store:
            value = store.fetch(args.bundle_name, generation=args.bundle_generation, maximum=e.MAX_BUNDLE_BYTES)
            need(value is not None and value.reference.generation == args.bundle_generation
                 and value.reference.sha256 == args.bundle_sha256 and len(value.raw) <= e.MAX_BUNDLE_BYTES, "bundle_changed")
            assert value is not None
            c._checked(value, args.bundle_name, e.MAX_BUNDLE_BYTES)
            bundle = Bundle.model_validate(e._json(value.raw))
            need(args.bundle_name not in {row.object.name for row in [*bundle.native_files, *bundle.authority_files]}, "recursive_bundle_input")
            result = run(bundle, store, Path(__file__).resolve().parents[2] / "evals/unified_credit_v2", args.workspace,
                         environment=environment)
        raw = e.packet.canonical(result)
        need(len(raw) <= 16 * 1024, "result_bound")
        print(raw.decode(), end="")
        return 0
    except (Exception, KeyboardInterrupt):
        print(json.dumps({"state": "refused_or_unknown_no_replay", "native_approval": False,
            "processing_authorized": False, "actual_native_cost_minor": None,
            "reserved_expense_released": False, "authorizes_retail": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
