"""Bounded keyless Job entry for unreviewed development captures.

The external operator still owns native Job/Execution verification and funding.
This entry never creates a Job, permission, permit, provider receipt or model call.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import ConfigDict, Field, model_validator  # noqa: E402

from app.domains.analysis import native_input_acquisition as acquisition
from app.domains.analysis import quality_packet as packet
from scripts import native_acquisition_channel as channel

PROJECT = "ai-resume-parser-482412"
MAX_INPUT_BYTES = 200 * 1024 * 1024
PATH = r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+){0,7}"


def need(value, code):
    if not value:
        raise ValueError("native_job_" + code)


class Input(acquisition.legacy.Closed):
    model_config = ConfigDict(revalidate_instances="always")
    path: str = Field(pattern="^" + PATH + "$", max_length=512)
    object: channel.ObjectRef

    @model_validator(mode="after")
    def safe(self):
        need(all(part not in {".", ".."} for part in self.path.split("/"))
             and self.object.name.startswith("input."), "input_path_or_object")
        return self


class Bundle(acquisition.legacy.Closed):
    model_config = ConfigDict(revalidate_instances="always")
    schema_version: Literal["development-native-job-input-v1"]
    purpose: Literal["unreviewed_development_capture_no_model_no_retail"]
    entry_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    job_name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    configuration: channel.Binding
    authorization: acquisition.Authorization
    dispatch: channel.Dispatch
    previous_dispatch: channel.Dispatch | None
    runner_funding_evidence: acquisition.legacy.Artifact
    native_files: list[Input] = Field(min_length=1, max_length=41)
    authorization_files: list[Input] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def finite(self):
        rows = [*self.native_files, *self.authorization_files]
        need(sum(row.object.size for row in rows) <= MAX_INPUT_BYTES, "input_byte_bound")
        need(all(row.object.size <= (channel.MAX_METADATA if row.path == "materialization-manifest.json"
                                     else acquisition.inspection.MAX_SOURCE_BYTES) for row in self.native_files)
             and all(row.object.size <= channel.MAX_METADATA for row in self.authorization_files),
             "per_input_byte_bound")
        need(len({row.object.name for row in rows}) == len(rows), "duplicate_input_object")
        for group in (self.native_files, self.authorization_files):
            paths = [row.path for row in group]
            need(len(set(paths)) == len(paths)
                 and not any(other.startswith(path + "/") for path in paths for other in paths),
                 "input_path_collision")
        need(self.configuration.namespace.endswith("/quality-development/" + self.authorization.experiment_id + "/"),
             "experiment_changed")
        return self


def _environment(environment):
    forbidden = ("DATABASE", "REDIS", "GEMINI", "OPENAI", "GROQ", "RESEND", "RAZORPAY", "NEON", "UPSTASH",
                 "GCE_METADATA", "GOOGLE_API_USE_")
    redirects = {"GOOGLE_APPLICATION_CREDENTIALS", "STORAGE_EMULATOR_HOST", "GOOGLE_CLOUD_UNIVERSE_DOMAIN",
                 "GOOGLE_CLOUD_QUOTA_PROJECT", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy",
                 "https_proxy", "all_proxy", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE", "SSLKEYLOGFILE"}
    need(not any(key in redirects or key.startswith(forbidden)
                 or key.endswith(("API_KEY", "ACCESS_TOKEN", "REFRESH_TOKEN", "_SECRET", "_PASSWORD"))
                 for key in environment), "unexpected_credential_environment")
    name, execution = environment.get("CLOUD_RUN_JOB"), environment.get("CLOUD_RUN_EXECUTION", "")
    need(type(name) is str and re.fullmatch(r"[a-z][a-z0-9-]{0,62}", name)
         and re.fullmatch(re.escape(name) + r"-[a-z0-9-]{1,40}", execution)
         and environment.get("CLOUD_RUN_TASK_INDEX") == "0"
         and environment.get("CLOUD_RUN_TASK_ATTEMPT") == "0"
         and environment.get("CLOUD_RUN_TASK_COUNT") == "1", "single_task_first_attempt_required")
    need(all(environment[key] == PROJECT for key in ("GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT") if key in environment),
         "project_changed")
    return {"job": name, "execution": execution, "task_index": 0, "task_attempt": 0,
            "custody": "environment_observation_not_provider_attestation"}


def context(bundle, environment):
    """Environment observations are explicitly not native execution attestation."""
    observed = _environment(environment)
    need(observed["job"] == bundle.job_name, "job_name_changed")
    return observed


def _active(bundle):
    auth = bundle.authorization
    need(auth.state == "authorized" and auth.valid_from <= datetime.now(UTC) < auth.expires_at,
         "active_original_authorization_required")


def _populate(store, root, rows):
    root.mkdir(mode=0o700)
    acquisition._private(root, directory=True)
    for row in rows:
        value = store.fetch(row.object.name, generation=row.object.generation, maximum=row.object.size)
        need(value is not None and value.reference == row.object, "input_generation_or_hash_changed")
        channel._checked(value, name=row.object.name, maximum=row.object.size)
        target = root / row.path
        for parent in reversed(target.parents):
            if parent == root or root in parent.parents:
                parent.mkdir(mode=0o700, exist_ok=True)
                acquisition._private(parent, directory=True)
        acquisition._new(target.parent, target.name, value.raw)


def run(bundle, store, packet_root, workspace, *, environment, transport=None):
    """One externally authorized batch. No restore permits a resend of an intent."""
    bundle = Bundle.model_validate(bundle)
    observed = context(bundle, environment)
    need(bundle.entry_sha256 == packet.file_digest(Path(__file__))
         and store.namespace == bundle.configuration.namespace, "entry_or_namespace_changed")
    _active(bundle)
    need(workspace.is_absolute() and str(workspace.absolute()) == str(workspace.resolve())
         and bundle.authorization.journal_path == str(workspace / "journal"), "exact_workspace_required")
    workspace.mkdir(mode=0o700, parents=False, exist_ok=False)
    acquisition._private(workspace, directory=True)
    native, authority, journal = workspace / "native", workspace / "authority", workspace / "journal"
    _populate(store, native, bundle.native_files)
    _populate(store, authority, bundle.authorization_files)
    common = (packet_root, native, bundle.authorization, authority)
    report = acquisition.plan(*common)
    need({row.path for row in bundle.native_files} == {"materialization-manifest.json"}
         | {row["source"]["path"] for row in report["selected_cases"]}, "exact_development_sources_required")
    reviews = [bundle.authorization.funding_evidence, bundle.runner_funding_evidence,
               bundle.dispatch.external_authority_review]
    if bundle.previous_dispatch is not None:
        reviews.append(bundle.previous_dispatch.external_authority_review)
    need({row.path for row in bundle.authorization_files} == {row.path for row in reviews},
         "exact_funding_and_review_inputs_required")
    for artifact in reviews:
        acquisition.legacy._artifact(authority, artifact)
    adapter = channel.Channel(store, bundle.configuration, bundle.dispatch,
        authorization_root=authority, authorization=bundle.authorization)
    adapter._validate_configuration(report)
    if bundle.previous_dispatch is not None:
        previous = channel.Channel(store, bundle.configuration, bundle.previous_dispatch,
            authorization_root=authority, authorization=bundle.authorization)
        need(channel.dispatch_bytes(bundle.previous_dispatch) != channel.dispatch_bytes(bundle.dispatch),
             "restored_dispatch_requires_new_authority")
        channel.restore(common, journal, previous)
    _active(bundle)
    # Create-only context seals the same externally reviewed dispatch; it cannot
    # certify a Job UID or become an application/result receipt.
    context_name = "job." + packet.digest(channel.dispatch_bytes(bundle.dispatch)) + ".context.json"
    store.create(context_name, packet.canonical(observed))
    result = channel.execute(common, journal, adapter, initialize=bundle.previous_dispatch is None,
        maximum_requests=bundle.dispatch.maximum_requests, transport=transport)
    return {"state": "completed_unreviewed", "terminal": result["terminal"],
            "observed_context": observed, "native_approval": "pending",
            "actual_native_cost_minor": None, "authorizes_retail": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--bundle-name", required=True)
    parser.add_argument("--bundle-generation", required=True)
    parser.add_argument("--bundle-sha256", required=True)
    parser.add_argument("--workspace", type=Path, default=Path("/tmp/hirewiz-native-acquisition"))
    args = parser.parse_args()
    try:
        need(re.fullmatch(r"input\.[A-Za-z0-9._-]{1,120}\.json", args.bundle_name)
             and re.fullmatch(r"[1-9][0-9]{0,20}", args.bundle_generation)
             and re.fullmatch(r"[a-f0-9]{64}", args.bundle_sha256), "bundle_reference_required")
        # ADC is keyless metadata identity in the reviewed private Job; no new
        # login, token file, owner session or credential fallback is created.
        _environment(dict(os.environ))
        from google.auth.compute_engine import Credentials
        from google.cloud import storage
        store = channel.GcsObjects(storage.Client(project=PROJECT, credentials=Credentials()), args.namespace)
        value = store.fetch(args.bundle_name, generation=args.bundle_generation, maximum=channel.MAX_METADATA)
        need(value is not None and value.reference.generation == args.bundle_generation
             and value.reference.sha256 == args.bundle_sha256, "bundle_changed")
        bundle = Bundle.model_validate_json(value.raw)
        result = run(bundle, store, Path(__file__).resolve().parents[1] / "evals/unified_credit_v2",
                     args.workspace, environment=dict(os.environ))
        print(json.dumps(result))
        return 0
    except (Exception, KeyboardInterrupt):
        print(json.dumps({"state": "refused_or_unknown_no_replay", "native_approval": "pending",
                          "authorizes_retail": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
