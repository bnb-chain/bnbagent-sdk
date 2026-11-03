"""Persistent service state in sqlite: async jobs, idempotency records, credits.

Thread-safe (one connection guarded by a lock; every operation is a short transaction).
In **no-retention** mode only hashes, aggregates and verdicts are written: case contents,
per-case results and reasons never reach the database. Full async results are then kept in
process memory only, until they are read or expire.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..hashing import hash_obj

__all__ = ["Credit", "IdempotencyRecord", "Job", "Store", "summarize_response", "token_hash"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    result TEXT,
    error TEXT,
    credit TEXT,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS idempotency (
    key TEXT PRIMARY KEY,
    request_hash TEXT NOT NULL,
    state TEXT NOT NULL,
    status_code INTEGER,
    response TEXT,
    response_hash TEXT,
    headers TEXT,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS credits (
    credit_id TEXT PRIMARY KEY,
    payer TEXT NOT NULL,
    amount INTEGER NOT NULL,
    asset TEXT,
    quote_id TEXT,
    request_hash TEXT,
    used INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
"""

_SUMMARY_KEYS = (
    "schema_version",
    "stage",
    "subject",
    "suite_hash",
    "result_hash",
    "response_hash",
    "aggregate",
    "verdict",
    "cost",
    "attestation",
)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def summarize_response(body: Mapping[str, Any]) -> dict[str, Any]:
    """The no-retention form of an EvalResponse: hashes, aggregates and the verdict."""
    out = {k: body[k] for k in _SUMMARY_KEYS if k in body}
    prov = body.get("provenance")
    if isinstance(prov, Mapping):
        out["provenance"] = {"request_hash": prov.get("request_hash")}
    out["retained"] = False
    return out


@dataclass(frozen=True)
class Job:
    job_id: str
    status: str  # queued | running | done | failed
    request_hash: str
    result: dict[str, Any] | None
    error: str | None
    credit: dict[str, Any] | None
    created_at: float
    expires_at: float


@dataclass(frozen=True)
class IdempotencyRecord:
    key: str
    request_hash: str
    state: str  # pending | done
    status_code: int | None
    response: dict[str, Any] | None
    response_hash: str | None
    headers: dict[str, str]


@dataclass(frozen=True)
class Credit:
    credit_id: str
    payer: str
    amount: int
    asset: str | None
    quote_id: str | None
    request_hash: str | None
    used: bool
    expires_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "credit_id": self.credit_id,
            "payer": self.payer,
            "amount": str(self.amount),
            "asset": self.asset,
            "quote_id": self.quote_id,
            "expires_at": int(self.expires_at),
        }


class _Result:
    """A fully read query result (rows and row count)."""

    __slots__ = ("rows", "rowcount")

    def __init__(self, rows: list[tuple], rowcount: int):
        self.rows, self.rowcount = rows, rowcount

    def fetchone(self) -> tuple | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple]:
        return list(self.rows)


class Store:
    def __init__(
        self,
        path: str | Path = ":memory:",
        *,
        no_retention: bool = False,
        clock: Callable[[], float] = time.time,
    ):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.no_retention = no_retention
        self.clock = clock
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        if self.path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        self._volatile: dict[str, dict[str, Any]] = {}  # no-retention async results

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _exec(self, sql: str, args: tuple = ()) -> _Result:
        # Rows are read while holding the lock: the connection is shared between threads, and
        # fetching from a cursor after releasing it races with other statements.
        with self._lock:
            cur = self._db.execute(sql, args)
            return _Result(cur.fetchall(), cur.rowcount)

    def _retain(self, body: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if body is None:
            return None
        if self.no_retention and "result_hash" in body:
            return summarize_response(body)
        return dict(body)

    # -------------------------------------------------------------------- jobs
    def create_job(self, request_hash: str, ttl_s: float) -> tuple[str, str]:
        """A new queued job; returns ``(job_id, access_token)`` (only the token hash is kept)."""
        job_id, token = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
        now = self.clock()
        self._exec(
            "INSERT INTO jobs (job_id, token_hash, status, request_hash, created_at, expires_at)"
            " VALUES (?, ?, 'queued', ?, ?, ?)",
            (job_id, token_hash(token), request_hash, now, now + ttl_s),
        )
        return job_id, token

    def rotate_token(self, job_id: str) -> str:
        token = secrets.token_urlsafe(32)
        self._exec("UPDATE jobs SET token_hash=? WHERE job_id=?", (token_hash(token), job_id))
        return token

    def set_job(
        self,
        job_id: str,
        status: str,
        *,
        result: Mapping[str, Any] | None = None,
        error: str | None = None,
        credit: Mapping[str, Any] | None = None,
    ) -> None:
        stored = self._retain(result)
        if result is not None and self.no_retention:
            self._volatile[job_id] = dict(result)
        self._exec(
            "UPDATE jobs SET status=?, result=?, error=?, credit=? WHERE job_id=?",
            (
                status,
                _dump(stored),
                error,
                _dump(dict(credit) if credit else None),
                job_id,
            ),
        )

    def get_job(self, job_id: str, token: str | None) -> Job | None:
        """The job if ``token`` matches and it has not expired; ``None`` otherwise."""
        row = self._exec(
            "SELECT job_id, token_hash, status, request_hash, result, error, credit,"
            " created_at, expires_at FROM jobs WHERE job_id=?",
            (job_id,),
        ).fetchone()
        if row is None or not token or not hmac.compare_digest(row[1], token_hash(token)):
            return None
        if row[8] <= self.clock():
            self._exec("DELETE FROM jobs WHERE job_id=?", (job_id,))
            self._volatile.pop(job_id, None)
            return None
        # No retention: the full result is handed out once, later reads get the summary.
        result = self._volatile.pop(job_id, None) or _load(row[4])
        return Job(row[0], row[2], row[3], result, row[5], _load(row[6]), row[7], row[8])

    def purge_expired(self) -> None:
        now = self.clock()
        expired = [
            r[0]
            for r in self._exec("SELECT job_id FROM jobs WHERE expires_at<=?", (now,)).fetchall()
        ]
        for job_id in expired:
            self._volatile.pop(job_id, None)
        for table in ("jobs", "idempotency", "credits"):
            self._exec(f"DELETE FROM {table} WHERE expires_at<=?", (now,))  # noqa: S608

    # ------------------------------------------------------------- idempotency
    def reserve(self, key: str, request_hash: str, ttl_s: float) -> IdempotencyRecord | None:
        """Atomically claim ``key``; returns ``None`` if claimed, else the existing record."""
        now = self.clock()
        with self._lock:
            self._db.execute("DELETE FROM idempotency WHERE key=? AND expires_at<=?", (key, now))
            cur = self._db.execute(
                "INSERT OR IGNORE INTO idempotency (key, request_hash, state, created_at,"
                " expires_at) VALUES (?, ?, 'pending', ?, ?)",
                (key, request_hash, now, now + ttl_s),
            )
            if cur.rowcount == 1:
                return None
        return self.get_record(key)

    def get_record(self, key: str) -> IdempotencyRecord | None:
        row = self._exec(
            "SELECT key, request_hash, state, status_code, response, response_hash, headers"
            " FROM idempotency WHERE key=?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        return IdempotencyRecord(
            row[0], row[1], row[2], row[3], _load(row[4]), row[5], _load(row[6]) or {}
        )

    def complete(
        self,
        key: str,
        status_code: int,
        response: Mapping[str, Any],
        headers: Mapping[str, str] | None = None,
    ) -> None:
        stored = self._retain(response)
        self._exec(
            "UPDATE idempotency SET state='done', status_code=?, response=?, response_hash=?,"
            " headers=? WHERE key=?",
            (status_code, _dump(stored), hash_obj(response), _dump(dict(headers or {})), key),
        )

    def alias(self, key: str, other: str) -> None:
        """Make ``other`` (e.g. the payment header key) return the same record as ``key``."""
        record = self.get_record(key)
        if record is None:
            return
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO idempotency SELECT ?, request_hash, state, status_code,"
                " response, response_hash, headers, created_at, expires_at FROM idempotency"
                " WHERE key=?",
                (other, key),
            )

    def release(self, key: str) -> None:
        """Drop a pending reservation (nothing was charged; the caller may retry)."""
        self._exec("DELETE FROM idempotency WHERE key=? AND state='pending'", (key,))

    # ----------------------------------------------------------------- credits
    def issue_credit(
        self,
        payer: str,
        amount: int,
        *,
        asset: str | None = None,
        quote_id: str | None = None,
        request_hash: str | None = None,
        ttl_s: float = 30 * 86400,
    ) -> Credit:
        credit_id = secrets.token_urlsafe(24)
        now = self.clock()
        self._exec(
            "INSERT INTO credits (credit_id, payer, amount, asset, quote_id, request_hash, used,"
            " created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
            (credit_id, payer, int(amount), asset, quote_id, request_hash, now, now + ttl_s),
        )
        return Credit(
            credit_id, payer, int(amount), asset, quote_id, request_hash, False, now + ttl_s
        )

    def get_credit(self, credit_id: str) -> Credit | None:
        row = self._exec(
            "SELECT credit_id, payer, amount, asset, quote_id, request_hash, used, expires_at"
            " FROM credits WHERE credit_id=?",
            (credit_id,),
        ).fetchone()
        if row is None or row[7] <= self.clock():
            return None
        return Credit(row[0], row[1], row[2], row[3], row[4], row[5], bool(row[6]), row[7])

    def redeem_credit(self, credit_id: str) -> bool:
        """Atomically mark an unused, unexpired credit as used. ``False`` if not possible."""
        cur = self._exec(
            "UPDATE credits SET used=1 WHERE credit_id=? AND used=0 AND expires_at>?",
            (credit_id, self.clock()),
        )
        return cur.rowcount == 1

    def restore_credit(self, credit_id: str) -> None:
        self._exec("UPDATE credits SET used=0 WHERE credit_id=?", (credit_id,))


def _dump(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, sort_keys=True)


def _load(text: str | None) -> Any:
    return None if text is None else json.loads(text)
