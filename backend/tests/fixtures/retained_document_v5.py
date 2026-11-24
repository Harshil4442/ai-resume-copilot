"""Exact code-only V5 source for historical protocol unit fixtures."""
from __future__ import annotations

import hashlib

from fixtures.retained_native_source import ROOT, local_git
from infra.gcp import document_source_candidate_v5 as v5

RETAINED_COMMIT = "1f4d0a523e99e95219aba20ec172b8d41356e97b"


def snapshot():
    raw = local_git(ROOT, "show", RETAINED_COMMIT + ":" + v5.POLICY_PATH)
    hashes = v5.validate_policy(raw, ROOT)
    contents = {name: local_git(ROOT, "show", RETAINED_COMMIT + ":" + name) for name in hashes}
    assert all(hashlib.sha256(contents[name]).hexdigest() == stamp for name, stamp in hashes.items())
    contents[v5.POLICY_PATH] = raw
    return contents, hashes, v5.POLICY_SHA256
