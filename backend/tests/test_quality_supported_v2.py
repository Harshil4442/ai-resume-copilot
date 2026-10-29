"""The new native-compatible packet is separate from the immutable v1 fixture."""
from __future__ import annotations

import copy
import io
import json
import shutil
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

import pytest

from app.domains.analysis import quality_materialization as native
from app.domains.analysis import quality_packet as packet
from app.domains.analysis import quality_supported_v2 as supported

ROOT = Path(__file__).resolve().parents[1] / "evals"
V1 = ROOT / "unified_credit_v1"
V2 = ROOT / "unified_credit_v2"


def test_frozen_supported_packet_preserves_every_pair_and_split():
    result = supported.verify_lineage(V1, V2)
    assert result == {
        "source_lineage_valid": True,
        "case_count": 200,
        "formats": {"docx": 50, "pdf": 50, "tex": 50, "texzip": 50},
        "development_pairs": 40,
        "held_out_pairs": 160,
        "authorizes_release": False,
    }
    old = packet.load_packet(V1)[2]
    new = packet.load_packet(V2)[2]
    assert [case["case_id"] for case in old] == [case["case_id"] for case in new]
    assert all(before["resume"] == after["resume"] and before["job"] == after["job"]
               and before["cohorts"] == after["cohorts"] and before["split"] == after["split"]
               for before, after in zip(old, new, strict=True))
    assert Counter(case["source_format"] for case in new) == dict.fromkeys(packet.FORMATS, 50)
    template = json.loads((V2 / "bundle.template.json").read_bytes())
    assert template["schema_version"] == packet.SUPPORTED_VERSION
    assert len(template["cases"]) == 200
    assert all(row["baseline"] is None and row["candidate"] is None for row in template["cases"])


def test_only_reviewed_native_recipe_changes_are_allowed():
    old = packet.load_packet(V1)[2]
    new = packet.load_packet(V2)[2]
    for before, after in zip(old, new, strict=True):
        assert after == supported.transformed(before)
        recipe = after["source_recipe"]
        if after["source_format"] in {"tex", "texzip"}:
            assert recipe["engine"] == supported.ENGINE
            assert recipe["font"] in supported.TEX_FONT_MAP.values()
            tex = native._tex(after).decode()
            assert r"\usepackage{fontspec}" not in tex
            assert r"\usepackage{lmodern}" in tex
        elif after["source_format"] == "docx":
            assert recipe["font"] in supported.DOCX_FONT_MAP.values()
        else:
            assert after == before
    changed = copy.deepcopy(old[0])
    changed["source_format"] = "tex"
    changed["source_recipe"]["font"] = "unknown"
    with pytest.raises(ValueError, match="no reviewed"):
        supported.transformed(changed)


def test_v2_materialization_keeps_source_packet_and_unqualified_state(tmp_path):
    examples = [next(case for case in packet.load_packet(V2)[2] if case["source_format"] == kind)
                for kind in ("docx", "tex", "texzip")]
    before = {p.name: packet.file_digest(p) for p in V2.iterdir() if p.is_file()}
    destination = tmp_path / "sources"
    result = native.materialize(V2, destination, case_ids=[case["case_id"] for case in examples])
    assert result["schema_version"] == "unified-quality-materialization-v2"
    assert result["case_count"] == result["materialized_count"] == 3
    assert result["authorizes_release"] is False and result["quality_status"] == "not_evaluated"
    assert before == {p.name: packet.file_digest(p) for p in V2.iterdir() if p.is_file()}
    for row in result["cases"]:
        folder = destination / row["case_id"]
        assert row["rendered_proof"] == row["visual_review"] == "pending"
        assert packet.file_digest(destination / row["input_artifact"]["path"]) == row["input_artifact"]["sha256"]
        if row["source_format"] == "docx":
            assert not (folder / "source.tex").exists()
        if row["source_format"] == "texzip":
            with ZipFile(io.BytesIO((folder / "source.texzip").read_bytes())) as archive:
                assert archive.namelist() == ["main.tex"]
                assert archive.read("main.tex") == (folder / "source.tex").read_bytes()


def test_v2_lineage_rejects_a_rebuilt_but_different_source(tmp_path):
    destination = tmp_path / "unified_credit_v2"
    supported.build(V1, destination)
    manifest, _, cases = packet.load_packet(destination)
    cases[0]["source_recipe"]["margin_inches"] = 0.75
    raw = b"".join(packet.canonical(case) for case in cases)
    (destination / "cases.jsonl").write_bytes(raw)
    manifest["corpus_sha256"] = packet.digest(raw)
    manifest["cases"][0]["sha256"] = packet.digest(packet.canonical(cases[0]))
    (destination / "manifest.json").write_bytes(packet.canonical(manifest))
    assert packet.validate_packet(destination)["draft_inputs_valid"]
    assert not supported.verify_lineage(V1, destination)["source_lineage_valid"]
    shutil.copytree(V1, tmp_path / "unified_credit_v1")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    result = packet.evaluate_evidence(destination, evidence)
    assert result["evidence_complete"] is False
    assert "source lineage" in result["errors"][0]
