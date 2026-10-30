"""Exception hierarchy. Callers catch OpenAlphaError to handle any engine failure."""
from __future__ import annotations


class OpenAlphaError(Exception):
    """Base class for all engine errors."""


class FetchError(OpenAlphaError):
    """An HTTP request failed in a way retrying will not fix."""

    def __init__(self, message: str, status: int | None = None, url: str = ""):
        super().__init__(message)
        self.status = status
        self.url = url


class RetriesExhausted(FetchError):
    """Every retry attempt failed."""


class DataError(OpenAlphaError):
    """A response arrived but its content was missing, malformed or unusable."""


class InvalidSymbol(OpenAlphaError, ValueError):
    """A ticker failed validation."""
