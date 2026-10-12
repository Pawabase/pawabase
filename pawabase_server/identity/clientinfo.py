"""Who is calling: the address and user agent of a request."""

from __future__ import annotations

from typing import Any


def client_info(ctx: Any) -> tuple[str | None, str | None]:
    forwarded = ctx.headers.get("x-forwarded-for")
    ip = forwarded.split(",")[0].strip() if forwarded else (ctx.client.host if ctx.client else None)
    return ip, (ctx.headers.get("user-agent") or "")[:500]
