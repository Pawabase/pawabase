import os
from dataclasses import dataclass
from typing import Any

import pytest

os.environ.setdefault("SILLO_ENV_FILE", "")


class FakePlatform:
    """Stands in for the platform: what identity asks of it (an environment's auth settings, mail and events), recorded for assertions."""

    def __init__(self, settings: Any) -> None:
        from sillo.cache import MemoryCache

        from pawabase_server.core.crypto import SecretBox

        self.settings = settings
        self.box = SecretBox(settings.master_key)
        self.cache = MemoryCache(namespace="test")
        self.auth: dict[str, Any] = {}
        self.mail: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []

    async def auth_settings(self, env: str) -> dict[str, Any]:
        from sillo.exceptions import HTTPException

        if env not in ("development", "production"):
            raise HTTPException(status_code=404, detail=f"no environment {env!r}")
        return {
            "env": env,
            "project_name": "Acme",
            "auth": self.auth,
            "public_url": "http://gateway.test",
        }

    async def queue_mail(self, env: str, **message: Any) -> str:
        self.mail.append({"env": env, **message})
        return "j"

    async def emit_event(self, env: str, name: str, payload: Any, *, actor: Any = None) -> str:
        self.events.append({"env": env, "name": name, "payload": payload, "actor": actor})
        return "e"

    def last_link(self) -> str:
        import re

        return re.search(r"https?://\S+", self.mail[-1]["text"]).group(0)

    def last_token(self) -> str:
        from urllib.parse import parse_qs, urlparse

        return parse_qs(urlparse(self.last_link()).query)["token"][0]


@dataclass
class Harness:
    app: Any
    http: Any
    admin: Any
    settings: Any
    api: FakePlatform
    accounts: Any

    def headers(self, env: str = "development", token: str | None = None) -> dict[str, str]:
        from pawabase_server.core.context import CONTEXT_HEADER, PlatformContext
        from pawabase_server.core.tokens import issue_context_token

        headers = {
            CONTEXT_HEADER: issue_context_token(
                self.settings.internal_secret, PlatformContext(env=env)
            )
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def signup(
        self, email="ada@example.com", password="correct-horse-1", **extra
    ) -> dict[str, Any]:
        response = await self.http.post(
            "/auth/v1/signup",
            json={"email": email, "password": password, **extra},
            headers=self.headers(),
        )
        assert response.status_code == 201, response.text
        return response.json()


@pytest.fixture
async def akz(tmp_path):
    from sillo.record import Record
    from sillo.testclient import AsyncTestClient

    from pawabase_server.core.auth import UserBackend
    from pawabase_server.core.clients import ServiceClient
    from pawabase_server.core.service import create_service
    from pawabase_server.db.database import MODEL_MODULES, database_config
    from pawabase_server.identity.accounts import Accounts
    from pawabase_server.routes.identity import register_routes
    from pawabase_server.settings import Settings

    settings = Settings(
        _env_file=None,
        app_env="testing",
        database_url=f"sqlite://{tmp_path}/accounts.db",
        db_generate_schemas=True,
        public_url="http://gateway.test",
    )
    platform = FakePlatform(settings)
    accounts = Accounts(platform)
    app = create_service(
        "pawabase",
        settings,
        title="Identity under test",
        description="Only the identity routes, on a stand-in platform.",
        backends=[UserBackend(settings.jwt_master_secret)],
        installables=[Record(database_config(settings), tuple(MODEL_MODULES))],
    )
    register_routes(app, accounts)
    await app._startup()
    http = AsyncTestClient(app, base_url="http://accounts.test", follow_redirects=False)
    admin = ServiceClient("http://x", secret=settings.internal_secret, issuer="studio", app=app)
    try:
        yield Harness(
            app=app, http=http, admin=admin, settings=settings, api=platform, accounts=accounts
        )
    finally:
        await admin.close()
        await http.aclose()
        await app._shutdown()
