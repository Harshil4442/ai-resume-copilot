# Build from repository root: docker build -f infra/document-processing/Dockerfile .
# Only deterministic parser/skill/safety helpers are copied; no app config,
# database, model clients, merchant credentials, source .env or submission code.
# Cloud Run sandbox launcher is injected only when explicitly enabled there.
FROM public.ecr.aws/docker/library/python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
RUN apt-get update && apt-get install -y --no-install-recommends clamav clamav-freshclam \
    && freshclam --stdout --datadir=/var/lib/clamav \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir --no-deps \
       cffi==2.1.0 charset-normalizer==3.4.9 cryptography==50.0.0 lxml==6.1.1 \
       pdfminer-six==20260107 pdfplumber==0.11.10 pillow==12.3.0 pycparser==3.0 \
       pypdf==6.19.0 pypdfium2==5.12.1 python-docx==1.2.0 rapidfuzz==3.14.5 typing-extensions==4.16.0
RUN mkdir -p /opt/document/app/services/market \
    && touch /opt/document/app/__init__.py /opt/document/app/services/__init__.py \
       /opt/document/app/services/market/__init__.py
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
