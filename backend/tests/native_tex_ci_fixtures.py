"""Explicit, fail-closed compiler prerequisite shared by real native tests."""

import hashlib

from backend.app.services import document_ingestion
from backend.scripts.native_tex_test_image import required_test_image


def configure_synthetic_native_inspector(monkeypatch) -> None:
    """Synthetic inspection transport only; never scan/isolation acceptance.

    Native compiler tests still execute the genuinely attested Docker compiler.
    Their synthetic documents use the real deterministic PDF parser in this
    explicit test boundary; no production scanner/parser fallback is installed.
    """
    from backend.app.services.parsing import parse_resume_file

    image = "synthetic-document-inspector@sha256:" + "3" * 64
    policy = "4" * 64
    monkeypatch.setenv("DOCUMENT_WORKER_URL", "https://synthetic-document.run.app/inspect")
    monkeypatch.setenv("DOCUMENT_WORKER_IMAGE_DIGEST", image)
    monkeypatch.setenv("DOCUMENT_WORKER_POLICY_SHA256", policy)
    monkeypatch.setenv("NATIVE_TEX_ISOLATION_POLICY_SHA256", "5" * 64)

    def inspect(data, *, source_format):
        parsed = parse_resume_file(data, "synthetic.pdf", use_llm=False) if source_format == "pdf" else ("", {}, [], 0.0, {})
        return document_ingestion.InspectedResume(
            parsed=parsed, sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data),
            source_format=source_format, worker_image=image, policy_sha256=policy,
            scan_engine="ClamAV", scan_version="synthetic-test", scan_definitions="synthetic-test-only",
        )

    monkeypatch.setattr(document_ingestion, "inspect_resume_document_with_receipt", inspect)


def configure_native_compiler(monkeypatch) -> str:
    image = required_test_image()
    configure_synthetic_native_inspector(monkeypatch)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("NATIVE_TEX_IMAGE", image)
    monkeypatch.delenv("NATIVE_TEX_COMPILER_URL", raising=False)
    return image
