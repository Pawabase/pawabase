# Development image: dependencies only. The source is bind-mounted at /app by
# docker-compose.dev.yml, so code changes never need a rebuild; rebuild only
# when a pyproject.toml or uv.lock changes.
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app \
    SILLO_ENV_FILE=
# git: the Pawabase kit is installed from its repository (see [tool.uv.sources] in pyproject.toml).
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "uv>=0.8,<0.9"
WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY api/pyproject.toml api/pyproject.toml
COPY akountz/pyproject.toml akountz/pyproject.toml
COPY angula/pyproject.toml angula/pyproject.toml
COPY gateway/pyproject.toml gateway/pyproject.toml
COPY studio/pyproject.toml studio/pyproject.toml
RUN uv sync --frozen --all-packages \
    && uv pip install --python /opt/venv/bin/python watchfiles

COPY docker/entrypoint.sh /usr/local/bin/pawabase-service
RUN chmod +x /usr/local/bin/pawabase-service
ENTRYPOINT ["pawabase-service"]
CMD ["help"]
