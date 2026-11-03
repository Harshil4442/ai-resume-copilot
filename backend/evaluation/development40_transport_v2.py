"""Explicit GCS transport for unchanged, unreviewed development40 semantics.

This entry issues no permit, Job, provider attestation or model request. The
external versioned controller must qualify this entry AND the delegated V1
entry in the actual immutable image before a funded execution.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if (Path(__file__).resolve().parents[1] / "scripts/__init__.py").exists():
    raise ValueError("development_transport_undeclared_scripts_initializer")

from app.domains.analysis import quality_packet as packet  # noqa: E402
from scripts import native_acquisition_channel as channel  # noqa: E402
from scripts import native_acquisition_job as semantic  # noqa: E402

VERSION = "development40-gcs-transport-v2"
SEMANTIC_ENTRY = "backend/scripts/native_acquisition_job.py"
SEMANTIC_SHA256 = "8eef532ef4d4978e9dda000e47ee694b43b55e16a168f93f9c7da724ea9b8740"
CHANNEL_SHA256 = "89b65c3c3fb874368f28702f0048c19a3bbeccc7ab910dd12bc35a593225864e"
NO_BUCKET_METADATA = "DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA"
WORKSPACE = Path("/app/backend/.quality-development")
METADATA_CALLS = 2
METADATA_BUDGET_SECONDS = 10.0
METADATA_CALL_TIMEOUT = 5.0


def need(value: object, code: str) -> None:
    if not value:
        raise ValueError("development_transport_" + code)


def _pairs(rows):
    need(len({key for key, _ in rows}) == len(rows), "duplicate_bundle_key")
    return dict(rows)


def environment_context(environment: dict[str, str]) -> dict[str, Any]:
    """Platform observations are not native Job/Execution attestation."""
    observed = semantic._environment(environment)
    forbidden = {"API_ENDPOINT_OVERRIDE", "ENABLE_GCS_PYTHON_CLIENT_OTEL_TRACES",
        "PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "PYTHONSTARTUP", "PYTHONINSPECT",
        "LD_PRELOAD", "LD_LIBRARY_PATH"}
    need(not any(key in forbidden or key.startswith(("DYLD_", "OTEL_", "GIT_"))
                 for key in environment), "redirect_or_import_environment")
    need(environment.get(NO_BUCKET_METADATA) == "true", "bucket_metadata_opt_out_required")
    return observed


def semantic_sources() -> None:
    backend = Path(__file__).resolve().parents[1]
    for module, name, expected in (
        (semantic, "native_acquisition_job.py", SEMANTIC_SHA256),
        (channel, "native_acquisition_channel.py", CHANNEL_SHA256),
    ):
        path = Path(module.__file__)
        need(path == backend / "scripts" / name and path.resolve() == path
             and not path.is_symlink() and packet.file_digest(path) == expected,
             "semantic_source_changed")


@contextmanager
def keyless_store(namespace: str):
    """One metadata credential acquisition; no object POST replay or redirect.

    The budget limits sends and checks elapsed time before/after requests; a
    requests timeout is an inactivity limit, not a hard refresh wall deadline.
    The native Job timeout remains the external elapsed bound.
    """
    need(re.fullmatch(r"gs://[a-z0-9][a-z0-9._-]{2,221}/quality-development/[a-z0-9][a-z0-9-]{7,79}/", namespace),
         "development_namespace_required")
    need(os.environ.get(NO_BUCKET_METADATA) == "true", "bucket_metadata_opt_out_required")
    from google.auth.compute_engine import Credentials
    from google.auth.transport.requests import AuthorizedSession, Request
    from google.cloud import storage
    from requests import Session
    from requests.adapters import HTTPAdapter

    def configure(http: Any) -> None:
        http.trust_env, http.max_redirects = False, 0
        for scheme in ("http://", "https://"):
            http.mount(scheme, HTTPAdapter(max_retries=0))

    class MetadataRequest(Request):
        def __init__(self, session):
            super().__init__(session=session)
            self.calls, self.started = 0, None

        def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **kwargs):
            target = urlsplit(url)
            query = parse_qs(target.query, strict_parsing=True)
            base = "/computeMetadata/v1/instance/service-accounts/default/"
            need(method == "GET" and body is None and not kwargs
                 and target.scheme == "http" and target.hostname == "metadata.google.internal"
                 and target.netloc == "metadata.google.internal" and not target.fragment
                 and ((target.path == base and query == {"recursive": ["true"]})
                      or (target.path == base + "token" and set(query) <= {"scopes"}
                          and all(len(values) == 1 for values in query.values()))), "metadata_route")
            now = time.monotonic()
            if self.started is None:
                self.started = now
            remaining = METADATA_BUDGET_SECONDS - (now - self.started)
            need(self.calls < METADATA_CALLS and remaining > 0, "metadata_budget_exhausted")
            self.calls += 1  # Reserve before a physical send, including unknown outcomes.
            try:
                response = super().__call__(url, method=method, body=None, headers=headers,
                    timeout=min(METADATA_CALL_TIMEOUT, remaining), allow_redirects=False)
            except Exception:
                # Convert TransportError before compute metadata's retry/backoff
                # loop sees it; failed/unknown metadata reads are not replayed.
                raise ValueError("development_transport_metadata_unavailable") from None
            need(time.monotonic() - self.started < METADATA_BUDGET_SECONDS,
                 "metadata_budget_exhausted")
            need(response.status == 200 and response.headers.get("Metadata-Flavor") == "Google",
                 "metadata_unavailable")
            return response

    auth_http = Session()
    http = client = None
    try:
        configure(auth_http)
        credentials = Credentials(universe_domain="googleapis.com")
        http = AuthorizedSession(credentials, max_refresh_attempts=0,
            auth_request=MetadataRequest(auth_http))
        configure(http)
        client = storage.Client(project=semantic.PROJECT, credentials=credentials, _http=http,
            client_options={"api_endpoint": "https://storage.googleapis.com", "universe_domain": "googleapis.com"})
        yield channel.GcsObjects(client, namespace)
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
    for name in ("namespace", "bundle-name", "bundle-generation", "bundle-sha256"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--workspace", type=Path, default=WORKSPACE)
    args = parser.parse_args(argv)
    try:
        environment = dict(os.environ)
        environment_context(environment)
        semantic_sources()
        need(re.fullmatch(r"input\.[A-Za-z0-9._-]{1,120}\.json", args.bundle_name)
             and re.fullmatch(r"[1-9][0-9]{0,20}", args.bundle_generation)
             and re.fullmatch(r"[a-f0-9]{64}", args.bundle_sha256), "bundle_reference_required")
        with keyless_store(args.namespace) as store:
            value = store.fetch(args.bundle_name, generation=args.bundle_generation, maximum=channel.MAX_METADATA)
            need(value is not None and value.reference.generation == args.bundle_generation
                 and value.reference.sha256 == args.bundle_sha256, "bundle_changed")
            bundle = semantic.Bundle.model_validate(json.loads(value.raw, object_pairs_hook=_pairs))
            result = semantic.run(bundle, store, Path(__file__).resolve().parents[1] / "evals/unified_credit_v2",
                args.workspace, environment=environment)
        print(json.dumps(result))
        return 0
    except (Exception, KeyboardInterrupt):
        print(json.dumps({"state": "refused_or_unknown_no_replay", "transport_profile": VERSION,
            "native_approval": "pending", "authorizes_retail": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
