"""Running Studio as a service of its own: ``python -m studio``."""

from __future__ import annotations

import logging

import uvicorn

from studio.app import create_studio
from studio.config import StudioSettings


def main() -> int:
    settings = StudioSettings()
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(levelname)s %(name)s: %(message)s"
    )
    uvicorn.run(
        create_studio(settings),
        host=settings.studio_host,
        port=settings.studio_port,
        log_level=settings.log_level,
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
        access_log=settings.log_level == "debug",
    )
    return 0
