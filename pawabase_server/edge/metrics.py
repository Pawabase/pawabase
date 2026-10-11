"""Counters and latency for what the public entry point serves."""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from typing import Any

BUCKETS_MS = (25, 50, 100, 250, 500, 1000, 2500, 5000)


def new_stats() -> dict[str, int]:
    return {
        "proxied": 0,
        "rejected_keys": 0,
        "rate_limited": 0,
        "websockets": 0,
        "quota_rejected": 0,
        "connections_rejected": 0,
        "refused": 0,
        "bad_requests": 0,
    }


class Latency:
    """Time to handle a request, by route group: count, mean, max and a histogram."""

    def __init__(self) -> None:
        self.count: Counter[str] = Counter()
        self.total_ms: Counter[str] = Counter()
        self.max_ms: dict[str, float] = {}
        self.buckets: dict[str, list[int]] = {}
        self.statuses: dict[str, Counter[str]] = {}

    def record(self, service: str, milliseconds: float, status: int) -> None:
        self.count[service] += 1
        self.total_ms[service] += milliseconds
        self.max_ms[service] = max(self.max_ms.get(service, 0.0), milliseconds)
        buckets = self.buckets.setdefault(service, [0] * (len(BUCKETS_MS) + 1))
        buckets[bisect_left(BUCKETS_MS, milliseconds)] += 1
        self.statuses.setdefault(service, Counter())[f"{status // 100}xx"] += 1

    def snapshot(self) -> dict[str, Any]:
        report: dict[str, Any] = {}
        for service, count in self.count.items():
            report[service] = {
                "requests": count,
                "mean_ms": round(self.total_ms[service] / count, 1),
                "max_ms": round(self.max_ms[service], 1),
                "histogram": dict(
                    zip(
                        [*(f"<={b}" for b in BUCKETS_MS), ">5000"],
                        self.buckets[service],
                        strict=True,
                    )
                ),
                "statuses": dict(self.statuses[service]),
            }
        return report
