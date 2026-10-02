# One image, every Pawabase service. Compose runs it once per service with a
# different command (see docker/entrypoint.sh), so the services stay separate
# processes and containers while sharing a single build.

# ── Studio's front end ─────────────────────────────────────────────────────
FROM node:22-slim AS studio-assets
WORKDIR /src
COPY studio/frontend/package.json studio/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY studio/frontend/ ./
RUN npm run build

# ── Python services ────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app \
    SILLO_ENV_FILE=
RUN pip install --no-cache-dir "uv>=0.8,<0.9"
WORKDIR /app

# Dependencies first, so code changes do not reinstall them.
COPY pyproject.toml uv.lock ./
COPY api/pyproject.toml api/pyproject.toml
COPY akountz/pyproject.toml akountz/pyproject.toml
COPY angula/pyproject.toml angula/pyproject.toml
COPY gateway/pyproject.toml gateway/pyproject.toml
COPY studio/pyproject.toml studio/pyproject.toml
RUN uv sync --frozen --no-dev --all-packages

# Libraries the deployed functions import (the platform does not know what they are). Edit docker/function-requirements.txt, rebuild.
COPY docker/function-requirements.txt /tmp/function-requirements.txt
RUN uv pip install --python /opt/venv/bin/python --no-cache -r /tmp/function-requirements.txt
COPY pawabase pawabase
COPY pawabase_core pawabase_core
COPY api api
COPY akountz akountz
COPY angula angula
COPY gateway gateway
COPY studio studio
COPY examples examples
COPY docker/entrypoint.sh /usr/local/bin/pawabase
RUN chmod +x /usr/local/bin/pawabase \
    && useradd --create-home --uid 10001 pawabase \
    && mkdir -p /data /code \
    && chown -R pawabase:pawabase /data /code
COPY --from=studio-assets /src/dist studio/frontend/dist

USER pawabase
VOLUME ["/data"]
ENTRYPOINT ["pawabase"]
CMD ["help"]
