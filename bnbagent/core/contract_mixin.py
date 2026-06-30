"""Shared transaction sending and retry logic for web3 contract clients."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from web3.exceptions import ContractLogicError, TimeExhausted

from ..exceptions import TransactionPendingError
from ..networks.addresses import BSC_MAINNET_CHAIN_ID, BSC_TESTNET_CHAIN_ID
from .nonce_manager import NonceManager

logger = logging.getLogger(__name__)

MAX_RETRIES = 5
RETRY_BASE_DELAY = 1.0

# Floor for transaction gas price. ``eth_gasPrice`` on BSC testnet (and other
# low-traffic EVM RPCs) sometimes returns values below what validators actually
# require to include the tx, leaving the broadcast stuck in mempool. The floor
# is per-chain because the real minimum differs by ~10×: BSC mainnet sits at
# 0.1 Gwei since the gas-price reform, while testnet's cutoff is ~1 Gwei. A
# single mainnet-only floor (the previous behaviour) stranded testnet txs; the
# even-older 3 Gwei floor was a 30× overcharge on mainnet — see
# https://github.com/bnb-chain/bnbagent-sdk/issues/40 for receipts.
MIN_GAS_PRICE_WEI = 100_000_000  # 0.1 Gwei — default/fallback for unknown chains

_MIN_GAS_PRICE_WEI_PER_CHAIN = {
    BSC_MAINNET_CHAIN_ID: 100_000_000,  # 0.1 Gwei
    BSC_TESTNET_CHAIN_ID: 1_000_000_000,  # 1 Gwei — above the testnet validator cutoff
}
_min_gas_price_override: int | None = None  # set via set_min_gas_price_wei()


def set_min_gas_price_wei(wei: int) -> None:
    """Pin the gas-price floor for *all* chains.

    Public API (re-exported from ``bnbagent``). Overrides both the per-chain
    defaults and the ``BNBAGENT_MIN_GAS_PRICE_WEI`` env var; precedence is
    ``set_min_gas_price_wei() > env > per-chain default``.
    """
    global _min_gas_price_override
    if wei <= 0:
        raise ValueError("min gas price must be positive")
    _min_gas_price_override = int(wei)


def min_gas_price_wei(chain_id: int) -> int:
    """Resolve the gas-price floor (wei) for ``chain_id``.

    Precedence: the :func:`set_min_gas_price_wei` override, then the
    ``BNBAGENT_MIN_GAS_PRICE_WEI`` env var (global, all chains), then the
    per-chain default (``MIN_GAS_PRICE_WEI`` for unknown chains).
    """
    if _min_gas_price_override is not None:
        return _min_gas_price_override
    raw = os.environ.get("BNBAGENT_MIN_GAS_PRICE_WEI")
    if raw:
        try:
            return int(raw)
        except ValueError:
            logger.warning("Ignoring invalid BNBAGENT_MIN_GAS_PRICE_WEI=%r", raw)
    return _MIN_GAS_PRICE_WEI_PER_CHAIN.get(chain_id, MIN_GAS_PRICE_WEI)


# Fallback gas limit used only when on-chain estimation is unavailable
# (transport/RPC error, opaque revert data) or bypassed (skip_preflight).
# Nodes require ``balance >= gas_limit * gasPrice`` upfront, so a blanket
# 2M limit would demand ~0.007 BNB per tx while typical writes burn
# 50-150k gas — estimation keeps the entry cost proportional to real usage.
DEFAULT_GAS_FALLBACK = 2_000_000

# Default seconds to wait for a transaction receipt. web3.py's own default
# (120s) is too short on congested BNB Chain / paymaster-relayed paths; both
# write paths (``_send_tx`` here and ``LocalExecutor``) share this value.
DEFAULT_RECEIPT_TIMEOUT = 300

_receipt_timeout_override: int | None = None  # set via set_default_receipt_timeout()


def set_default_receipt_timeout(seconds: int) -> None:
    """Set the default transaction-receipt timeout (seconds) for both write paths.

    Public API (re-exported from ``bnbagent``). Takes precedence over the
    ``BNBAGENT_RECEIPT_TIMEOUT`` env var and applies to operations executed
    after the call — including the cached intent executor, which resolves the
    default lazily at execute time.
    """
    global _receipt_timeout_override
    if seconds <= 0:
        raise ValueError("receipt timeout must be positive")
    _receipt_timeout_override = int(seconds)


def get_default_receipt_timeout() -> int:
    """Resolve the default receipt timeout (seconds).

    Precedence: the :func:`set_default_receipt_timeout` override, then the
    ``BNBAGENT_RECEIPT_TIMEOUT`` env var, then ``DEFAULT_RECEIPT_TIMEOUT``.
    """
    if _receipt_timeout_override is not None:
        return _receipt_timeout_override
    raw = os.environ.get("BNBAGENT_RECEIPT_TIMEOUT")
    if raw:
        try:
            return int(raw)
        except ValueError:
            logger.warning("Ignoring invalid BNBAGENT_RECEIPT_TIMEOUT=%r", raw)
    return DEFAULT_RECEIPT_TIMEOUT


class ContractClientMixin:
    """Shared transaction sending and retry logic for web3 contract clients.

    Subclasses must set:
        self.w3: Web3 instance
        self._wallet_provider: WalletProvider | None  (None = read-only client)
        self._account: str | None
    """

    def _execute_intent(self, intent) -> dict[str, Any]:
        """Run an :class:`~bnbagent.wallets.intents.Intent` through the
        wallet's executor.

        This is the write path for clients migrated to the intent seam: the
        wallet decides how the intent executes (a pure signer wraps itself in
        a ``LocalExecutor``; a self-broadcasting wallet, e.g. twak, runs the
        semantic operation itself). The executor is built lazily and cached
        per client instance.
        """
        if not self._wallet_provider:
            raise RuntimeError(
                "wallet_provider is required for write operations (client is read-only)"
            )
        executor = getattr(self, "_intent_executor", None)
        if executor is None:
            from ..wallets.intents import ExecutionContext

            # ``_paymaster`` is optional and set by clients that opt into gas
            # sponsorship (e.g. ERC8183Client on a sponsored network); absent
            # for read-only or self-pay clients. The wallet's executor decides
            # how to use it (the local executor sponsors when sponsorable and
            # self-pays otherwise; a self-broadcasting wallet ignores it).
            executor = self._wallet_provider.make_executor(
                ExecutionContext(
                    web3=self.w3, paymaster=getattr(self, "_paymaster", None)
                )
            )
            self._intent_executor = executor
        return executor.execute(intent)

    def _send_tx(
        self, fn, value: int = 0, gas: int | None = None, skip_preflight: bool = False
    ) -> dict[str, Any]:
        """Build, sign, and send a transaction with nonce management and retry.

        ``gas=None`` (default) estimates the limit on-chain with a 20% buffer
        (mirroring erc8004's ``_execute_transaction``); pass an explicit ``gas``
        to skip estimation.
        """
        if not self._wallet_provider:
            raise RuntimeError(
                "wallet_provider is required for write operations (client is read-only)"
            )

        if gas is None:
            gas = self._estimate_gas_limit(fn, value, skip_preflight)

        # Resolve the per-chain gas-price floor once (chain_id is an RPC call).
        try:
            floor_wei = min_gas_price_wei(self.w3.eth.chain_id)
        except Exception:
            floor_wei = MIN_GAS_PRICE_WEI

        nonce_mgr = NonceManager.for_account(self.w3, self._account)
        last_error = None
        class_name = type(self).__name__

        for attempt in range(MAX_RETRIES):
            nonce = nonce_mgr.get_nonce()
            try:
                # Fetch current gas price and add 20% buffer; floor at the
                # per-chain minimum so a low ``eth_gasPrice`` reading on quiet
                # networks (BSC testnet returns 0.1 Gwei when idle) doesn't
                # leave the tx stranded in mempool below the validator cutoff.
                try:
                    gas_price = max(int(self.w3.eth.gas_price * 1.2), floor_wei)
                except Exception:
                    gas_price = floor_wei
                tx = fn.build_transaction(
                    {
                        "from": self._account,
                        "nonce": nonce,
                        "gas": gas,
                        "gasPrice": gas_price,
                        "value": value,
                    }
                )
                # Pre-flight: simulate via eth_call to surface revert reason before spending gas.
                # Skipped when skip_preflight=True (e.g. when node returns opaque 0x reverts).
                if not skip_preflight:
                    import concurrent.futures as _cf
                    _call_params = {
                        "from": self._account,
                        "to": tx.get("to"),
                        "data": tx.get("data", "0x"),
                        "value": tx.get("value", 0),
                        "gas": tx.get("gas", gas),
                    }
                    with _cf.ThreadPoolExecutor(max_workers=1) as _pool:
                        _future = _pool.submit(self.w3.eth.call, _call_params)
                        try:
                            _future.result(timeout=10)
                        except _cf.TimeoutError:
                            logger.warning(f"[{class_name}] Pre-flight eth_call timed out, proceeding anyway")
                        except Exception as preflight_err:
                            err_str = str(preflight_err)
                            # Skip pre-flight if node returns opaque 0x (no revert data)
                            if "'0x'" in err_str or err_str.strip().endswith(", '0x')"):
                                logger.warning(f"[{class_name}] Pre-flight returned opaque 0x revert, proceeding to on-chain tx")
                            else:
                                raise RuntimeError(f"Transaction would revert: {preflight_err}") from preflight_err

                signed = self._wallet_provider.sign_transaction(tx)
                raw_tx = signed["rawTransaction"]
                tx_hash = self.w3.eth.send_raw_transaction(raw_tx)
                timeout = get_default_receipt_timeout()
                try:
                    receipt = self.w3.eth.wait_for_transaction_receipt(
                        tx_hash, timeout=timeout
                    )
                except TimeExhausted as exc:
                    # Broadcast OK (nonce consumed) but unconfirmed in time —
                    # surface as pending with the hash, never as a fatal/retry
                    # (a blind retry would risk a double-broadcast).
                    tx_hash_hex = tx_hash.hex()
                    if not tx_hash_hex.startswith("0x"):
                        tx_hash_hex = "0x" + tx_hash_hex
                    raise TransactionPendingError(
                        tx_hash=tx_hash_hex, timeout_seconds=timeout
                    ) from exc
                if receipt["status"] == 0:
                    raise RuntimeError(
                        f"Transaction reverted on-chain: {receipt['transactionHash'].hex()}"
                    )
                return {
                    "transactionHash": receipt["transactionHash"].hex(),
                    "status": receipt["status"],
                    "receipt": receipt,
                }
            except TransactionPendingError:
                # Broadcast succeeded; not a failure and not retryable here.
                raise
            except Exception as e:
                last_error = e
                error_str = str(e).lower()

                # Nonce error -> re-sync and retry
                if nonce_mgr.handle_error(e, nonce) and attempt < MAX_RETRIES - 1:
                    logger.warning(
                        f"[{class_name}] Nonce error, retry {attempt + 1}/{MAX_RETRIES}"
                    )
                    continue

                # Rate limit -> backoff and retry
                is_rate_limit = "429" in error_str or "too many requests" in error_str
                if is_rate_limit and attempt < MAX_RETRIES - 1:
                    delay = RETRY_BASE_DELAY * (2**attempt)
                    logger.warning(
                        f"[{class_name}] Rate limited, retry {attempt + 1}/{MAX_RETRIES} "
                        f"in {delay:.1f}s"
                    )
                    time.sleep(delay)
                    continue

                # Any other error path (preflight revert, receipt timeout,
                # transient RPC failure): the cached nonce was already
                # incremented in get_nonce() but the tx may not have been
                # mined or even broadcast. Invalidate the cache so the next
                # caller re-seeds from chain instead of leaving a permanent
                # nonce gap that strands every subsequent tx in mempool.
                nonce_mgr.reset()
                raise

        raise last_error  # type: ignore

    def _estimate_gas_limit(self, fn, value: int, skip_preflight: bool) -> int:
        """Estimate gas for ``fn`` with a 20% buffer.

        Falls back to ``DEFAULT_GAS_FALLBACK`` when estimation is unavailable —
        transport/RPC errors, or nodes returning opaque ``0x`` revert data (the
        same escape hatch the pre-flight uses). A genuine revert surfaced by
        the estimation is raised as ``Transaction would revert`` so the caller
        sees the reason instead of a masked fallback broadcast.
        """
        class_name = type(self).__name__
        if skip_preflight:
            # estimate_gas simulates the call exactly like the pre-flight does;
            # callers that opted out of simulation get the fallback limit.
            return DEFAULT_GAS_FALLBACK
        try:
            estimate = fn.estimate_gas({"from": self._account, "value": value})
            return int(estimate * 1.2)
        except Exception as e:
            err_str = str(e)
            is_opaque = "'0x'" in err_str or err_str.strip().endswith(", '0x')")
            if isinstance(e, ContractLogicError) and not is_opaque:
                raise RuntimeError(f"Transaction would revert: {e}") from e
            logger.warning(
                f"[{class_name}] Gas estimation unavailable ({e}); "
                f"falling back to gas={DEFAULT_GAS_FALLBACK}"
            )
            return DEFAULT_GAS_FALLBACK

    def _call_with_retry(self, fn):
        """Call a read function with retry on rate limit."""
        last_error = None
        class_name = type(self).__name__
        for attempt in range(MAX_RETRIES):
            try:
                return fn.call()
            except Exception as e:
                last_error = e
                error_str = str(e).lower()
                is_rate_limit = "429" in error_str or "too many requests" in error_str
                if is_rate_limit and attempt < MAX_RETRIES - 1:
                    delay = RETRY_BASE_DELAY * (2**attempt)
                    logger.warning(
                        f"[{class_name}] Rate limited (read), retry {attempt + 1}/{MAX_RETRIES} "
                        f"in {delay:.1f}s"
                    )
                    time.sleep(delay)
                else:
                    raise
        raise last_error  # type: ignore
