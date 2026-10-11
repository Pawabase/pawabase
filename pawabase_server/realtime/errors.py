"""What can go wrong with a realtime request, apart from being refused (which is a :class:`PermissionError`)."""

from __future__ import annotations


class PayloadTooLarge(ValueError):
    """A publication or presence payload over its limit."""

    def __init__(self, size: int, limit: int, what: str = "payload") -> None:
        super().__init__(f"the {what} is {size} bytes; the limit is {limit}")
        self.size = size
        self.limit = limit
        self.what = what


class CapacityError(Exception):
    """A connection that would exceed what its environment or person may hold. ``scope`` is ``env`` or ``identity``."""

    def __init__(self, scope: str, limit: int) -> None:
        super().__init__(f"too many connections for this {scope} (limit {limit})")
        self.scope = scope
        self.limit = limit
