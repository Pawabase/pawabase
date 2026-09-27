# One image, every Pawabase service. Compose runs it once per service with a
# different command (see docker/entrypoint.sh), so the services stay separate
# processes and containers while sharing a single build.

# ── Studio's front end ─────────────────────────────────────────────────────
FROM node:22-slim AS studio-assets
WORKDIR /src
COPY services/studio/frontend/package.json services/studio/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY services/studio/frontend/ ./
RUN npm run build

# ── Python services ────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    SILLO_ENV_FILE=
RUN pip install --no-cache-dir "uv>=0.8,<0.9"
WORKDIR /app

# Dependencies first, so code changes do not reinstall them.
COPY pyproject.toml uv.lock ./
COPY packages/kit/pyproject.toml packages/kit/pyproject.toml
COPY services/api/pyproject.toml services/api/pyproject.toml
COPY services/akountz/pyproject.toml services/akountz/pyproject.toml
COPY services/angula/pyproject.toml services/angula/pyproject.toml
COPY services/gateway/pyproject.toml services/gateway/pyproject.toml
COPY services/studio/pyproject.toml services/studio/pyproject.toml
RUN mkdir -p packages/kit/pawabase_kit && touch packages/kit/pawabase_kit/__init__.py \
    && uv sync --frozen --no-dev --all-packages

COPY packages packages
COPY services services
COPY examples examples
COPY docker/entrypoint.sh /usr/local/bin/pawabase
RUN uv sync --frozen --no-dev --all-packages \
    && chmod +x /usr/local/bin/pawabase \
    && useradd --create-home --uid 10001 pawabase \
    && mkdir -p /data /code \
    && chown -R pawabase:pawabase /data /code
COPY --from=studio-assets /src/dist services/studio/frontend/dist

USER pawabase
VOLUME ["/data"]
ENTRYPOINT ["pawabase"]
CMD ["help"]
