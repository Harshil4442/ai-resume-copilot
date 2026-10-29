"""Derive a separate, frozen pdfLaTeX-compatible synthetic quality packet.

This preserves every v1 resume/job pair, case ID, cohort and held-out split.
Only native font/compiler fixture recipes change. It never renders, calls a model, or grants
reference/release authority.
"""
from __future__ import annotations

import copy
import shutil
from collections import Counter
from pathlib import Path

from . import quality_packet as packet

ENGINE = "pdftex-no-shell-escape-v1"
TEX_FONT_MAP = {"Times New Roman": "Latin Modern Roman", "Courier Prime": "Latin Modern Mono"}
DOCX_FONT_MAP = {"Times New Roman": "Liberation Serif", "Courier Prime": "Liberation Mono"}


def transformed(case: dict) -> dict:
    changed = copy.deepcopy(case)
    if changed["source_format"] in {"tex", "texzip"}:
        recipe = changed["source_recipe"]
        if recipe["font"] not in TEX_FONT_MAP:
            raise ValueError("The v1 TeX font has no reviewed pdfLaTeX fixture mapping")
        recipe["font"] = TEX_FONT_MAP[recipe["font"]]
        recipe["engine"] = ENGINE
    elif changed["source_format"] == "docx":
        recipe = changed["source_recipe"]
        if recipe["font"] not in DOCX_FONT_MAP:
            raise ValueError("The v1 DOCX font has no exact installed-font fixture mapping")
        recipe["font"] = DOCX_FONT_MAP[recipe["font"]]
    return changed


def _protocol(source: dict) -> dict:
    derived = copy.deepcopy(source)
    derived.update(
        schema_version=packet.SUPPORTED_VERSION,
        state="supported_source_draft_requires_native_and_human_approval",
        native_inputs=("PDF retains its exact embedded source-font recipes. DOCX uses the "
                       "installed Liberation Serif or Mono family. TeX and TeXZIP use "
                       "pdfLaTeX with Latin Modern Roman or Mono. These are new fixture inputs, "
                       "not preserved outputs from the v1 sources."),
        source_engine_policy=ENGINE,
        source_quality_status="not_evaluated",
    )
    return derived


def build(source: Path, destination: Path) -> dict:
    if destination.exists():
        raise ValueError("The supported packet already exists; create another version")
    original = packet.validate_packet(source)
    if not original.get("draft_inputs_valid") or original.get("schema_version") != packet.VERSION:
        raise ValueError("The frozen v1 source packet must validate")
    source_manifest, source_protocol, source_cases = packet.load_packet(source)
    if len(source_cases) != 200 or source_manifest["dataset_id"] != "hirewiz-synthetic-draft-200-v1":
        raise ValueError("The exact reviewed v1 synthetic source is required")
    cases = [transformed(case) for case in source_cases]
    if Counter(case["split"] for case in cases) != {"development": 40, "held_out": 160}:
        raise ValueError("The frozen development and held-out split changed")
    destination.mkdir(parents=True)
    shutil.copyfile(source / "rubric.md", destination / "rubric.md")
    protocol = _protocol(source_protocol)
    (destination / "protocol.json").write_bytes(packet.canonical(protocol))
    raw = b"".join(packet.canonical(case) for case in cases)
    (destination / "cases.jsonl").write_bytes(raw)
    manifest = {
        "schema_version": packet.SUPPORTED_VERSION,
        "dataset_id": "hirewiz-synthetic-supported-pdftex-200-v2",
        "state": "frozen_synthetic_draft_not_approved_reference",
        "source_manifest_sha256": packet.file_digest(source / "manifest.json"),
        "source_corpus_sha256": packet.file_digest(source / "cases.jsonl"),
        "case_count": len(cases), "corpus_sha256": packet.digest(raw),
        "protocol_sha256": packet.file_digest(destination / "protocol.json"),
        "rubric_sha256": packet.file_digest(destination / "rubric.md"),
        "coverage": packet.coverage(cases),
        "cases": [{"case_id": case["case_id"], "split": case["split"],
                   "sha256": packet.digest(packet.canonical(case))} for case in cases],
        "quality_status": "not_evaluated", "authorizes_release": False,
    }
    (destination / "manifest.json").write_bytes(packet.canonical(manifest))
    if not verify_lineage(source, destination)["source_lineage_valid"]:
        raise ValueError("The derived packet failed its frozen source-lineage check")
    packet.write_templates(destination)
    return manifest


def verify_lineage(source: Path, destination: Path) -> dict:
    original = packet.validate_packet(source)
    derived = packet.validate_packet(destination)
    if not original.get("draft_inputs_valid") or not derived.get("draft_inputs_valid"):
        return {"source_lineage_valid": False, "authorizes_release": False}
    source_manifest, source_protocol, source_cases = packet.load_packet(source)
    derived_manifest, derived_protocol, derived_cases = packet.load_packet(destination)
    valid = (
        original["schema_version"] == packet.VERSION
        and derived["schema_version"] == packet.SUPPORTED_VERSION
        and source_manifest["case_count"] == derived_manifest["case_count"] == 200
        and derived_manifest.get("source_manifest_sha256") == packet.file_digest(source / "manifest.json")
        and derived_manifest.get("source_corpus_sha256") == packet.file_digest(source / "cases.jsonl")
        and derived_manifest.get("dataset_id") == "hirewiz-synthetic-supported-pdftex-200-v2"
        and packet.canonical(derived_protocol) == packet.canonical(_protocol(source_protocol))
        and (destination / "rubric.md").read_bytes() == (source / "rubric.md").read_bytes()
        and len(source_cases) == len(derived_cases)
        and all(packet.canonical(transformed(before)) == packet.canonical(after)
                for before, after in zip(source_cases, derived_cases, strict=True))
    )
    return {"source_lineage_valid": valid, "case_count": len(derived_cases),
            "formats": dict(sorted(Counter(case["source_format"] for case in derived_cases).items())),
            "development_pairs": sum(case["split"] == "development" for case in derived_cases),
            "held_out_pairs": sum(case["split"] == "held_out" for case in derived_cases),
            "authorizes_release": False}
