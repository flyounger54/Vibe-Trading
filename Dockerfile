# syntax=docker/dockerfile:1.7
FROM node:20-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0 AS frontend-build

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run build

FROM ubuntu:24.04@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90 AS python-build

COPY --from=ghcr.io/astral-sh/uv:0.11.28@sha256:0f36cb9361a3346885ca3677e3767016687b5a170c1a6b88465ec14aefec90aa /uv /uvx /bin/
ENV UV_LINK_MODE=copy \
    UV_PYTHON=/usr/bin/python3.12 \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /app

# Compilers are isolated to the build stage and never reach production.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && sed -i 's|http://|https://|g' /etc/apt/sources.list.d/ubuntu.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
        build-essential python3.12 python3.12-dev python3.12-venv \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock LICENSE README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY agent/ agent/
RUN uv sync --locked --no-dev --no-editable

FROM ubuntu:24.04@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90 AS runtime

LABEL org.opencontainers.image.title="Vibe-Trading" \
    org.opencontainers.image.description="Natural-language finance research AI agent with backtesting" \
    org.opencontainers.image.version="0.1.10" \
    org.opencontainers.image.source="https://github.com/HKUDS/Vibe-Trading" \
    org.opencontainers.image.licenses="MIT"

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/agent" \
    PYTHONUNBUFFERED=1 \
    VIBE_TRADING_LOG_FORMAT=json
WORKDIR /app

# Python and WeasyPrint runtime libraries only; the compiler toolchain stays
# isolated in python-build. Both stages use the same pinned distribution ABI.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && sed -i 's|http://|https://|g' /etc/apt/sources.list.d/ubuntu.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    python3.12 \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    libfontconfig1 \
    libgdk-pixbuf-2.0-0 \
    libcairo2 \
    libgdbm6t64 \
    libreadline8t64 \
    libsqlite3-0 \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY --from=python-build /app/.venv /app/.venv
COPY --from=python-build /app/agent /app/agent
COPY --from=frontend-build /app/frontend/dist /app/frontend/dist

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin vibe \
    && install -d -o vibe -g vibe \
        /app/agent/runs \
        /app/agent/sessions \
        /app/agent/uploads \
        /app/agent/.swarm/runs \
        /home/vibe/.vibe-trading
USER 10001:10001

EXPOSE 8899
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8899/readyz', timeout=3)" || exit 1

CMD ["vibe-trading", "serve", "--host", "0.0.0.0", "--port", "8899"]
