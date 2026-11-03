"""SDK exceptions and error dicts → :class:`ChainIntegrationError` (code, retryable)."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from bnbagent.core.contract_mixin import READ_ONLY_MESSAGE
from bnbagent.core.rpc_errors import is_revert_error, is_transient_rpc_error
from bnbagent.exceptions import (
    ContractError,
    ERC8004PartialRegistrationError,
    NetworkError,
    RelaySubmissionUnverifiedError,
    StorageError,
    TransactionPendingError,
)
from bnbagent.signing import PolicyViolation
from bnbagent.utils.public_http import PublicHttpError
from bnbagent.wallets.errors import UnsupportedWalletOperation

from ...exceptions import ChainIntegrationError

T = TypeVar("T")

# Codes shared with the SDK's job-ops error dicts, plus adapter-specific ones.
TX_PENDING = "tx_pending"
RELAY_UNVERIFIED = "relay_unverified"
CHAIN_UNAVAILABLE = "chain_unavailable"
CONTRACT_REVERTED = "contract_reverted"
READ_ONLY = "read_only"
SIGNING_POLICY = "signing_policy_violation"
UNSUPPORTED_WALLET = "unsupported_wallet_operation"
STORAGE_ERROR = "storage_error"
FETCH_REFUSED = "fetch_refused"
FETCH_FAILED = "fetch_failed"
PARTIAL_REGISTRATION = "partial_registration"
INVALID_REQUEST = "invalid_request"
NOT_FOUND = "not_found"
INTERNAL = "internal_error"

_TRANSIENT_FETCH = ("timed out", "busy", "download failed", "incomplete")


def redact(message: str) -> str:
    """Drop URL-shaped tokens (RPC endpoints embed API keys)."""
    return re.sub(r"\S+://\S+", "<redacted>", message)


def _rpc_payload(exc: BaseException) -> bool:
    """web3 6 raises ``ValueError({"code": ..., "message": ...})`` for JSON-RPC errors."""
    payload = exc.args[0] if exc.args else None
    return isinstance(payload, dict) and isinstance(payload.get("code"), int)


def to_chain_error(exc: BaseException, *, op: str) -> ChainIntegrationError:
    """Classify an SDK / web3 / transport exception."""
    if isinstance(exc, ChainIntegrationError):
        return exc
    msg = redact(str(exc)) or type(exc).__name__
    tx_hash = None
    if isinstance(exc, TransactionPendingError):
        code, retryable, tx_hash = TX_PENDING, False, exc.tx_hash
    elif isinstance(exc, RelaySubmissionUnverifiedError):
        code, retryable, tx_hash = RELAY_UNVERIFIED, False, exc.tx_hash
    elif isinstance(exc, ERC8004PartialRegistrationError):
        code, retryable, tx_hash = PARTIAL_REGISTRATION, bool(exc.retryable), exc.tx_hash
    elif isinstance(exc, PolicyViolation):
        code, retryable = SIGNING_POLICY, False
    elif isinstance(exc, UnsupportedWalletOperation):
        code, retryable = UNSUPPORTED_WALLET, False
    elif READ_ONLY_MESSAGE in str(exc):
        code, retryable = READ_ONLY, False
    elif isinstance(exc, PublicHttpError):
        retryable = any(k in str(exc).lower() for k in _TRANSIENT_FETCH)
        code = FETCH_FAILED if retryable else FETCH_REFUSED
    elif isinstance(exc, StorageError):
        code, retryable = STORAGE_ERROR, is_transient_rpc_error(exc.__cause__ or exc)
    elif isinstance(exc, ContractError) or is_revert_error(exc):
        code, retryable = CONTRACT_REVERTED, False
    elif isinstance(exc, (NetworkError, ConnectionError)):
        code, retryable = CHAIN_UNAVAILABLE, True
    elif isinstance(exc, (ValueError, TypeError, KeyError)) and not _rpc_payload(exc):
        code, retryable = INVALID_REQUEST, False
    elif is_transient_rpc_error(exc):
        code, retryable = CHAIN_UNAVAILABLE, True
    else:
        code, retryable = INTERNAL, True
    err = ChainIntegrationError(f"{op}: {msg}", code=code, retryable=retryable)
    err.tx_hash = tx_hash  # type: ignore[attr-defined]
    return err


def from_error_dict(result: Mapping[str, Any], *, op: str) -> ChainIntegrationError:
    """An SDK ``{"success": False, "error", "error_code", "retryable"}`` dict as an error."""
    err = ChainIntegrationError(
        f"{op}: {redact(str(result.get('error') or 'failed'))}",
        code=str(result.get("error_code") or INTERNAL),
        retryable=bool(result.get("retryable", False)),
    )
    err.tx_hash = result.get("tx_hash")  # type: ignore[attr-defined]
    return err


async def call(fn: Callable[..., T], *args: Any, op: str, **kwargs: Any) -> T:
    """Run a synchronous SDK call in a worker thread; map its failures."""
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except Exception as exc:
        raise to_chain_error(exc, op=op) from exc


def to_hex(value: Any) -> str:
    """bytes / HexBytes / str → ``0x``-prefixed lowercase hex."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "0x" + bytes(value).hex()
    text = str(value)
    return text.lower() if text[:2].lower() == "0x" else "0x" + text.lower()


def tx_hash(result: Any, *, op: str) -> str:
    """The transaction hash of an SDK write result dict."""
    if isinstance(result, Mapping):
        if result.get("success") is False:
            raise from_error_dict(result, op=op)
        value = result.get("transactionHash") or result.get("txHash")
        if value:
            return to_hex(value)
    raise ChainIntegrationError(f"{op}: no transaction hash in SDK result", code=INTERNAL)
