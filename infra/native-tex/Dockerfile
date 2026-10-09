# Build in a reviewed dependency stage, then configure the resulting immutable
# image digest. The application refuses mutable tags at execution time.
FROM public.ecr.aws/docker/library/debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587
RUN apt-get update && apt-get install -y --no-install-recommends python3 texlive-latex-base texlive-latex-recommended lmodern && rm -rf /var/lib/apt/lists/*
COPY processor.py service.py /
RUN mkdir -p /work && chown 65534:65534 /work
USER 65534:65534
WORKDIR /work
ENTRYPOINT ["python3", "-I", "/processor.py"]
