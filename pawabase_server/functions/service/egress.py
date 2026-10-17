"""A function's ``fetch()``, made by the runtime.

In ``guarded`` network mode a worker has no sockets (seccomp refuses ``socket()``), so ``fetch`` in an HTTP function is a message to the
runtime, which makes the request here and streams the answer back. That puts every outbound request through one place that

* refuses private, loopback, link-local, carrier-grade-NAT, multicast and reserved addresses (unless the operator names a host in
  ``PAWABASE_OUTBOUND_ALLOW_HOSTS``) — checked **on the address actually connected to**: the name is resolved once, the address is
  checked, and the connection is made to that address (TLS still verifies the name), so a DNS answer that changes between a check and
  the connection (DNS rebinding) cannot reach an internal service;
* follows redirects itself, checking each hop the same way, and drops credentials when a redirect leaves the origin;
* counts requests and bytes per invocation against the installation's limits;
* removes hop-by-hop headers in both directions.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import AsyncIterator, Collection
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import httpcore
import httpx

#: Never forwarded: they describe one connection, not the request.
HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)
#: Dropped when a redirect goes to another origin, as browsers do.
CREDENTIALS = frozenset({"authorization", "cookie", "proxy-authorization", "apikey"})
REDIRECTS = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 20
MAX_REQUEST_BODY = 32 * 1024 * 1024
USER_AGENT = "Pawabase-Functions/1.0"


class EgressRefused(Exception):
    """The request may not be made. ``code`` says why (``private_address``, ``bad_url``, ``limit``, ``redirect``…)."""

    def __init__(self, message: str, code: str = "refused") -> None:
        super().__init__(message)
        self.code = code


def address_allowed(address: str) -> bool:
    """Whether a function may connect to *address*: a globally routable unicast address."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if isinstance(ip, ipaddress.IPv6Address) and ip.sixtofour is not None:
        ip = ip.sixtofour
    return ip.is_global and not ip.is_multicast


class GuardedBackend(httpcore.AsyncNetworkBackend):
    """Resolves a host once, checks the addresses, and connects to an allowed one: the check and the connection see the same address."""

    def __init__(self, allow_hosts: Collection[str] = (), *, inner: httpcore.AsyncNetworkBackend | None = None) -> None:
        self.allow_hosts = {host.lower() for host in allow_hosts if host}
        self.inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        trusted = host.lower() in self.allow_hosts
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise httpcore.ConnectError(f"cannot resolve {host}") from exc
        addresses: list[str] = []
        for info in infos:
            address = str(info[4][0])
            if address not in addresses:
                addresses.append(address)
        allowed = addresses if trusted else [a for a in addresses if address_allowed(a)]
        if not allowed:
            raise httpcore.ConnectError(
                f"{host} resolves to a private or reserved address, which functions may not reach"
            )
        last: Exception | None = None
        for address in allowed:
            try:
                return await self.inner.connect_tcp(
                    address, port, timeout=timeout, local_address=local_address, socket_options=socket_options
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last = exc
        raise last or httpcore.ConnectError(f"cannot connect to {host}")

    async def connect_unix_socket(self, path: str, timeout: float | None = None, socket_options: Any = None):
        raise httpcore.ConnectError("functions may not connect to local sockets")

    async def sleep(self, seconds: float) -> None:
        await self.inner.sleep(seconds)


@dataclass
class Budget:
    """What one invocation may still fetch."""

    requests: int
    bytes: int
    used_requests: int = 0
    used_bytes: int = 0

    def take_request(self) -> None:
        if self.used_requests >= self.requests:
            raise EgressRefused(
                f"this invocation already made {self.requests} outbound requests, the most it may", "limit"
            )
        self.used_requests += 1

    def take_bytes(self, count: int) -> None:
        self.used_bytes += count
        if self.used_bytes > self.bytes:
            raise EgressRefused(
                f"this invocation received more than {self.bytes} bytes from outbound requests", "limit"
            )


@dataclass
class EgressResponse:
    status: int
    reason: str
    headers: list[tuple[str, str]]
    url: str
    redirected: bool
    _response: httpx.Response
    _budget: Budget
    _closed: bool = field(default=False)

    async def chunks(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self._response.aiter_bytes():  # decoded: the function sees the body, not its transfer encoding
                if chunk:
                    self._budget.take_bytes(len(chunk))
                    yield chunk
        finally:
            await self.close()

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self._response.aclose()


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlparse(url)
    return parsed.scheme, (parsed.hostname or "").lower(), parsed.port


class Egress:
    """Outbound HTTP for functions: one connection pool for the whole runtime, guarded."""

    def __init__(self, *, allow_hosts: Collection[str] = (), max_connections: int = 256) -> None:
        backend = GuardedBackend(allow_hosts)
        transport = httpx.AsyncHTTPTransport()
        # The transport's own pool, rebuilt on the guarded backend: httpx has no other way to choose where a connection goes.
        transport._pool = httpcore.AsyncConnectionPool(  # noqa: SLF001
            ssl_context=httpx.create_ssl_context(),
            max_connections=max_connections,
            max_keepalive_connections=64,
            keepalive_expiry=30.0,
            network_backend=backend,
        )
        self.client = httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            timeout=httpx.Timeout(30.0, read=120.0),
            headers={},
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def open(
        self,
        method: str,
        url: str,
        headers: list[tuple[str, str]],
        body: bytes | None,
        *,
        redirect: str,
        budget: Budget,
        timeout: float | None = None,
    ) -> EgressResponse:
        """Make the request (following redirects as *redirect* says) and return the final response, unread."""
        method = method.upper()
        current = url
        sent = [(k, v) for k, v in headers if k.lower() not in HOP_BY_HOP]
        if not any(k.lower() == "user-agent" for k, _ in sent):
            sent.append(("user-agent", USER_AGENT))
        if body is not None and len(body) > MAX_REQUEST_BODY:
            raise EgressRefused(f"an outbound request body may be at most {MAX_REQUEST_BODY} bytes", "limit")
        redirected = False
        for _hop in range(MAX_REDIRECTS + 1):
            scheme, host, _ = _origin(current)
            if scheme not in ("http", "https") or not host:
                raise EgressRefused(f"only http and https URLs can be fetched, not {current!r}", "bad_url")
            budget.take_request()
            request = self.client.build_request(
                method, current, headers=sent, content=body if method not in ("GET", "HEAD") else None,
                timeout=httpx.Timeout(timeout or 30.0, read=timeout or 120.0),
            )
            try:
                response = await self.client.send(request, stream=True)
            except httpx.ConnectError as exc:
                raise EgressRefused(str(exc) or f"cannot connect to {host}", "connect") from exc
            except httpx.TimeoutException as exc:
                raise EgressRefused(f"{host} did not answer in time", "timeout") from exc
            except httpx.HTTPError as exc:
                raise EgressRefused(f"the request to {host} failed: {exc}", "network") from exc
            location = response.headers.get("location")
            if response.status_code not in REDIRECTS or not location or redirect == "manual":
                return EgressResponse(
                    status=response.status_code,
                    reason=response.reason_phrase,
                    # The body is handed over decoded, so its encoding and length headers would describe other bytes.
                    headers=[
                        (k.decode("latin-1"), v.decode("latin-1"))
                        for k, v in response.headers.raw
                        if k.decode("latin-1").lower() not in HOP_BY_HOP | {"content-encoding"}
                    ],
                    url=current,
                    redirected=redirected,
                    _response=response,
                    _budget=budget,
                )
            await response.aclose()
            if redirect == "error":
                raise EgressRefused(f"{current} redirected, and the request said redirect: 'error'", "redirect")
            target = urljoin(current, location)
            if _origin(target) != _origin(current):
                sent = [(k, v) for k, v in sent if k.lower() not in CREDENTIALS]
            if response.status_code == 303 or (response.status_code in (301, 302) and method == "POST"):
                method, body = ("GET" if method != "HEAD" else "HEAD"), None
                sent = [(k, v) for k, v in sent if k.lower() not in ("content-type",)]
            current, redirected = target, True
        raise EgressRefused(f"more than {MAX_REDIRECTS} redirects", "redirect")
