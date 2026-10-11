"""Reading a request: its headers, its API key, the environment it names and the query that goes upstream."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode

KEY_HEADERS = ("apikey", "x-api-key")
KEY_SHAPE = re.compile(r"^[\x21-\x7e]{1,512}$")
REQUEST_ID = re.compile(r"^[A-Za-z0-9._\-]{8,64}$")


def header_map(scope: dict[str, Any]) -> dict[str, str]:
    """Headers by lower-cased name; when a name repeats the last value wins, as the servers in front of us treat it."""
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}


def query_pairs(scope: dict[str, Any]) -> list[tuple[str, str]]:
    return parse_qsl(scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True)


@dataclass
class Request:
    """What the gateway needs to know about one request, worked out once."""

    scope: dict[str, Any]
    headers: dict[str, str]
    client_ip: str
    #: Whether the connection came from a proxy the operator trusts, so its forwarding headers can be believed.
    via_proxy: bool = False
    query: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def of(cls, scope: dict[str, Any], client_ip: str, *, via_proxy: bool = False) -> Request:
        return cls(scope, header_map(scope), client_ip, via_proxy, query_pairs(scope))

    @property
    def method(self) -> str:
        return self.scope.get("method", "GET").upper()

    @property
    def path(self) -> str:
        return self.scope.get("path", "")

    @property
    def origin(self) -> str | None:
        return self.headers.get("origin")

    @property
    def host(self) -> str:
        return self.headers.get("host", "")

    @property
    def user_agent(self) -> str:
        return self.headers.get("user-agent", "")

    def api_key(self) -> str | None:
        """The key the caller sent: a header, or ``?apikey=`` for browser navigations and WebSockets."""
        for name in KEY_HEADERS:
            if self.headers.get(name):
                return self.headers[name]
        for name, value in self.query:
            if name == "apikey" and value:
                return value
        return None

    def environment_hint(self) -> str | None:
        """The environment the caller says the key is for, if it says: optional, since a key names its own."""
        env = self.headers.get("x-environment")
        for name, value in self.query:
            if name == "environment" and value:
                env = value
        return env

    def upstream_query(self) -> str:
        """The query string sent on: everything except the key itself. The environment hint stays, since upstreams may read it."""
        return urlencode([(k, v) for k, v in self.query if k != "apikey"])

    def request_id(self, minted: str | None) -> str | None:
        """The caller's ``X-Request-ID`` when it is well-formed, else the one the framework minted. A caller cannot inject odd bytes."""
        supplied = self.headers.get("x-request-id")
        if supplied and REQUEST_ID.match(supplied):
            return supplied
        return minted


def plausible_key(raw: str) -> bool:
    """Whether *raw* could be an API key at all. Rejects empty, over-long and non-printable values without asking the API."""
    return bool(KEY_SHAPE.match(raw))
