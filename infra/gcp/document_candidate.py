"""Source-only DOCX font candidate; no image or renderer acceptance is claimed.

The reviewed serving policy and renderer profiles remain historical evidence.
This inventory identifies candidate build inputs only. A future native image receipt
must establish its renderer and font manifest before a native probe can admit it.
"""
from __future__ import annotations

POLICY_SHA256 = "8ed5f38142d88d88883129681953e939e79157e8b92e3efc516920d2e4409c9f"
# Exact native candidate only. These pins do not assert that its corpus passed
# or that it is serving; document_candidate_release rereads the native evidence.
IMAGE = ("us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/"
         "hirewiz-document-inspector@sha256:bbfad0c7faa2acf0ef93d13df300a9ac34cb16a792fee04d873aa994ec3afb94")
RENDERER = "LibreOffice 25.2.3.2 520(Build:2)"
FONT = "87b2c806de6d5e3f528e393f3ad6b2a223f89e21f72f811bc96d8d21cc2cc526"
REFRESH_DOCKERFILE = "infra/document-processing/refresh-courier-v3.Dockerfile"
REFRESH_SHA256 = "8618952dc7f999d7e8138a421b5de0a59f9b518b5a8250da13cc407485509b5b"
POLICY_PATH = "infra/document-processing/policy.json"
DOCKERFILE = "infra/document-processing/Dockerfile"
RUNTIME = {
    "infra/document-processing/processor.py": "/opt/document/processor.py",
    "infra/document-processing/service.py": "/opt/document/service.py",
    "infra/document-processing/docx_renderer.py": "/opt/document/docx_renderer.py",
    "infra/document-processing/writer_owner.py": "/opt/document/writer_owner.py",
    "infra/document-processing/courier_prime_fonts.py": "/opt/document/courier_prime_fonts.py",
    "backend/app/services/parsing.py": "/opt/document/app/services/parsing.py",
    "backend/app/services/market/skill_extractor.py": "/opt/document/app/services/market/skill_extractor.py",
    "backend/app/services/market/skill_taxonomy.py": "/opt/document/app/services/market/skill_taxonomy.py",
    "backend/app/services/market/security.py": "/opt/document/app/services/market/security.py",
    "backend/app/services/resume_layout.py": "/opt/document/app/services/resume_layout.py",
    "backend/app/services/native_project.py": "/opt/document/app/services/native_project.py",
    "backend/app/services/pdf_streams.py": "/opt/document/app/services/pdf_streams.py",
    "backend/app/services/docx_layout_validation.py": "/opt/document/app/services/docx_layout_validation.py",
    "backend/app/services/docx_fonts.py": "/opt/document/app/services/docx_fonts.py",
    "backend/evals/assets/courier_prime/CourierPrime-Regular.ttf": "/usr/share/fonts/truetype/hirewiz-courier-prime/CourierPrime-Regular.ttf",
    "backend/evals/assets/courier_prime/CourierPrime-Bold.ttf": "/usr/share/fonts/truetype/hirewiz-courier-prime/CourierPrime-Bold.ttf",
    "backend/evals/assets/courier_prime/CourierPrime-Italic.ttf": "/usr/share/fonts/truetype/hirewiz-courier-prime/CourierPrime-Italic.ttf",
    "backend/evals/assets/courier_prime/CourierPrime-BoldItalic.ttf": "/usr/share/fonts/truetype/hirewiz-courier-prime/CourierPrime-BoldItalic.ttf",
    "backend/evals/assets/courier_prime/OFL.txt": "/usr/share/doc/hirewiz-courier-prime/OFL.txt",
    "backend/evals/assets/courier_prime/provenance.json": "/usr/share/doc/hirewiz-courier-prime/provenance.json",
}
SOURCES = frozenset({DOCKERFILE, *RUNTIME})
