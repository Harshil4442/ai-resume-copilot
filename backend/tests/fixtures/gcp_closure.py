"""Synthetic Storage transport for actual closed-aware witness/native coordinators.

This supplies neither fake reader behavior nor production IAM/UID/retention evidence.
"""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

from test_gcp_journal_sdk import response

from app.domains.recovery.contracts import canonical
from app.domains.recovery.gcp_closure_contracts import AuthorityCloseIntent, ClosedInventoryManifest
from app.domains.recovery.gcp_contracts import WitnessBody, WitnessPin
from app.domains.recovery.gcp_journal import GcsWitnessFence
from app.domains.recovery.gcp_password_closure import (
    GcpPasswordCloseCoordinator,
    GcsAuthorityClosure,
)
from app.domains.recovery.gcp_password_lifetime import GcpPasswordLifetimeCoordinator
from app.domains.recovery.gcp_publication_contracts import PublicationGate


def install_witness(c):
    original = c.http.request
    lists = []
    witness_path = "witness/current"
    body = WitnessBody(registry=c.pin, state="OPEN", manifest_generation="123",
        manifest_sha256="d" * 64, partitions_sha256="e" * 64)
    raw = canonical(body.model_dump(mode="json")).encode()
    c.http.objects[witness_path] = (raw, "201")
    c.http.allowed[witness_path] = raw
    class Identity:
        def identity(self):
            assert not c.active, "Fresh identity IO entered a native transaction"
            return {"name": c.pin.database, "uid": str(c.pin.database_uid), "type": "FIRESTORE_NATIVE"}
    def request(method, url, **kwargs):
        assert not c.active, "Storage IO entered a native transaction"
        query = parse_qs(urlsplit(url).query)
        if method == "GET" and urlsplit(url).path.endswith("/o"):
            assert kwargs["timeout"] == 2.0 and 1 <= int(query["maxResults"][0]) <= 16
            prefix = query["prefix"][0]
            token = int(query.get("pageToken", ["0"])[0])
            items = [{"name": path, "bucket": c.journal.bucket.name, "generation": generation,
                "size": str(len(data))} for path, (data, generation) in sorted(c.http.objects.items())
                if path.startswith(prefix)]
            end = token + int(query["maxResults"][0])
            result = {"items": items[token:end]}
            if end < len(items):
                result["nextPageToken"] = str(end)
            lists.append((prefix, token, len(items)))
            return response(json.dumps(result).encode())
        if method == "GET":
            path = unquote(urlsplit(url).path.split("/o/", 1)[1])
            if path not in c.http.objects:
                return response(b'{"error":{"code":404,"message":"synthetic absent object"}}', status=404)
        if method == "POST":
            metadata = kwargs["data"].split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0]
            path = json.loads(metadata)["name"]
            if path.startswith(("authority-closures/", "authority-closed-cuts/", "authority-publication-seals/")):
                data = kwargs["data"].split(b"\r\n\r\n")[2].rsplit(b"\r\n--", 1)[0]
                schema = (AuthorityCloseIntent if path.startswith("authority-closures/") else PublicationGate
                          if path.startswith("authority-publication-seals/") else ClosedInventoryManifest)
                typed = schema.model_validate_json(data)
                assert canonical(typed.model_dump(mode="json")).encode() == data
                c.http.allowed[path] = data
        return original(method, url, **kwargs)
    c.journal.bucket.client._http.request.side_effect = request
    pin = WitnessPin(registry=c.pin, bucket=c.journal.bucket.name, path=witness_path,
        generation="201", sha256=hashlib.sha256(raw).hexdigest())
    witness = GcsWitnessFence(c.journal.bucket, Identity(), pin, body)
    c.fence = c.coordinator.fence = witness
    c.closure = GcsAuthorityClosure(c.journal.bucket)
    c.close_service = GcpPasswordCloseCoordinator(c.registry, c.pin, epoch_generation=1,
        witness=witness, closure=c.closure, publication=c.publication, now_ms=lambda: c.now)
    c.witness_transport = SimpleNamespace(request=request, lists=lists, path=witness_path)
    return witness


def closed_writer(c, command):
    install_witness(c)
    result = c.close_service.close(command)
    assert result.status == "COMMITTED" and result.denial_fence is not None
    c.closed = result.denial_fence
    c.lifetime = GcpPasswordLifetimeCoordinator(c.registry, c.pin, epoch_generation=1,
        journal=c.lifetime_journal, closed_denial=c.closed, now_ms=lambda: c.now)
    return c.lifetime.allocate(command)
