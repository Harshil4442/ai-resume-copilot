"""Read V5 native evidence with distinct launcher and processor outcomes."""
from __future__ import annotations

from infra.gcp import document_candidate_probe as candidate
from infra.gcp import document_candidate_receipt as base
from infra.gcp import document_source_payload as v4_payload
from infra.gcp import document_source_receipt as v4

VERSION = 5
INVENTORY_EVENT = "hirewiz_document_source_inventory_v5"
CORPUS_EVENT = "hirewiz_document_source_required_v5"
NAMES = v4.NAMES
_DIAGNOSTIC_KEYS = base.DIAG | {"processor_code"}
_REFUSALS = frozenset({
    "eicar_exact_file",
    "eicar_tex_source",
    "eicar_texzip_member",
    "rendered_reflow_refusal",
    "unsupported_source_font_refusal",
    "pdf_spanning_columns_refused",
    "pdf_unaligned_gutter_refused",
})


def _project_diagnostic(value: dict, *, code: int | None) -> dict:
    base.require(type(value) is dict and set(value) == _DIAGNOSTIC_KEYS)
    base.require(value["launcher"] == "returned"
                 and base.integer(value["launcher_code"], 0, 0)
                 and base.integer(value["launcher_elapsed_ms"], 0, 120000)
                 and value["cleanup"] == "provider_teardown"
                 and value["cleanup_code"] is None and value["cleanup_elapsed_ms"] is None)
    if code is None:
        base.require(value["processor_code"] is None and value["processor"] == "not_validated"
                     and value["validation"] == "not_started" and value["failure_phase"] is None)
    else:
        base.require(base.integer(value["processor_code"], code, code)
                     and value["processor"] == ("refused" if code == 1 else "clean")
                     and value["validation"] == "complete"
                     and value["failure_phase"] == ("processor" if code == 1 else None))
    # Adapt the validated transport fields privately for the frozen semantic
    # checker. Only the original V5 record is eligible for native custody.
    projected = {key: value[key] for key in base.DIAG}
    projected.update(launcher_code=code if code is not None else 0, cleanup="returned", cleanup_code=0)
    return projected


def _header(value: dict, *, event: str) -> dict:
    base.require(type(value) is dict and value.get("event") == event
                 and base.integer(value.get("version"), VERSION, VERSION))
    identity = value.get("identity_preflight")
    base.require(type(identity) is dict and identity.get("cleanup_passed") is True)
    return {**value, "identity_preflight": {
        **identity, "diagnostic": _project_diagnostic(identity.get("diagnostic"), code=None),
    }}


def validate_inventory(value: dict, image: str, runtime: dict[str, str], *, policy: str) -> dict[str, str]:
    projected = _header(value, event=INVENTORY_EVENT)
    projected.update(event=v4_payload.INVENTORY_EVENT, version=1)
    return candidate.validate_inventory(projected, image, runtime, policy=policy, event=v4_payload.INVENTORY_EVENT)


def validate(value: dict, image: str, policy: str, runtime: dict[str, str]) -> bool:
    projected = _header(value, event=CORPUS_EVENT)
    rows = value.get("cases")
    base.require(type(rows) is list and len(rows) == len(NAMES))
    cases = []
    for row, name in zip(rows, NAMES, strict=True):
        base.require(type(row) is dict and row.get("case") == name and row.get("attempted") is True
                     and row.get("passed") is True and row.get("cleanup_passed") is True)
        code = None if name == "eicar_direct_detection" else int(name in _REFUSALS)
        cases.append({**row, "diagnostic": _project_diagnostic(row.get("diagnostic"), code=code)})
    projected.update(event=v4_payload.CORPUS_EVENT, version=4, cases=cases)
    return v4.validate(projected, image, policy, runtime)
