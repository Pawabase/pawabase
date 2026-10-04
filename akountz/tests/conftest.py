import os
from dataclasses import dataclass
from typing import Any

import pytest

os.environ.setdefault("SILLO_ENV_FILE", "")


class FakeApi:
    """Stands in for the Pawabase API: environment auth config, mail and events."""

    def __init__(self) -> None:
        self.auth: dict[str, Any] = {}
        self.mail: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []

    async def get(self, path: str, **kwargs: Any) -> Any:
        from pawabase_core.clients import ServiceError

        parts = path.strip("/").split("/")
        if parts[:3] == ["internal", "v1", "environments"] and parts[-1] == "auth":
            env = parts[3]
            if env not in ("development", "production"):
                raise ServiceError(404, {"detail": "no such environment"})
            return {
                "env": env,
                "project_name": "Acme",
                "auth": self.auth,
                "public_url": "http://gateway.test",
            }
        raise AssertionError(f"unexpected GET {path}")

    async def post(self, path: str, json: Any = None, **kwargs: Any) -> Any:
        if path == "/internal/v1/mail":
            self.mail.append(json)
            return {"job_id": "j"}
        if path == "/internal/v1/events":
            self.events.append(json)
            return {"event_id": "e"}
        raise AssertionError(f"unexpected POST {path}")

    async def close(self) -> None:
        pass

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
    api: FakeApi
    akountz: Any

    def headers(
        self, env: str = "development", token: str | None = None
    ) -> dict[str, str]:
        from pawabase_core.context import CONTEXT_HEADER, PlatformContext
        from pawabase_core.tokens import issue_context_token

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
    from sillo.testclient import AsyncTestClient

    from app.bootstrap import create_app
    from app.config import AkountzSettings
    from app.platform import Akountz
    from pawabase_core.clients import ServiceClient

    settings = AkountzSettings(
        _env_file=None,
        app_env="testing",
        database_url=f"sqlite://{tmp_path}/akountz.db",
        db_generate_schemas=True,
        public_url="http://gateway.test",
    )
    fake = FakeApi()
    akountz = Akountz(settings, api=fake)
    app = create_app(settings, akountz=akountz)
    await app._startup()
    http = AsyncTestClient(app, base_url="http://akountz.test", follow_redirects=False)
    admin = ServiceClient(
        "http://x", secret=settings.internal_secret, issuer="studio", audience="akountz", app=app
    )
    try:
        yield Harness(app=app, http=http, admin=admin, settings=settings, api=fake, akountz=akountz)
    finally:
        await admin.close()
        await http.aclose()
        await app._shutdown()
