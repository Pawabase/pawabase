# One image, four commands. `serve` is the Pawabase server: one process with identity, the management plane, the data plane, storage,
# flows, realtime and the public entry point. `studio` is the control plane, a service of its own that manages the server over its
# internal listener. `worker` and `scheduler` are the queue worker and the scheduler, each a process of its own (`serve` also starts and
# supervises them as children unless PAWABASE_WORKER_MODE=external). Compose runs them as containers next to Postgres, Redis and storage.

# ── Studio's front end ─────────────────────────────────────────────────────
FROM node:22-slim AS studio-assets
WORKDIR /src
COPY studio/frontend/package.json studio/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY studio/frontend/ ./
RUN npm run build

# ── Python dependencies and the package ────────────────────────────────────
FROM python:3.11-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
# git: the Pawabase kit is installed from its repository (see [tool.uv.sources]).
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "uv>=0.8,<0.9"
WORKDIR /app
# Dependencies first, so a code change does not reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ── Runtime ────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app \
    SILLO_ENV_FILE= \
    PAWABASE_HOST=0.0.0.0 \
    PAWABASE_PORT=8080 \
    PAWABASE_INTERNAL_HOST=0.0.0.0 \
    PAWABASE_INTERNAL_PORT=8001 \
    PAWABASE_STUDIO_HOST=0.0.0.0 \
    PAWABASE_STUDIO_PORT=8090 \
    PAWABASE_STORAGE_ROOT=/data/objects \
    PAWABASE_CODE_PATH=/code
COPY --from=build /opt/venv /opt/venv
# Both packages run from source (Studio's built front end sits inside its own), so there is no wheel to rebuild or to leave files out of.
COPY pawabase_server /app/pawabase_server
COPY studio /app/studio
COPY --from=studio-assets /src/dist /app/studio/frontend/dist
COPY docker/entrypoint.sh /usr/local/bin/entrypoint
RUN chmod +x /usr/local/bin/entrypoint \
    && useradd --create-home --uid 10001 pawabase \
    && mkdir -p /data /code \
    && chown -R pawabase:pawabase /data /code
USER pawabase
WORKDIR /home/pawabase
VOLUME ["/data"]
EXPOSE 8080 8001 8090
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"
ENTRYPOINT ["entrypoint"]
CMD ["serve"]
