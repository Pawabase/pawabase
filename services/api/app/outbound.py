"""Outbound HTTP for flows and webhook delivery.

Sillo's :class:`~sillo.http.client.HTTPClient` is used for service-to-service
calls. Outbound calls to arbitrary third parties need the exact status code
of every response and a ceiling on how much body is read, and Sillo's client
does not report success statuses (it returns the decoded body). So these calls
use ``httpx`` (the library Sillo's client wraps) directly, with Sillo's retry
helper for backoff and jitter.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

import httpx
from sillo.helpers.retry import async_retry

MAX_BODY = 256 * 1024
RETRYABLE_STATUSES = {408, 425, 429, 500, 502, 503, 504}


class OutboundRefused(ValueError):
    """The target is not allowed (bad scheme, or a private address)."""


class _Retryable(Exception):
    def __init__(self, response: dict[str, Any]) -> None:
        super().__init__(f"retryable status {response['status']}")
        self.response = response


def check_target(url: str, *, allow_private: bool = False) -> None:
    """Refuse non-HTTP URLs, and private or loopback targets unless allowed.

    Flows and webhooks are configured by developers but can be pointed
    anywhere. Refusing internal addresses keeps them from reaching the
    platform's own services or the cloud metadata endpoint.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise OutboundRefused("only http and https URLs are allowed")
    if allow_private:
        return
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise OutboundRefused(f"cannot resolve {parsed.hostname}") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_multicast:
            raise OutboundRefused(f"{parsed.hostname} resolves to a private address")


async def request_once(client: httpx.AsyncClient, method: str, url: str, *, headers: Mapping[str, str] | None = None, json_body: Any = None, content: bytes | None = None, params: Mapping[str, Any] | None = None, timeout: float = 30.0) -> dict[str, Any]:
    async with client.stream(method.upper(), url, headers=dict(headers or {}), json=json_body if content is None else None, content=content, params=params, timeout=timeout) as response:
        body = b""
        async for chunk in response.aiter_bytes():
            body += chunk
            if len(body) > MAX_BODY:
                body = body[:MAX_BODY]
                break
    text = body.decode("utf-8", "replace")
    try:
        parsed: Any = response.json() if text and "json" in response.headers.get("content-type", "") else text
    except ValueError:
        parsed = text
    return {"status": response.status_code, "headers": dict(response.headers), "body": parsed}


async def request_with_retries(client: httpx.AsyncClient, method: str, url: str, *, headers=None, json_body=None, content=None, params=None, timeout: float = 30.0, retries: int = 0, allow_private: bool = False) -> dict[str, Any]:
    """One request, retried with Sillo's backoff on transient failures."""
    check_target(url, allow_private=allow_private)

    async def attempt() -> dict[str, Any]:
        response = await request_once(client, method, url, headers=headers, json_body=json_body, content=content, params=params, timeout=timeout)
        if response["status"] in RETRYABLE_STATUSES and retries:
            raise _Retryable(response)
        return response

    if not retries:
        return await attempt()
    try:
        return await async_retry(attempt, max_attempts=retries + 1, base_delay=0.5, max_delay=10.0, retryable_exceptions=(_Retryable, httpx.TransportError))
    except Exception as exc:
        last = getattr(exc, "__cause__", None) or exc
        if isinstance(last, _Retryable):
            return last.response
        response = getattr(exc, "response", None)
        if isinstance(response, dict):
            return response
        raise
