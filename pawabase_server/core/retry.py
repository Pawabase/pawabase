"""One retry policy for everything the platform retries: jobs, event processing, webhook deliveries (and, later, customer queues).

A policy is how many attempts, how long to wait before each retry, and how much to randomise that wait. It is data, so the same policy
drives the retry and is shown to people (Studio, the API) without the two drifting apart.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RetryPolicy:
    """``max_attempts`` in total (1 = no retry). Waits follow ``schedule`` when given, else ``base`` doubling per attempt up to
    ``max_delay``; each wait is spread by ``jitter`` either way so that many failures do not come back at the same second."""

    max_attempts: int = 1
    base: float = 1.0
    max_delay: float = 3600.0
    jitter: float = 0.2
    schedule: tuple[float, ...] = field(default=())

    def nominal(self, attempt: int) -> float:
        """The wait before attempt ``attempt + 1``, without jitter."""
        if self.schedule:
            return float(self.schedule[min(attempt, len(self.schedule)) - 1])
        return min(self.base * (2 ** (attempt - 1)), self.max_delay)

    def delay(self, attempt: int) -> float:
        wait = self.nominal(attempt)
        return wait * random.uniform(1 - self.jitter, 1 + self.jitter) if self.jitter else wait

    def with_attempts(self, max_attempts: int) -> RetryPolicy:
        return RetryPolicy(
            max(1, max_attempts), self.base, self.max_delay, self.jitter, self.schedule
        )

    def describe(self) -> dict[str, Any]:
        """What the policy does, for people: the attempts and the wait before each retry."""
        waits = [round(self.nominal(n), 1) for n in range(1, self.max_attempts)]
        return {
            "max_attempts": self.max_attempts,
            "waits_seconds": waits,
            "total_seconds": round(sum(waits), 1),
            "jitter": self.jitter,
        }
