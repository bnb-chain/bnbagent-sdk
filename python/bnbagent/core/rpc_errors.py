"""Tell RPC and transport failures, which a retry can get past, from the rest."""

from __future__ import annotations

import asyncio
import concurrent.futures
import re

import requests
from web3 import exceptions as web3_exceptions

from ..exceptions import NetworkError

_TRANSIENT_TYPES: tuple[type[BaseException], ...] = (
    OSError,
    asyncio.TimeoutError,
    concurrent.futures.TimeoutError,
    requests.exceptions.RequestException,
    NetworkError,
) + tuple(
    error_type
    for name in (
        # web3 >= 7; web3 6 raises ValueError({"code": ..., "message": ...}) instead
        "Web3RPCError",
        "ProviderConnectionError",
        "TooManyRequests",
        "TimeExhausted",
        "BadResponseFormat",
        "PersistentConnectionError",
    )
    if isinstance(error_type := getattr(web3_exceptions, name, None), type)
)
try:
    import httpx

    _TRANSIENT_TYPES += (httpx.HTTPError,)
except ImportError:  # the ipfs extra is not installed
    pass
try:
    import aiohttp

    _TRANSIENT_TYPES += (aiohttp.ClientError,)
except ImportError:
    pass

_REVERT_TYPES = (
    web3_exceptions.ContractLogicError,
    web3_exceptions.BadFunctionCallOutput,
)

_TRANSIENT_REASON = re.compile(
    r"\b429\b|too many requests|rate limit|limit exceeded|timeout|timed out|connection"
    r"|network|\brpc\b|header not found|missing trie node",
    re.IGNORECASE,
)


def _cause_chain(exc: BaseException):
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__


def is_revert_error(exc: BaseException) -> bool:
    """Whether ``exc`` is a revert (``ContractLogicError``) or a call that returned
    no data (``BadFunctionCallOutput``) anywhere in its ``__cause__`` chain:
    deterministic for the same chain state, so a retry cannot get past it."""
    return any(isinstance(current, _REVERT_TYPES) for current in _cause_chain(exc))


def is_transient_rpc_error(exc: BaseException) -> bool:
    """Whether ``exc`` is a transport or JSON-RPC failure, so the same request can
    get past it on a retry.

    True for an ``OSError``, a timeout, a ``requests`` / ``httpx`` / ``aiohttp``
    error, web3's RPC and provider errors (``Web3RPCError`` and its subclasses),
    an SDK :class:`~bnbagent.exceptions.NetworkError`, or a web3 6 ``{"code": ...}``
    payload, anywhere in the ``__cause__`` chain; failing those, a message naming
    such trouble. A revert (:func:`is_revert_error`) is never transient, nor is any
    other error (e.g. one raised by the content being checked).
    """
    if is_revert_error(exc):
        return False
    transient = False
    for current in _cause_chain(exc):
        payload = current.args[0] if current.args else None
        transient = (
            transient
            or isinstance(current, _TRANSIENT_TYPES)
            or (isinstance(payload, dict) and isinstance(payload.get("code"), int))
        )
    return transient or bool(_TRANSIENT_REASON.search(str(exc)))
