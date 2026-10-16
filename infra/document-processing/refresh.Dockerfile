# Daily successors always use this reviewed renderer base, never yesterday's
# successor. Only official database bytes enter the final layer.
FROM us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-document-inspector@sha256:624597726ce2fefa2b3574fbf0a8ec4ccdc02a826380d76c5edc8ebbd59aa107 AS definitions
USER 0
ARG DOCUMENT_DEFINITIONS_NONCE
RUN test -n "$DOCUMENT_DEFINITIONS_NONCE" \
    && test "${#DOCUMENT_DEFINITIONS_NONCE}" -eq 32 \
    && case "$DOCUMENT_DEFINITIONS_NONCE" in *[!0-9a-f]*) exit 1 ;; esac \
    && freshclam --stdout --datadir=/var/lib/clamav
FROM us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-document-inspector@sha256:624597726ce2fefa2b3574fbf0a8ec4ccdc02a826380d76c5edc8ebbd59aa107
USER 0
# COPY merges directories: remove the old database first so a cvd->cld update
# cannot leave an obsolete base-layer file visible beside its replacement.
RUN rm -rf /var/lib/clamav
COPY --from=definitions /var/lib/clamav /var/lib/clamav
USER 65534:65534
