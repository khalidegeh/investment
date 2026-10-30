"""Shared test doubles: a fake clock whose sleep advances it, and a scripted transport."""
from __future__ import annotations

from collections.abc import Callable

import httpx


class FakeTime:
    def __init__(self, start: float = 1_000_000.0):
        self.now = start
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class Script:
    """Transport that replays queued responses (or exceptions) and counts calls."""

    def __init__(self, steps: list[httpx.Response | Exception]):
        self.steps = list(steps)
        self.calls: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request)
            step = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
            if isinstance(step, Exception):
                raise step
            return step

        return httpx.MockTransport(handler)


def route(table: dict[str, Callable[[httpx.Request], httpx.Response]]) -> httpx.MockTransport:
    """Transport that dispatches on URL substring."""

    def handler(request: httpx.Request) -> httpx.Response:
        for needle, fn in table.items():
            if needle in str(request.url):
                return fn(request)
        return httpx.Response(404)

    return httpx.MockTransport(handler)
