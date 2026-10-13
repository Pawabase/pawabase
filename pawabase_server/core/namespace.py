"""A prefix for everything a runtime puts in a shared Redis.

Without one, two runtimes on the same Redis share the platform event channel, the realtime fan-out channel, the cache and the rate-limit
counters, and would hear each other. ``PAWABASE_NAMESPACE`` (set once per runtime) puts a prefix on all of them. Empty keeps the names
installations have always had.
"""

from __future__ import annotations

import re

_prefix = ""


def configure(value: str) -> None:
    """Set the prefix for this process. Called once, when the platform is built."""
    global _prefix
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "-", (value or "").strip())[:40].strip(":")
    _prefix = f"{cleaned}:" if cleaned else ""


def scoped(name: str) -> str:
    return f"{_prefix}{name}"
