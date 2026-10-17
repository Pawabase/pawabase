"""HTTP functions: what one is, and its ``config``, checked.

An HTTP function is a folder of a TypeScript deployment, ``functions/<name>/index.ts``, that answers requests to
``/functions/v1/<name>`` and every path below it, with any method, by returning a standard ``Response`` (see the kit's ``serve()``).
Its entry may export ``config``; this module turns that into an :class:`HttpFunctionConfig` the gateway and the runtime can rely on,
or into problems that stop the deployment from activating.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: ``auth`` values with a fixed meaning; anything else is a policy reference (a policy name or a condition).
AUTH_PUBLIC = "public"
AUTH_KEY = "key"
AUTH_USER = "user"
AUTH_SERVICE = "service"
NAMED_AUTH = (AUTH_PUBLIC, AUTH_KEY, AUTH_USER, AUTH_SERVICE)
METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
DEFAULT_TIMEOUT = 150
MAX_WARM = 4


@dataclass(frozen=True)
class Cors:
    origins: tuple[str, ...] | str = "*"
    methods: tuple[str, ...] = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
    headers: tuple[str, ...] = ("authorization", "apikey", "content-type", "x-client-info")
    expose: tuple[str, ...] = ()
    credentials: bool = False
    max_age: int = 600

    def allows(self, origin: str | None) -> str | None:
        """The ``Access-Control-Allow-Origin`` to send for *origin*, or ``None`` when it is not allowed."""
        if not origin:
            return None
        if self.origins == "*":
            return "*" if not self.credentials else origin
        return origin if origin in self.origins else None

    def response_headers(self, origin: str | None) -> list[tuple[str, str]]:
        allowed = self.allows(origin)
        if allowed is None:
            return []
        headers = [("access-control-allow-origin", allowed)]
        if allowed != "*":
            headers.append(("vary", "origin"))
        if self.credentials:
            headers.append(("access-control-allow-credentials", "true"))
        if self.expose:
            headers.append(("access-control-expose-headers", ", ".join(self.expose)))
        return headers

    def preflight_headers(self, origin: str | None, requested: str | None) -> list[tuple[str, str]]:
        headers = self.response_headers(origin)
        if not headers:
            return []
        # "*" in the allowed headers echoes what the browser asked for (a literal "*" is ignored when credentials are sent).
        allow = (requested or "*") if "*" in self.headers else ", ".join(self.headers)
        headers += [
            ("access-control-allow-methods", ", ".join(self.methods)),
            ("access-control-allow-headers", allow),
            ("access-control-max-age", str(self.max_age)),
        ]
        return headers


@dataclass(frozen=True)
class HttpFunctionConfig:
    description: str = ""
    #: ``public``, ``key``, ``user``, ``service``, or a policy reference.
    auth: Any = AUTH_KEY
    timeout: int = DEFAULT_TIMEOUT
    memory: int | None = None
    warm: int = 0
    concurrency: int | None = None
    cors: Cors | None = None
    methods: frozenset[str] = METHODS
    tags: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "auth": self.auth,
            "timeout": self.timeout,
            "memory": self.memory,
            "warm": self.warm,
            "concurrency": self.concurrency,
            "cors": None
            if self.cors is None
            else {
                "origins": self.cors.origins if isinstance(self.cors.origins, str) else list(self.cors.origins),
                "methods": list(self.cors.methods),
                "headers": list(self.cors.headers),
                "expose_headers": list(self.cors.expose),
                "credentials": self.cors.credentials,
                "max_age": self.cors.max_age,
            },
            "methods": sorted(self.methods),
            "tags": list(self.tags),
        }


def config_from_dict(data: dict[str, Any]) -> HttpFunctionConfig:
    """The inverse of :meth:`HttpFunctionConfig.as_dict`, for a config the server already checked (the runtime service receives it)."""
    cors = data.get("cors")
    return HttpFunctionConfig(
        description=str(data.get("description") or ""),
        auth=data.get("auth", AUTH_KEY),
        timeout=int(data.get("timeout") or DEFAULT_TIMEOUT),
        memory=int(data["memory"]) if data.get("memory") else None,
        warm=int(data.get("warm") or 0),
        concurrency=int(data["concurrency"]) if data.get("concurrency") else None,
        cors=None
        if not cors
        else Cors(
            origins=cors.get("origins", "*") if cors.get("origins", "*") == "*" else tuple(cors.get("origins") or ()),
            methods=tuple(cors.get("methods") or Cors.methods),
            headers=tuple(cors.get("headers") or Cors.headers),
            expose=tuple(cors.get("expose_headers") or ()),
            credentials=bool(cors.get("credentials")),
            max_age=int(cors.get("max_age") or 600),
        ),
        methods=frozenset(data.get("methods") or METHODS),
        tags=tuple(data.get("tags") or ()),
    )


@dataclass(frozen=True)
class HttpFunctionSpec:
    """One HTTP function of one deployment."""

    name: str
    entry: str
    config: HttpFunctionConfig
    #: ``pawabase`` (the kit's ``serve``) or ``deno`` (``Deno.serve``).
    style: str
    env: str
    branch: str
    deployment_id: str | None = None
    #: The deployment's vendored module cache (``None``: it imports nothing remote).
    vendor: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        return "http"

    def wire(self) -> dict[str, Any]:
        """What the runtime service needs to run this function."""
        return {
            "name": self.name,
            "entry": self.entry,
            "config": self.config.as_dict(),
            "style": self.style,
            "env": self.env,
            "branch": self.branch,
            "deployment_id": self.deployment_id,
            "vendor": self.vendor,
        }

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> HttpFunctionSpec:
        return cls(
            name=str(data["name"]),
            entry=str(data["entry"]),
            config=config_from_dict(data.get("config") or {}),
            style=str(data.get("style") or "pawabase"),
            env=str(data["env"]),
            branch=str(data.get("branch") or "main"),
            deployment_id=data.get("deployment_id"),
            vendor=data.get("vendor"),
        )

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": "http",
            "entry": self.entry,
            "file": self.entry,
            "line": None,
            "style": self.style,
            "env": self.env,
            "branch": self.branch,
            "deployment_id": self.deployment_id,
            "description": self.config.description,
            "policy": self.config.auth,
            "timeout": self.config.timeout,
            "config": self.config.as_dict(),
            "source": "deployment",
        }


def _strings(value: Any, what: str, problems: list[str]) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return tuple(value)
    problems.append(f"{what} must be a string or a list of strings")
    return ()


def _number(value: Any, what: str, problems: list[str], *, low: float, high: float) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        problems.append(f"{what} must be a number")
        return None
    if not low <= value <= high:
        problems.append(f"{what} must be between {low:g} and {high:g}")
        return None
    return value


def parse_cors(value: Any, problems: list[str]) -> Cors | None:
    if value in (None, False):
        return None
    if value is True:
        return Cors()
    if not isinstance(value, dict):
        problems.append("config.cors must be true, false or an object")
        return None
    origins = value.get("origins", "*")
    if origins != "*":
        origins = _strings(origins, "config.cors.origins", problems)
        for origin in origins:
            if not origin.startswith(("http://", "https://")):
                problems.append(f"config.cors.origins: {origin!r} must be a full origin such as https://app.example.com")
    methods = tuple(m.upper() for m in _strings(value.get("methods"), "config.cors.methods", problems)) or Cors.methods
    headers = tuple(h.lower() for h in _strings(value.get("headers"), "config.cors.headers", problems)) or Cors.headers
    expose = tuple(h.lower() for h in _strings(value.get("exposeHeaders") or value.get("expose_headers"), "config.cors.exposeHeaders", problems))
    credentials = bool(value.get("credentials", False))
    if credentials and origins == "*":
        problems.append("config.cors: credentials need a list of origins, not '*'")
    max_age = _number(value.get("maxAge", value.get("max_age")), "config.cors.maxAge", problems, low=0, high=86_400)
    return Cors(
        origins=origins,
        methods=methods,
        headers=headers,
        expose=expose,
        credentials=credentials,
        max_age=int(max_age if max_age is not None else 600),
    )


def parse_config(
    raw: Any, *, max_timeout: int, max_memory: int, max_concurrency: int
) -> tuple[HttpFunctionConfig, list[str]]:
    """``config`` as exported by the function, checked against the installation's limits. Returns the config and its problems."""
    problems: list[str] = []
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        return HttpFunctionConfig(), ["config must be an object"]
    known = {"description", "auth", "timeout", "memory", "warm", "concurrency", "cors", "methods", "tags", "verify_jwt", "verifyJwt"}
    for key in raw:
        if key not in known:
            problems.append(f"config.{key} is not a setting (known: {', '.join(sorted(known - {'verify_jwt', 'verifyJwt'}))})")
    auth: Any = raw.get("auth", AUTH_KEY)
    # Supabase's switch, accepted so a function moved from there keeps its meaning.
    verify = raw.get("verify_jwt", raw.get("verifyJwt"))
    if verify is not None and "auth" not in raw:
        auth = AUTH_USER if verify else AUTH_PUBLIC
    if not (isinstance(auth, str) and auth) and not isinstance(auth, dict):
        problems.append("config.auth must be 'public', 'key', 'user', 'service', a policy name or a condition")
        auth = AUTH_KEY
    timeout = _number(raw.get("timeout"), "config.timeout", problems, low=1, high=max_timeout)
    memory = _number(raw.get("memory"), "config.memory", problems, low=32, high=max_memory)
    warm = _number(raw.get("warm"), "config.warm", problems, low=0, high=MAX_WARM)
    concurrency = _number(raw.get("concurrency"), "config.concurrency", problems, low=1, high=max_concurrency)
    methods = frozenset(m.upper() for m in _strings(raw.get("methods"), "config.methods", problems)) or METHODS
    unknown = sorted(methods - METHODS)
    if unknown:
        problems.append(f"config.methods: {', '.join(unknown)} cannot be served")
    description = raw.get("description") or ""
    if not isinstance(description, str):
        problems.append("config.description must be text")
        description = ""
    config = HttpFunctionConfig(
        description=description[:500],
        auth=auth,
        timeout=int(timeout) if timeout is not None else min(DEFAULT_TIMEOUT, max_timeout),
        memory=int(memory) if memory is not None else None,
        warm=int(warm or 0),
        concurrency=int(concurrency) if concurrency is not None else None,
        cors=parse_cors(raw.get("cors"), problems),
        methods=methods - {"OPTIONS"} | ({"OPTIONS"} if "OPTIONS" in methods or raw.get("cors") else set()),
        tags=_strings(raw.get("tags"), "config.tags", problems),
    )
    return config, problems
