"""Exponential backoff with jitter."""
from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    factor: float = 2.0
    base_delay: float = 1.0
    max_delay: float = 30.0
    max_retries: int = 5
    retry_statuses: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})

    def delay(
        self, attempt: int, rng: random.Random, retry_after: float | None = None
    ) -> float:
        """Seconds to wait before retry number ``attempt + 1`` (attempt is 0-based).

        Equal jitter: half the exponential ceiling is guaranteed, the other half
        is random. That keeps clients from retrying in lockstep without letting
        a lucky draw collapse the backoff to zero. A server ``Retry-After``
        raises the floor but never past ``max_delay``.
        """
        ceiling = min(self.max_delay, self.base_delay * self.factor**attempt)
        wait = ceiling / 2.0 + rng.uniform(0.0, ceiling / 2.0)
        if retry_after is not None and retry_after > 0:
            wait = max(wait, min(retry_after, self.max_delay))
        return wait


def parse_retry_after(value: str | None) -> float | None:
    """Parse a delta-seconds Retry-After header. HTTP-date values are ignored."""
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None
