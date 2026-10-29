"""Finite V4 receipt: unchanged V3 safety requirements plus real parser cases."""
from __future__ import annotations

import copy

from infra.gcp import document_candidate_receipt as base
from infra.gcp import document_source_payload as payload

NAMES = (*base.NAMES, *payload.EXTRA_CASES)


def validate(value, image, policy, runtime):
    base.require(type(value) is dict and set(value) <= base.TOP
                 and value.get("event") == payload.CORPUS_EVENT and type(value.get("version")) is int
                 and value["version"] == 4 and value.get("all_required_passed") is True
                 and value.get("failure_stage") is None)
    rows = value.get("cases")
    base.require(type(rows) is list and len(rows) == len(NAMES)
                 and tuple(row.get("case") for row in rows) == NAMES)
    original = copy.deepcopy(value)
    original.update(event="hirewiz_document_candidate_required_v3", version=3, cases=rows[:len(base.NAMES)])
    base.require(base.validate(original, image, policy, runtime) is True)
    for row in rows[len(base.NAMES):]:
        base.require(type(row) is dict and set(row) <= base.ROW | {"reading_order_pass"}
                     and row.get("passed") is True and row.get("attempted") is True
                     and row.get("binding_match") is True and row.get("cleanup_passed") is True
                     and base.diagnostic(row["diagnostic"]))
        binding = row.get("binding")
        base.require(type(binding) is dict and set(binding) == {"version", "sha256", "size_bytes", "source_format"}
                     and type(binding.get("version")) is int and binding["version"] == 1
                     and base.hashed(binding.get("sha256")) and binding.get("source_format") == "pdf"
                     and base.integer(binding.get("size_bytes"), 1, 5 * 1024**2))
        diagnostic = row["diagnostic"]
        if row["case"].endswith("_refused"):
            base.require(row.get("worker_refused") is True and diagnostic["processor"] == "refused"
                         and diagnostic["launcher_code"] == 1 and diagnostic["validation"] == "complete"
                         and "scan" not in row and "reading_order_pass" not in row)
        else:
            base.require(row.get("reading_order_pass") is True and diagnostic["processor"] == "clean"
                         and diagnostic["launcher_code"] == 0 and diagnostic["validation"] == "complete")
            base.scan(row.get("scan"))
    return True
