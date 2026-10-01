"""What blocks may call on.

Blocks never import a service. They reach platform capabilities through a
:class:`Runtime` the host service provides: the API gives its real
implementation, and tests give a fake. Each method maps onto a Sillo capability
in the API (cache, events, queue, storage, mail, HTTP client…).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any, Protocol, runtime_checkable


class NotAvailable(RuntimeError):
    """The host service does not provide this capability."""


@runtime_checkable
class Runtime(Protocol):
    """Capabilities available to blocks and functions."""

    # data
    async def resource_list(
        self,
        resource: str,
        *,
        filters: Mapping[str, Any] | None = None,
        sort: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]: ...
    async def resource_get(self, resource: str, record_id: Any) -> dict[str, Any] | None: ...
    async def resource_create(self, resource: str, data: Mapping[str, Any]) -> dict[str, Any]: ...
    async def resource_update(
        self, resource: str, record_id: Any, data: Mapping[str, Any]
    ) -> dict[str, Any] | None: ...
    async def resource_delete(self, resource: str, record_id: Any) -> bool: ...
    async def db_query(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]: ...
    async def db_transaction(self, operations: list[Mapping[str, Any]]) -> list[Any]: ...

    # cache
    async def cache_get(self, key: str) -> Any: ...
    async def cache_set(
        self, key: str, value: Any, ttl: int | None = None, tags: list[str] | None = None
    ) -> None: ...
    async def cache_delete(self, key: str) -> bool: ...
    async def cache_invalidate(self, tags: list[str]) -> int: ...

    # events, queues, realtime
    async def emit(self, name: str, payload: Any) -> str: ...
    async def dispatch_flow(
        self, flow: str, input: Any, *, delay: int = 0, queue: str | None = None
    ) -> str: ...
    async def call_flow(self, flow: str, input: Any) -> Any: ...
    async def dispatch_function(
        self, function: str, input: Any, *, delay: int = 0, queue: str | None = None
    ) -> str: ...
    async def publish(self, channel: str, event: str, payload: Any) -> dict[str, Any]: ...

    # storage and mail
    async def storage_put(
        self, bucket: str, key: str, content: bytes, content_type: str = ""
    ) -> dict[str, Any]: ...
    async def storage_read(self, bucket: str, key: str, limit: int = 1_048_576) -> bytes: ...
    async def storage_signed_url(
        self, bucket: str, key: str, method: str = "GET", expires_in: int = 300
    ) -> str: ...
    async def storage_delete(self, bucket: str, key: str) -> bool: ...
    async def send_mail(
        self,
        to: list[str],
        subject: str,
        *,
        text: str | None = None,
        html: str | None = None,
        template: str | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    # outbound
    async def http_request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        timeout: float = 30.0,
        retries: int = 0,
    ) -> dict[str, Any]: ...
    async def webhook_send(self, event: str, payload: Any) -> int: ...

    # platform
    async def secret(self, name: str) -> str | None: ...
    async def call_function(self, name: str, input: Any) -> Any: ...
    async def identity_user(self, user_id: str) -> dict[str, Any] | None: ...
    async def check_policy(self, ref: Any, context: Mapping[str, Any]) -> bool: ...
    async def log(self, level: str, message: str, data: Any = None) -> None: ...
    async def metric(
        self, name: str, value: float = 1.0, tags: Mapping[str, str] | None = None
    ) -> None: ...


class BaseRuntime:
    """A runtime where every capability is missing.

    Hosts subclass it and override what they provide, so a block that uses
    something unavailable fails with a clear message rather than an
    ``AttributeError``.
    """

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        async def missing(*args: Any, **kwargs: Any) -> Any:
            raise NotAvailable(f"{name} is not available in this runtime")

        return missing

    async def stream(self) -> AsyncIterator[bytes]:  # pragma: no cover - typing aid
        raise NotAvailable("stream")
        yield b""
