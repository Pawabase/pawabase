"""Reading what a client sent.

Everything a client can send is checked here once, so handlers work with typed fields instead of repeating ``dict.get`` and casts. A message
that cannot be understood raises :class:`ProtocolError` carrying the ``error`` name the client is sent back.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pawabase_server.realtime.protocol import codes

MAX_REF = 64
KINDS = frozenset({"ping", "auth", "subscribe", "unsubscribe", "publish", "presence", "history"})


class ProtocolError(Exception):
    """A message that can't be accepted. ``code`` is one of :mod:`pawabase_server.realtime.protocol.codes`."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


@dataclass
class Message:
    """A parsed client message. Fields a kind does not use keep their defaults."""

    kind: str
    ref: str | int | None = None
    channel: str = ""
    event: Any = None
    payload: Any = None
    meta: dict[str, Any] = field(default_factory=dict)
    presence: dict[str, Any] | None = None
    since: int | None = None
    limit: int | None = None
    token: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _int(value: Any, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ProtocolError(codes.BAD_REQUEST, f"{name} must be a number")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(codes.BAD_REQUEST, f"{name} must be a number") from exc


def _ref(value: Any) -> str | int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return value[:MAX_REF]
    raise ProtocolError(codes.BAD_REQUEST, "ref must be a string or a number")


def parse(raw: str, *, max_bytes: int) -> Message:
    """Parse one text frame. Raises :class:`ProtocolError`."""
    if len(raw.encode()) > max_bytes:
        raise ProtocolError(codes.MESSAGE_TOO_LARGE)
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ProtocolError(codes.INVALID_JSON) from exc
    if not isinstance(data, dict):
        raise ProtocolError(codes.INVALID_MESSAGE)
    kind = data.get("type")
    if not isinstance(kind, str):
        raise ProtocolError(codes.INVALID_MESSAGE)
    meta = data.get("meta")
    presence = data.get("presence")
    channel = data.get("channel")
    token = data.get("token")
    return Message(
        kind=kind,
        ref=_ref(data.get("ref")),
        channel=channel if isinstance(channel, str) else "",
        event=data.get("event"),
        payload=data.get("payload"),
        meta=dict(meta) if isinstance(meta, dict) else {},
        presence=dict(presence) if isinstance(presence, dict) else None,
        since=_int(data.get("since"), "since"),
        limit=_int(data.get("limit"), "limit"),
        token=token if isinstance(token, str) else None,
        raw=data,
    )
