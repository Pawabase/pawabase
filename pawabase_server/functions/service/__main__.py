"""``python -m pawabase_server.functions.service``: run the functions runtime service.

It listens on ``PAWABASE_FUNCTIONS_RUNTIME_HOST``:``PAWABASE_FUNCTIONS_RUNTIME_PORT`` (``127.0.0.1:8100``; ``0.0.0.0`` in its container,
which is on the internal network only). The server reaches it at ``PAWABASE_FUNCTIONS_RUNTIME_URL``; it reaches the server at
``PAWABASE_FUNCTIONS_API_URL``. Both sides share ``PAWABASE_INTERNAL_SECRET`` and the deployments volume.
"""

from __future__ import annotations

import logging

import uvicorn

from pawabase_server.functions.service.app import RuntimeService
from pawabase_server.settings import Settings


def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if settings.app_env == "production":
        problems = settings.validate_for_production()
        if problems:
            raise SystemExit("refusing to start: " + "; ".join(problems))
    uvicorn.run(
        RuntimeService(settings),
        host=settings.functions_runtime_host,
        port=settings.functions_runtime_port,
        lifespan="on",
        log_level="info",
        proxy_headers=False,
        access_log=False,
        timeout_keep_alive=75,
    )


if __name__ == "__main__":
    main()
