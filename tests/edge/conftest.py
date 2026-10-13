import json
import os
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

os.environ.setdefault("SILLO_ENV_FILE", "")

KEYS = {
    "pk_anon": {
        "env": "main",
        "project_id": "project-main",
        "project_limits": {},
        "project_limits_version": 1,
        "role": "anon",
        "key_id": "k1",
        "scopes": [],
        "cors_origins": ["https://shop.example"],
    },
    "sk_service": {
        "env": "main",
        "project_id": "project-main",
        "project_limits": {},
        "project_limits_version": 1,
        "role": "service",
        "key_id": "k2",
        "scopes": ["*"],
    },
    "sk_restricted": {
        "env": "main",
        "project_id": "project-main",
        "project_limits": {},
        "project_limits_version": 1,
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
class FakeKeys:
    """Stands in for the key lookup: the keys in :data:`KEYS`, and a record of what was asked."""

    calls: list[str] = field(default_factory=list)

    async def lookup(self, raw: str, env: str | None = None) -> dict[str, Any]:
        from pawabase_server.edge.key_resolver import KeyRejected

        self.calls.append(raw)
        if raw not in KEYS:
            raise KeyRejected("invalid API key")
        return KEYS[raw]


@dataclass
class Gateway:
    app: Any
    http: Any
    #: Everything the application behind the edge received. Several names for one recorder, so a test can say what it expects.
    upstreams: dict[str, Echo]
    api: FakeKeys
    settings: Any

    @property
    def edge(self) -> Any:
        return self.app


@pytest.fixture
def settings():
    from pawabase_server.settings import Settings

    return Settings(
        _env_file=None, app_env="testing", rate_limit=5, rate_window=60, max_body_bytes=1024
    )


@pytest.fixture
async def gateway(settings):
    from pawabase_server.edge import Edge
    from pawabase_server.edge.key_resolver import KeyResolver

    inner = Echo("server")
    keys = FakeKeys()
    edge = Edge(inner, settings, KeyResolver(keys.lookup, ttl=30))
    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=edge, client=("203.0.113.5", 1234)),
        base_url="http://gateway.test",
    )
    try:
        yield Gateway(
            app=edge,
            http=http,
            upstreams={"api": inner, "server": inner},
            api=keys,
            settings=settings,
        )
    finally:
        await http.aclose()
        await edge.close()
