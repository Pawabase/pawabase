# Development image: dependencies only. The source is bind-mounted at /app by docker-compose.dev.yml, so code changes never need a
# rebuild; rebuild only when pyproject.toml or uv.lock changes.
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
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "uv>=0.8,<0.9"
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project \
    && uv pip install --python /opt/venv/bin/python watchfiles
EXPOSE 8080 8001 8090
# Restart the server whenever Python changes on the bind-mounted source. (Studio overrides the command in docker-compose.dev.yml.)
CMD ["watchfiles", "--filter", "python", "python -m pawabase_server serve", "/app/pawabase_server"]
