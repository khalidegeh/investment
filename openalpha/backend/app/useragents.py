"""User agent selection.

Default is one honest, identifying agent. Browser-style rotation is opt-in and
may breach a site's terms; it is never used for SEC EDGAR.
"""
from __future__ import annotations

import random
from collections.abc import Sequence

BROWSER_AGENTS: tuple[str, ...] = (
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.4 Safari/605.1.15"
    ),
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
    ),
)


def identifying_agent(contact: str | None) -> str:
    """Agent string naming the tool and a contact address (SEC fair-access policy)."""
    if not contact or "@" not in contact:
        raise ValueError("a contact email is required (set OPENALPHA_CONTACT)")
    return f"OpenAlpha/0.1 (personal research; {contact.strip()})"


class UserAgentPool:
    def __init__(
        self,
        agents: Sequence[str],
        rotate: bool = False,
        rng: random.Random | None = None,
    ):
        if not agents:
            raise ValueError("need at least one user agent")
        self._agents = tuple(agents)
        self._rotate = rotate
        self._rng = rng or random.Random()

    def next(self) -> str:
        return self._rng.choice(self._agents) if self._rotate else self._agents[0]
