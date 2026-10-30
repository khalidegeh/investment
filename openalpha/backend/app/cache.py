"""SQLite response cache. Identical requests inside the TTL never hit the network."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path


def make_key(method: str, url: str, params: Mapping[str, str]) -> str:
    """Stable key. The user agent is deliberately not part of it."""
    blob = json.dumps([method, url, sorted(params.items())], separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


@dataclass(frozen=True)
class CachedEntry:
    status: int
    body: bytes
    fetched_at: float


class SqliteCache:
    """Thread-safe (one lock) SQLite store. All SQL is parameterised."""

    def __init__(
        self,
        path: str | Path,
        default_ttl: float = 900.0,
        clock: Callable[[], float] = time.time,
    ):
        self._ttl = default_ttl
        self._clock = clock
        self._lock = threading.Lock()
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        with self._lock:
            if str(path) != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS http_cache ("
                "key TEXT PRIMARY KEY, fetched_at REAL NOT NULL, "
                "status INTEGER NOT NULL, body BLOB NOT NULL)"
            )
            self._conn.commit()

    def get(self, key: str, ttl: float | None = None) -> CachedEntry | None:
        limit = self._ttl if ttl is None else ttl
        with self._lock:
            row = self._conn.execute(
                "SELECT fetched_at, status, body FROM http_cache WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        fetched_at, status, body = row
        if self._clock() - fetched_at > limit:
            return None
        return CachedEntry(int(status), bytes(body), float(fetched_at))

    def put(self, key: str, status: int, body: bytes) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO http_cache (key, fetched_at, status, body) "
                "VALUES (?, ?, ?, ?)",
                (key, self._clock(), status, body),
            )
            self._conn.commit()

    def purge_older_than(self, seconds: float) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM http_cache WHERE fetched_at < ?", (self._clock() - seconds,)
            )
            self._conn.commit()
            return cur.rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()
