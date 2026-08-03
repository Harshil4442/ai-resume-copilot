FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/

# Production backend image. The Next.js frontend is deployed separately (for
# example on Vercel) and talks to this service over the explicit API origin.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=10000 \
    PATH="/app/backend/.venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    libpq-dev \
    texlive-latex-base \
    texlive-fonts-recommended \
    texlive-latex-extra \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project \
    && .venv/bin/python -m spacy download en_core_web_sm

COPY backend/ ./

RUN chmod +x /app/backend/entrypoint.sh

RUN useradd --create-home --uid 10001 hirewiz \
    && chown -R hirewiz:hirewiz /app
USER hirewiz

EXPOSE 10000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '10000') + '/api/health', timeout=3)" || exit 1

CMD ["/app/backend/entrypoint.sh"]
