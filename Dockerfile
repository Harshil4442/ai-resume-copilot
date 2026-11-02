FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/

# Production backend image. The Next.js frontend is deployed separately (for
# example on Vercel) and talks to this service over the explicit API origin.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PGSSLROOTCERT=/etc/ssl/certs/ca-certificates.crt \
    PORT=10000 \
    PATH="/app/backend/.venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    libpq-dev \
    libfontconfig1 \
    fontconfig-config \
    libfreetype6 \
    fonts-dejavu-core \
    fonts-dejavu-mono \
    fonts-lmodern \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project \
    && .venv/bin/python -m spacy download en_core_web_sm

COPY backend/ ./

# Inert exact submission source inputs for the server-side delivery digest.
# No frontend build, execution, Node dependencies or additional runtime entrypoint.
COPY ["browser-companion/extension/assistance-runtime.js", "browser-companion/extension/assistance-transport.js", "browser-companion/extension/background.js", "browser-companion/extension/config.js", "browser-companion/extension/device.js", "browser-companion/extension/greenhouse-native-network.js", "browser-companion/extension/greenhouse-owned-upload.js", "browser-companion/extension/greenhouse-resume-component.js", "browser-companion/extension/local-adapter.js", "browser-companion/extension/lost-key-recovery.js", "browser-companion/extension/manifest.json", "browser-companion/extension/pairing-runtime.js", "browser-companion/extension/pairing-transport.js", "browser-companion/extension/popup.css", "browser-companion/extension/popup.html", "browser-companion/extension/popup.js", "browser-companion/extension/protocol.js", "browser-companion/extension/razorpay-adapter.js", "browser-companion/extension/startup-diagnostics.js", "browser-companion/extension/submission-billing.js", "browser-companion/extension/transport.js", "/app/browser-companion/extension/"]
COPY ["frontend/components/BrowserAssistanceReview.tsx", "/app/frontend/components/"]
COPY ["frontend/lib/browserAssistanceGateway.ts", "frontend/lib/browserHostedNetworkReview.ts", "frontend/lib/browserSubmissionBilling.ts", "/app/frontend/lib/"]


RUN chmod +x /app/backend/entrypoint.sh

RUN useradd --create-home --uid 10001 hirewiz \
    && chown -R hirewiz:hirewiz /app
USER hirewiz

EXPOSE 10000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '10000') + '/api/health', timeout=3)" || exit 1

CMD ["/app/backend/entrypoint.sh"]
