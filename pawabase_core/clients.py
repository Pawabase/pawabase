"""Calling another Pawabase service.

:class:`ServiceClient` wraps Sillo's :class:`~sillo.http.client.HTTPClient`
(connection pooling, timeouts, statistics) and adds what every internal call
needs: a fresh service token addressed to the callee, the platform context when
acting for an environment.

In tests, pass ``app=`` to route calls in-process through Sillo's
:class:`~sillo.testclient.AsyncTestClient` instead of the network.
"""

from __future__ import annotations

from typing import Any

from .auth import SERVICE_HEADER
from .context import CONTEXT_HEADER, PlatformContext
from .tokens import issue_context_token, issue_service_token


def _parse(body: Any) -> Any:
    import json

    if not body:
        return {}
    try:
        return json.loads(body)
    except (TypeError, ValueError):
        return {"detail": body}


class ServiceError(Exception):
    """The callee answered with an error status.

    Attributes:
        status: The HTTP status.
        body: The decoded error body.
    """

    def __init__(self, status: int, body: Any, *, service: str = "") -> None:
        detail = body.get("detail") if isinstance(body, dict) else body
        super().__init__(f"{service or 'service'} answered {status}: {detail}")
        self.status = status
        self.body = body
        self.service = service


class ServiceUnavailable(ServiceError):
    """The callee could not be reached."""

    def __init__(self, service: str, reason: str) -> None:
        super().__init__(503, {"detail": f"{service} is unavailable: {reason}"}, service=service)


class ServiceClient:
    """An authenticated client for one internal service.

    Args:
        base_url: The callee's internal URL.
        secret: The installation's internal secret.
        issuer: The calling service's name.
        audience: The callee's name (``api``, ``akountz``, ``angula``).
        timeout: Seconds per call.
        app: An ASGI app to call in-process instead (tests).
    """

    def __init__(
        self,
        base_url: str,
        *,
        secret: str,
        issuer: str,
        audience: str,
        timeout: float = 10.0,
        app: Any = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.secret = secret
        self.issuer = issuer
        self.audience = audience
        self.timeout = timeout
        self.app = app
        self._http: Any = None
        self._raw_http: Any = None

    async def _client(self) -> Any:
        if self._http is None:
            if self.app is not None:
                from sillo.testclient import AsyncTestClient

                self._http = AsyncTestClient(self.app, base_url="http://internal")
            else:
                from sillo.http.client import HTTPClient

                self._http = HTTPClient(
                    self.base_url,
                    default_timeout=self.timeout,
                    raise_for_status=True,
                    follow_redirects=False,
                )
                await self._http.start()
        return self._http

    def headers(
        self,
        *,
        context: PlatformContext | None = None,
        extra: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Headers authenticating one call."""
        headers = {
            SERVICE_HEADER: issue_service_token(
                self.secret, issuer=self.issuer, audience=self.audience
            )
        }
        if context is not None:
            headers[CONTEXT_HEADER] = issue_context_token(self.secret, context)
        if extra:
            headers.update(extra)
        return headers

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        context: PlatformContext | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Call the service and return the decoded body. Raises :class:`ServiceError`."""
        client = await self._client()
        all_headers = self.headers(context=context, extra=headers)
        kwargs: dict[str, Any] = {"headers": all_headers}
        if json is not None:
            kwargs["json"] = json
        if params:
            kwargs["params"] = {k: v for k, v in params.items() if v is not None}
        try:
            if self.app is not None:
                response = await client.request(method, path, **kwargs)
                return self._decode(response.status_code, response)
            return await client.request(method, path, **kwargs)
        except ServiceError:
            raise
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if status is not None:
                raise ServiceError(
                    status, _parse(getattr(exc, "response_body", None)), service=self.audience
                ) from exc
            if type(exc).__name__ in (
                "HTTPConnectionError",
                "HTTPTimeoutError",
                "ConnectError",
                "ConnectTimeout",
            ):
                raise ServiceUnavailable(self.audience, str(exc)) from exc
            raise

    async def request_raw(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        context: PlatformContext | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Call a service and preserve status and headers, including error responses.

        This is intended for operator tooling such as an API explorer. Normal
        service-to-service calls should use :meth:`request` and its exceptions.
        """
        kwargs: dict[str, Any] = {
            "headers": self.headers(context=context, extra=headers)
        }
        if json is not None:
            kwargs["json"] = json
        if params:
            kwargs["params"] = {key: value for key, value in params.items() if value is not None}
        if self.app is not None:
            client = await self._client()
            response = await client.request(method, path, **kwargs)
        else:
            if self._raw_http is None:
                from sillo.http.client import HTTPClient

                self._raw_http = HTTPClient(
                    self.base_url,
                    default_timeout=self.timeout,
                    raise_for_status=False,
                    follow_redirects=False,
                )
                await self._raw_http.start()
            response = await self._raw_http._send(method, path, **kwargs)
        try:
            body = response.json()
        except ValueError:
            body = response.text
        return {
            "status": response.status_code,
            "headers": dict(response.headers),
            "body": body,
        }

    def _decode(self, status: int, response: Any) -> Any:
        try:
            body = response.json()
        except ValueError:
            body = response.text
        if status >= 400:
            raise ServiceError(status, body, service=self.audience)
        return body

    async def get(self, path: str, **kwargs: Any) -> Any:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> Any:
        return await self.request("POST", path, **kwargs)

    async def put(self, path: str, **kwargs: Any) -> Any:
        return await self.request("PUT", path, **kwargs)

    async def patch(self, path: str, **kwargs: Any) -> Any:
        return await self.request("PATCH", path, **kwargs)

    async def delete(self, path: str, **kwargs: Any) -> Any:
        return await self.request("DELETE", path, **kwargs)

    async def close(self) -> None:
        if self._http is not None:
            if self.app is not None:
                await self._http.aclose()
            else:
                await self._http.stop()
            self._http = None
        if self._raw_http is not None:
            await self._raw_http.stop()
            self._raw_http = None
