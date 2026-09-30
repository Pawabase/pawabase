import json
import os
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

os.environ.setdefault("SILLO_ENV_FILE", "")

KEYS = {
    "pk_anon": {
        "project": "shop",
        "env": "main",
        "role": "anon",
        "key_id": "k1",
        "scopes": [],
        "cors_origins": ["https://shop.example"],
    },
    "sk_service": {
        "project": "shop",
        "env": "main",
        "role": "service",
        "key_id": "k2",
        "scopes": ["*"],
    },
    "sk_restricted": {
        "project": "shop",
        "env": "main",
        "role": "service",
        "key_id": "k3",
        "scopes": ["*"],
        "allowed_ips": [],
        "allowed_routes": ["GET /rest/v1/*"],
    },
}


class Echo:
    """An upstream that answers with what it received."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.seen: list[dict[str, Any]] = []

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        record = {
            "service": self.name,
            "path": scope["path"],
            "query": scope["query_string"].decode(),
            "method": scope["method"],
            "headers": {k.decode(): v.decode() for k, v in scope["headers"]},
            "body": body.decode(),
        }
        self.seen.append(record)
        payload = json.dumps(record).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"x-upstream", self.name.encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})


@dataclass
class FakeApiClient:
    """Stands in for the API's key resolution endpoint."""

    calls: list[str] = field(default_factory=list)

    async def post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        from pawabase_kit.clients import ServiceError

        assert path == "/internal/v1/keys/resolve"
        self.calls.append(json["key"])
        if json["key"] not in KEYS:
            raise ServiceError(401, {"error": "invalid_api_key"})
        return KEYS[json["key"]]

    async def close(self) -> None:
        pass


@dataclass
class Gateway:
    app: Any
    http: Any
    upstreams: dict[str, Echo]
    api: FakeApiClient
    settings: Any


@pytest.fixture
def settings():
    from app.config import GatewaySettings

    return GatewaySettings(
        _env_file=None, app_env="testing", rate_limit=5, rate_window=60, max_body_bytes=1024
    )


@pytest.fixture
async def gateway(settings):
    from sillo.testclient import AsyncTestClient

    from app.bootstrap import create_app

    upstreams = {name: Echo(name) for name in ("api", "akountz", "angula")}
    clients = {
        name: httpx.AsyncClient(
            transport=httpx.ASGITransport(app=up, client=("10.0.0.9", 1234)),
            base_url=f"http://{name}",
        )
        for name, up in upstreams.items()
    }
    api = FakeApiClient()
    app = create_app(settings, clients=clients, api=api)
    await app._startup()
    http = AsyncTestClient(app, base_url="http://gateway.test")
    try:
        yield Gateway(app=app, http=http, upstreams=upstreams, api=api, settings=settings)
    finally:
        await http.aclose()
        await app._shutdown()
