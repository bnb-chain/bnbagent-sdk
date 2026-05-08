"""
SQLiteStorageProvider — custom StorageProvider backed by SQLite.

Demonstrates how to implement a custom storage backend for the bnbagent SDK.
Satisfies the full StorageProvider ABC: upload / download / exists + from_env().

Usage in service.py (swap the backend line):
    from .sqlite_provider import SQLiteStorageProvider
    _storage = SQLiteStorageProvider.from_env()

NOTE: This provider returns public HTTP URLs of the form
``{public_base_url}/{key}``. For the deliverable to be reachable by voter/
client you must expose an HTTP route that queries SQLite by key and returns
the JSON. This file does NOT add that route — it only proves the ABC contract
is satisfiable. See examples/storage-demos/sqlite_demo.py for a standalone
walkthrough.

Required env when using from_env():
    STORAGE_SQLITE_DB_PATH    path to SQLite database file (default: ./.agent-data/storage.db)
    STORAGE_SQLITE_PUBLIC_URL agent's public URL prefix for deliverables
                              (default: http://localhost:8003/storage)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import aiosqlite

from bnbagent.storage import StorageProvider
from bnbagent.exceptions import StorageError

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS deliverables (
    key  TEXT PRIMARY KEY,
    data TEXT NOT NULL
)
"""


class SQLiteStorageProvider(StorageProvider):
    """StorageProvider that persists deliverables in a local SQLite database.

    Args:
        db_path:        Path to the SQLite file (use ":memory:" for tests).
        public_base_url: HTTP prefix for returned URLs, e.g.
                         "http://localhost:8003/storage".
    """

    def __init__(self, db_path: str, public_base_url: str) -> None:
        self._db_path = db_path
        self._base = public_base_url.rstrip("/")
        self._conn: aiosqlite.Connection | None = None

    @classmethod
    def from_env(cls) -> SQLiteStorageProvider:
        db_path = os.getenv("STORAGE_SQLITE_DB_PATH", "./.agent-data/storage.db")
        public_base_url = os.getenv(
            "STORAGE_SQLITE_PUBLIC_URL", "http://localhost:8003/storage"
        )
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        return cls(db_path, public_base_url)

    async def _connect(self) -> aiosqlite.Connection:
        if self._conn is None:
            self._conn = await aiosqlite.connect(self._db_path)
            await self._conn.execute(_CREATE_TABLE)
            await self._conn.commit()
        return self._conn

    async def upload(self, data: dict, filename: str | None = None) -> str:
        key = filename or (self.compute_hash(data).hex() + ".json")
        conn = await self._connect()
        await conn.execute(
            "INSERT OR REPLACE INTO deliverables (key, data) VALUES (?, ?)",
            (key, json.dumps(data)),
        )
        await conn.commit()
        return f"{self._base}/{key}"

    async def download(self, url: str) -> dict:
        key = url.rsplit("/", 1)[-1]
        conn = await self._connect()
        async with conn.execute(
            "SELECT data FROM deliverables WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise StorageError(f"Key not found in SQLite: {key!r}")
        return json.loads(row[0])

    async def exists(self, url: str) -> bool:
        key = url.rsplit("/", 1)[-1]
        conn = await self._connect()
        async with conn.execute(
            "SELECT 1 FROM deliverables WHERE key = ?", (key,)
        ) as cur:
            return await cur.fetchone() is not None

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
