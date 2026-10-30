# Production only: preserve the reviewed renderer, fonts, rules, cache and manifest.
# The separate public Dockerfile remains the credential-free native CI builder.
FROM us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-document-inspector@sha256:bbfad0c7faa2acf0ef93d13df300a9ac34cb16a792fee04d873aa994ec3afb94 AS definitions
USER 0:0
ARG DOCUMENT_DEFINITIONS_NONCE
RUN test "${#DOCUMENT_DEFINITIONS_NONCE}" -eq 32 \
    && test -z "$(printf '%s' "$DOCUMENT_DEFINITIONS_NONCE" | tr -d '0-9a-f')" \
    && freshclam --stdout --datadir=/var/lib/clamav

FROM us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-document-inspector@sha256:bbfad0c7faa2acf0ef93d13df300a9ac34cb16a792fee04d873aa994ec3afb94
USER 0:0
RUN rm -rf /var/lib/clamav
COPY --from=definitions /var/lib/clamav /var/lib/clamav
# Only deterministic Python runtime files are overlaid; inherited fonts are attested.
COPY backend/app/services/parsing.py /opt/document/app/services/parsing.py
COPY backend/app/services/market/skill_extractor.py backend/app/services/market/skill_taxonomy.py       backend/app/services/market/security.py /opt/document/app/services/market/
COPY backend/app/services/resume_layout.py backend/app/services/pdf_streams.py       backend/app/services/docx_layout_validation.py backend/app/services/docx_fonts.py backend/app/services/native_project.py /opt/document/app/services/
COPY infra/document-processing/processor.py infra/document-processing/service.py       infra/document-processing/docx_renderer.py infra/document-processing/writer_owner.py       infra/document-processing/courier_prime_fonts.py /opt/document/
USER 65534:65534
WORKDIR /opt/document
ENTRYPOINT ["/usr/local/bin/python3", "-I", "/opt/document/service.py"]
