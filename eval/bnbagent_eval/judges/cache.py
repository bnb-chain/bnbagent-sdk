"""Judge-response cache: replays of an evaluation can hit the cache.

Keys are independent of the fence nonce: provider, model, params, template hash, unfenced
inputs and sample index. ``EVAL_JUDGE_CACHE=memory`` or ``sqlite:///path/to/judge.db``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Protocol

from ..hashing import keccak_hex


class JudgeCache(Protocol):
    name: str

    def get(self, key: str) -> dict[str, Any] | None: ...

    def set(self, key: str, value: dict[str, Any]) -> None: ...


class MemoryCache:
    name = "cache:memory"

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        return self._data.get(key)

    def set(self, key: str, value: dict[str, Any]) -> None:
        self._data[key] = value


class SqliteCache:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.name = "cache:" + keccak_hex(str(self.path))[2:12]
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS judge_cache (k TEXT PRIMARY KEY, v TEXT)")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT v FROM judge_cache WHERE k = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set(self, key: str, value: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO judge_cache (k, v) VALUES (?, ?)", (key, json.dumps(value))
            )


def from_url(url: str | None) -> JudgeCache | None:
    if not url:
        return None
    if url == "memory":
        return MemoryCache()
    if url.startswith("sqlite:///"):
        return SqliteCache(url[len("sqlite:///") :])
    raise ValueError(f"unsupported judge cache URL {url!r} (use 'memory' or 'sqlite:///path')")
