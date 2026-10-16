"""The whole server, assembled the way it runs: one application, the public entry point in front of it, and Studio beside it."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import pytest

os.environ.setdefault("SILLO_ENV_FILE", "")


@dataclass
class Running:
    server: Any
    #: The public entry point: what a client of the deployed server sees.
    public: Any
    #: Studio, a service of its own, managing the server through a client.
    studio: Any
    #: Management calls, authenticated the way Studio authenticates (a service token, in-process).
    manage: Any
    publishable: str
    secret: str


@pytest.fixture
async def running(tmp_path):
    from sillo.testclient import AsyncTestClient

    from pawabase_server.app import create_server
    from pawabase_server.core.clients import ServiceClient
    from pawabase_server.functions.registry import clear_functions
    from pawabase_server.settings import Settings

    clear_functions()
    settings = Settings(
        _env_file=None,
        app_env="testing",
        database_url=f"sqlite://{tmp_path}/server.db",
        db_generate_schemas=True,
        default_data_url=f"sqlite://{tmp_path}/data/{{env}}.db",
        storage_root=str(tmp_path / "objects"),
        code_path=str(tmp_path / "code"),
        inline_worker=True,
        inline_scheduler=False,
        function_isolation="inprocess",
        record_buffer_seconds=0,
        status_probe_interval=0,
        domain_refresh_interval=0,
    )
    server = create_server(settings)
    await server.app._startup()
    manage = ServiceClient(
        "http://internal", secret=settings.internal_secret, issuer="test", app=server.app
    )
    public = AsyncTestClient(server.public, base_url="http://api.test")
    from studio.app import create_studio
    from studio.config import StudioSettings

    frontend = tmp_path / "studio-frontend"
    dist = frontend / "dist"
    (dist / ".vite").mkdir(parents=True)
    (dist / "assets").mkdir()
    (dist / ".vite" / "manifest.json").write_text(
        '{"src/main.jsx": {"file": "assets/main-abc.js", "css": ["assets/main-abc.css"]}}'
    )
    (dist / "assets" / "main-abc.js").write_text("console.log('studio')")
    studio_app = create_studio(
        StudioSettings(
            _env_file=None,
            app_env="testing",
            internal_secret=settings.internal_secret,
            frontend_dir=str(frontend),
        ),
        client=ServiceClient(
            "http://server", secret=settings.internal_secret, issuer="studio", app=server.app
        ),
    )
    studio = AsyncTestClient(studio_app, base_url="http://studio.test")
    base = "/platform/v1/envs/development/keys"
    publishable = (await manage.post(base, json={"name": "web", "role": "publishable"}))["key"]
    secret = (await manage.post(base, json={"name": "server", "role": "secret"}))["key"]
    try:
        yield Running(server, public, studio, manage, publishable, secret)
    finally:
        await manage.close()
        await public.aclose()
        await studio.aclose()
        await server.app._shutdown()
        clear_functions()
