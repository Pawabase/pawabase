import os
from dataclasses import dataclass, field
from typing import Any

import pytest

os.environ.setdefault("SILLO_ENV_FILE", "")

PASSWORD = "Sup3r-secret!pass"
#: What the stand-in server signs the Explorer's application-user tokens with.
MASTER = "studio-test-jwt-master-secret-0123456789"


@dataclass
class FakeAkountz:
    """Signs an application user in with a real token, for the Explorer."""

    master: str
    calls: list[tuple[str, Any]] = field(default_factory=list)

    async def request(self, method: str, path: str, *, json: Any = None, **kwargs: Any) -> Any:
        from pawabase_server.core.clients import ServiceError
        from pawabase_server.core.tokens import issue_user_token

        self.calls.append((f"{method} {path}", json))
        self.last_kwargs = kwargs
        if path == "/auth/v1/token":
            if json.get("password") != PASSWORD:
                raise ServiceError(400, {"detail": "invalid email or password"}, service="akountz")
            token = issue_user_token(
                self.master,
                env=kwargs["context"].env,
                user_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
                jti="j1",
                session_id="s1",
                email=json["email"],
            )
            return {"access_token": token, "refresh_token": "r1", "token_type": "bearer"}
        if path.startswith("/admin/v1/"):
            return {"data": [{"id": "01ARZ3NDEKTSV4RRFFQ69G5FAV", "email": "u@example.com"}]}
        raise AssertionError(f"unexpected {method} {path}")

    async def post(self, path: str, **kwargs: Any) -> Any:
        return await self.request("POST", path, **kwargs)

    async def close(self) -> None:
        pass


ENVIRONMENTS = [
    {"name": "main", "is_default": True, "version": 3},
    {"name": "staging", "is_default": False, "version": 1},
]


@dataclass
class FakeApi:
    calls: list[tuple[str, str, Any, Any]] = field(default_factory=list)
    environments: list[dict[str, Any]] = field(
        default_factory=lambda: [dict(e) for e in ENVIRONMENTS]
    )

    async def request(
        self, method: str, path: str, *, json: Any = None, params: Any = None, **kwargs: Any
    ) -> Any:
        from pawabase_server.core.clients import ServiceError

        self.calls.append((method, path, json, params))
        self.last_kwargs = kwargs
        if path == "/platform/v1/runtime":
            return {"name": "Shop", "app_env": "testing", "environments": self.environments}
        if path == "/platform/v1/audit":
            return {"data": [{"actor": "studio", "action": "environment.created", "env": "main"}]}
        if path == "/platform/v1/blocks":
            return {"data": [{"name": "trigger.http"}]}
        if path == "/openapi.json":
            return {
                "openapi": "3.1.0",
                "info": {"title": "Pawabase runtime", "version": "1"},
                "paths": {
                    "/auth/v1/user": {
                        "get": {
                            "operationId": "auth.user",
                            "summary": "Signed-in user",
                            "responses": {"200": {"description": "User"}},
                        }
                    },
                    "/platform/v1/envs/{env}/events": {
                        "post": {
                            "operationId": "events.emit",
                            "tags": ["events"],
                            "summary": "Emit an event",
                            "parameters": [
                                {
                                    "name": "env",
                                    "in": "path",
                                    "required": True,
                                    "schema": {"type": "string"},
                                }
                            ],
                            "responses": {"202": {"description": "Accepted"}},
                        }
                    },
                    "/internal/v1/secrets": {
                        "get": {
                            "operationId": "internal.secrets",
                            "responses": {"200": {"description": "Internal"}},
                        }
                    },
                },
            }
        env = path.removeprefix("/platform/v1/envs/").split("/")[0]
        if path.startswith("/platform/v1/envs/") and env not in {
            e["name"] for e in self.environments
        }:
            raise ServiceError(404, {"detail": f"no environment {env!r}"}, service="api")
        if path == "/platform/v1/envs/main/overview":
            return {"resources": 2, "version": 3}
        if path == "/platform/v1/envs/main/openapi":
            return {"openapi": "3.1.0", "info": {"title": "Shop API"}, "paths": {}}
        if path == "/platform/v1/envs/main/schemas" and method == "POST":
            if not json.get("name"):
                raise ServiceError(422, {"detail": "name is required"}, service="api")
            return {"id": "01ARZ3NDEKTSV4RRFFQ69G5FAV", **json}
        return {"data": []}

    async def get(self, path: str, **kwargs: Any) -> Any:
        if path == "/v1/status":
            return {
                "ok": True,
                "services": {name: {"ok": True} for name in ("api", "auth", "realtime")},
            }
        return {"status": "ok"}

    async def close(self) -> None:
        pass

    async def request_raw(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Any = None,
        context: Any = None,
        headers: Any = None,
        content: bytes | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.raw_call = {
            "method": method,
            "path": path,
            "json": json,
            "params": params,
            "context": context,
            "headers": headers,
            "content": content,
        }
        if path.startswith("/storage/v1/object/"):
            return {
                "status": 200,
                "headers": {"content-type": "image/png", "etag": '"abc"'},
                "body": None,
                "content": b"\x89PNG\r\n\x1a\n",
            }
        return {
            "status": 201,
            "headers": {"content-type": "application/json", "set-cookie": "secret=bad"},
            "body": {"id": 7, **(json or {})},
            "content": b'{"id": 7}',
        }


class FakeCore(FakeApi):
    """The server Studio manages: the management API, identity and realtime answer through the one client."""

    identity: Any = None

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        if path.startswith(("/auth/v1/", "/admin/v1/")):
            self.calls.append((method, path, kwargs.get("json"), kwargs.get("params")))
            self.last_kwargs = kwargs
            return await self.identity.request(method, path, **kwargs)
        return await super().request(method, path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> Any:
        return await self.request("POST", path, **kwargs)


def fake_core(settings: Any) -> FakeCore:
    core = FakeCore()
    core.identity = FakeAkountz(master=MASTER)
    return core


@dataclass
class Studio:
    app: Any
    http: Any
    #: One stand-in for the server; ``api``, ``accounts`` and ``angula`` are the same object, kept so tests read as before.
    api: FakeCore
    accounts: FakeAkountz
    angula: FakeCore
    settings: Any

    async def csrf(self) -> dict[str, str]:
        """The double-submit token: a GET sets the cookie, a mutation echoes it."""
        if not self.http.cookies.get("XSRF-TOKEN"):
            await self.http.get("/health")
            await self.http.get("/", headers={"X-Inertia": "true"})
        return {"X-XSRF-TOKEN": self.http.cookies.get("XSRF-TOKEN") or ""}


@pytest.fixture
async def studio(tmp_path):
    from sillo.testclient import AsyncTestClient

    from studio.app import create_studio
    from studio.config import StudioSettings

    dist = tmp_path / "frontend" / "dist"
    (dist / ".vite").mkdir(parents=True)
    (dist / "assets").mkdir()
    (dist / ".vite" / "manifest.json").write_text(
        '{"src/main.jsx": {"file": "assets/main-abc.js", "css": ["assets/main-abc.css"]}}'
    )
    (dist / "assets" / "main-abc.js").write_text("console.log('studio')")
    settings = StudioSettings(
        _env_file=None,
        app_env="testing",
        frontend_dir=str(tmp_path / "frontend"),
        project_name="Shop",
    )
    core = fake_core(settings)
    app = create_studio(settings, client=core)
    await app._startup()
    http = AsyncTestClient(app, base_url="http://studio.test", follow_redirects=False)
    try:
        yield Studio(
            app=app, http=http, api=core, accounts=core.identity, angula=core, settings=settings
        )
    finally:
        await http.aclose()
        await app._shutdown()
