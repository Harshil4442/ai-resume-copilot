# Root Dockerfile: runs BOTH frontend (Next.js) and backend (FastAPI) in ONE container
# Target: Render Docker Web Service (single domain; no CORS needed)

FROM python:3.12-slim

# OS deps (Node install + build deps)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates gnupg \
    build-essential \
    libpq-dev \
    texlive-latex-base \
    texlive-fonts-recommended \
    texlive-latex-extra \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 20 LTS
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get update && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Backend deps
COPY backend/requirements.txt ./backend/requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r backend/requirements.txt


# Install spaCy model at build time (avoids runtime E050)
RUN python -m spacy download en_core_web_sm
# Frontend deps + build
COPY frontend/package.json ./frontend/package.json
# If you later add package-lock.json, you can switch to npm ci.
WORKDIR /app/frontend
RUN npm install

COPY frontend/ ./
# Build with /api default (same-origin proxy)
ENV NEXT_PUBLIC_API_BASE_URL=/api
RUN npm run build

# Backend code
WORKDIR /app
COPY backend/ ./backend/

# Entrypoint
COPY start.sh ./start.sh
RUN chmod +x ./start.sh

ENV NODE_ENV=production
EXPOSE 10000
CMD ["./start.sh"]
