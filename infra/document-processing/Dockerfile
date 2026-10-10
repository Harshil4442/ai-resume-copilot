# Build with backend/scripts/document_test_image.py for a fresh owned nonce.
# Only deterministic parser/skill/safety helpers are copied; no app config,
# database, model clients, merchant credentials, source .env or submission code.
# Cloud Run sandbox launcher is injected only when explicitly enabled there.
FROM public.ecr.aws/docker/library/python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258 AS dependencies
RUN apt-get update && apt-get install -y --no-install-recommends clamav clamav-freshclam \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir --no-deps \
       cffi==2.1.0 charset-normalizer==3.4.9 cryptography==50.0.0 lxml==6.1.1 \
       pdfminer-six==20260107 pdfplumber==0.11.10 pillow==12.3.0 pycparser==3.0 \
       pypdf==6.19.0 pypdfium2==5.12.1 python-docx==1.2.0 rapidfuzz==3.14.5 typing-extensions==4.16.0
RUN mkdir -p /opt/document/app/services/market \
    && touch /opt/document/app/__init__.py /opt/document/app/services/__init__.py \
       /opt/document/app/services/market/__init__.py
# Only this acquisition stage is invalidated for each newly built image.
# The nonce does not establish freshness; scanner database headers do that.
FROM dependencies AS definitions
ARG DOCUMENT_DEFINITIONS_NONCE
RUN test -n "$DOCUMENT_DEFINITIONS_NONCE" \
    && test "${#DOCUMENT_DEFINITIONS_NONCE}" -eq 32 \
    && case "$DOCUMENT_DEFINITIONS_NONCE" in *[!0-9a-f]*) exit 1 ;; esac \
    && freshclam --stdout --datadir=/var/lib/clamav
FROM dependencies AS runtime
COPY --from=definitions /var/lib/clamav /var/lib/clamav
COPY backend/app/services/parsing.py /opt/document/app/services/parsing.py
COPY backend/app/services/market/skill_extractor.py backend/app/services/market/skill_taxonomy.py \
     backend/app/services/market/security.py /opt/document/app/services/market/
COPY infra/document-processing/processor.py infra/document-processing/service.py /opt/document/
# Immutable image rebuilds must refresh official definitions at least every
# three days. No freshclam/network updater runs while processing candidate data.
# The runtime scanner independently checks database-header age and rejects stale
# or unavailable definitions. Deployment/isolation/live scanner proof is open.
USER 65534:65534
WORKDIR /opt/document
ENTRYPOINT ["/usr/local/bin/python3", "-I", "/opt/document/service.py"]
