"""Per-minute counters, for Studio's metrics view."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

from tortoise.expressions import F

from database.models import MetricCounter


def _window(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(second=0, microsecond=0)


def _tags(tags: Mapping[str, str]) -> str:
    return ",".join(f"{k}={v}" for k, v in sorted(tags.items()))[:500]


async def increment(project: str, env: str, name: str, value: float = 1.0, tags: Mapping[str, str] | None = None) -> None:
    window = _window()
    tag_text = _tags(tags or {})
    updated = await MetricCounter.filter(project=project, env=env, name=name, tags=tag_text, window=window).update(value=F("value") + value, count=F("count") + 1)
    if not updated:
        try:
            await MetricCounter.create(project=project, env=env, name=name, tags=tag_text, window=window, value=value, count=1)
        except Exception:  # a concurrent writer created it first
            await MetricCounter.filter(project=project, env=env, name=name, tags=tag_text, window=window).update(value=F("value") + value, count=F("count") + 1)
