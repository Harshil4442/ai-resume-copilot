# Daily successors always use this reviewed renderer base, never yesterday's
# successor. Only official database bytes enter the final layer.
FROM us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-document-inspector@sha256:0a7e550623f0485945f9bbe52573091aabedfd612eb353e6a8bbb920e2ffa94f AS definitions
USER 0
ARG DOCUMENT_DEFINITIONS_NONCE
RUN test -n "$DOCUMENT_DEFINITIONS_NONCE" \
    && test "${#DOCUMENT_DEFINITIONS_NONCE}" -eq 32 \
    && case "$DOCUMENT_DEFINITIONS_NONCE" in *[!0-9a-f]*) exit 1 ;; esac \
    && freshclam --stdout --datadir=/var/lib/clamav
FROM us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-document-inspector@sha256:0a7e550623f0485945f9bbe52573091aabedfd612eb353e6a8bbb920e2ffa94f
USER 0
# COPY merges directories: remove the old database first so a cvd->cld update
# cannot leave an obsolete base-layer file visible beside its replacement.
RUN rm -rf /var/lib/clamav
COPY --from=definitions /var/lib/clamav /var/lib/clamav
USER 65534:65534
