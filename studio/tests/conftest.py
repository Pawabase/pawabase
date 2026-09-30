import os
from dataclasses import dataclass, field
from typing import Any

import pytest

os.environ.setdefault("SILLO_ENV_FILE", "")

PASSWORD = "Sup3r-secret!pass"


@dataclass
class FakeAkountz:
    """Signs ``_platform`` operators in with real tokens."""

    master: str
    mfa: bool = False
    calls: list[tuple[str, Any]] = field(default_factory=list)

    def _tokens(
        self, *, project: str = "_platform", env: str = "main", email: str = "root@pawabase.dev"
    ) -> dict[str, Any]:
        from pawabase_kit.tokens import issue_user_token

        token = issue_user_token(
            self.master,
            project=project,
            env=env,
            user_id="1",
            jti="j1",
            session_id="s1",
            email=email,
            roles=["admin"] if project == "_platform" else ["customer"],
        )
        return {"access_token": token, "refresh_token": "r1", "token_type": "bearer"}

    async def request(self, method: str, path: str, *, json: Any = None, **kwargs: Any) -> Any:
        from pawabase_kit.clients import ServiceError

        self.calls.append((f"{method} {path}", json))
        if path == "/auth/v1/token":
            context = kwargs["context"]
            if context.project != "_platform":
                if json.get("password") != PASSWORD:
                    raise ServiceError(400, {"detail": "invalid email or password"}, service="akountz")
                return self._tokens(project=context.project, env=context.env, email=json["email"])
            if json.get("grant_type") == "mfa":
                if json.get("code") != "123456":
                    raise ServiceError(400, {"detail": "invalid code"}, service="akountz")
                return self._tokens()
            if json.get("grant_type") == "refresh_token":
                return self._tokens()
            if json.get("password") != PASSWORD:
                raise ServiceError(400, {"detail": "invalid email or password"}, service="akountz")
            if self.mfa:
                return {"mfa_required": True, "mfa_token": "challenge"}
            return self._tokens()
        if path == "/auth/v1/logout":
            return {}
        if path.startswith("/admin/v1/"):
            assert kwargs["operator"]["email"] == "root@pawabase.dev"
            return {"data": [{"id": 7, "email": "u@example.com"}]}
        raise AssertionError(f"unexpected {method} {path}")

    async def post(self, path: str, **kwargs: Any) -> Any:
        return await self.request("POST", path, **kwargs)

    async def close(self) -> None:
        pass


ORG = {"slug": "acme", "name": "Acme", "role": "owner", "projects": 1, "members": 1}


@dataclass
class FakeApi:
    calls: list[tuple[str, str, Any, Any]] = field(default_factory=list)
    orgs: list[dict[str, Any]] = field(default_factory=lambda: [dict(ORG)])
    #: Projects the operator may reach; anything else is another organization's.
    projects: tuple[str, ...] = ("shop",)

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Any = None,
        operator: Any = None,
        **kwargs: Any,
    ) -> Any:
        from pawabase_kit.clients import ServiceError

        if not path.startswith("/platform/v1/invitations/"):
            assert operator and operator["email"] == "root@pawabase.dev"
        self.calls.append((method, path, json, params))
        self.last_kwargs = kwargs
        if path == "/platform/v1/orgs" and method == "GET":
            return {"data": self.orgs}
        if path == "/platform/v1/orgs/acme":
            return next(o for o in self.orgs if o["slug"] == "acme")
        if path == "/platform/v1/orgs/acme/members":
            return {"data": [{"user_id": "1", "email": "root@pawabase.dev", "role": "owner"}]}
        if path == "/platform/v1/orgs/acme/invitations":
            return {"data": [{"id": 1, "email": "new@example.com", "role": "developer"}]}
        if path == "/platform/v1/invitations/good":
            return {"organization": "Acme", "slug": "acme", "email": "new@example.com", "role": "developer"}
        if path.startswith("/platform/v1/invitations/"):
            if path.endswith("/accept"):
                return {"slug": "acme", "role": "developer"}
            raise ServiceError(404, {"detail": "this invitation is no longer valid"}, service="api")
        ref = path.removeprefix("/platform/v1/projects/").split("/")[0]
        if path.startswith("/platform/v1/projects/") and ref not in self.projects and ref != "nope":
            raise ServiceError(404, {"detail": f"no project {ref!r}"}, service="api")
        if path == "/platform/v1/projects":
            return {"data": [{"ref": "shop", "name": "Shop", "org": "acme"}]}
        if path == "/platform/v1/overview":
            return {"projects": 1}
        if path == "/platform/v1/projects/shop":
            return {"ref": "shop", "name": "Shop", "org": "acme"}
        if path == "/platform/v1/projects/shop/envs":
            return {"data": [{"name": "main", "is_default": True}]}
        if path == "/platform/v1/projects/shop/envs/main/overview":
            return {"resources": 2}
        if path == "/platform/v1/blocks":
            return {"data": [{"name": "trigger.http"}]}
        if path.startswith("/platform/v1/projects/nope"):
            raise ServiceError(404, {"detail": "no project 'nope'"}, service="api")
        if path == "/platform/v1/projects/shop/envs/main/schemas" and method == "POST":
            if not json.get("name"):
                raise ServiceError(422, {"detail": "name is required"}, service="api")
            return {"id": 1, **json}
        return {"data": []}

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
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.raw_call = {
            "method": method,
            "path": path,
            "json": json,
            "params": params,
            "context": context,
            "headers": headers,
        }
        return {
            "status": 201,
            "headers": {"content-type": "application/json", "set-cookie": "secret=bad"},
            "body": {"id": 7, **(json or {})},
        }


@dataclass
class Studio:
    app: Any
    http: Any
    api: FakeApi
    akountz: FakeAkountz
    angula: FakeApi

    async def login(self) -> None:
        await self.http.get("/login")
        response = await self.http.post(
            "/login", json={"email": "root@pawabase.dev", "password": PASSWORD}, headers=self.csrf()
        )
        assert response.status_code in (302, 303), response.text
        assert response.headers["location"] == "/"

    def csrf(self) -> dict[str, str]:
        return {"X-XSRF-TOKEN": self.http.cookies.get("XSRF-TOKEN") or ""}


@pytest.fixture
async def studio(tmp_path):
    from sillo.testclient import AsyncTestClient

    from app.bootstrap import create_app
    from app.config import StudioSettings

    dist = tmp_path / "frontend" / "dist"
    (dist / ".vite").mkdir(parents=True)
    (dist / "assets").mkdir()
    (dist / ".vite" / "manifest.json").write_text(
        '{"src/main.jsx": {"file": "assets/main-abc.js", "css": ["assets/main-abc.css"]}}'
    )
    (dist / "assets" / "main-abc.js").write_text("console.log('studio')")
    settings = StudioSettings(
        _env_file=None, app_env="testing", frontend_dir=str(tmp_path / "frontend")
    )
    api = FakeApi()
    akountz = FakeAkountz(master=settings.jwt_master_secret)
    angula = FakeApi()
    app = create_app(settings, clients={"api": api, "akountz": akountz, "angula": angula})
    await app._startup()
    http = AsyncTestClient(app, base_url="http://studio.test", follow_redirects=False)
    try:
        yield Studio(app=app, http=http, api=api, akountz=akountz, angula=angula)
    finally:
        await http.aclose()
        await app._shutdown()
