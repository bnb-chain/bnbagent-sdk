"""Shared transaction sending and retry logic for web3 contract clients."""

from __future__ import annotations

import logging
import time
from typing import Any

from .nonce_manager import NonceManager

logger = logging.getLogger(__name__)

MAX_RETRIES = 5
RETRY_BASE_DELAY = 1.0


class ContractClientMixin:
    """Shared transaction sending and retry logic for web3 contract clients.

    Subclasses must set:
        self.w3: Web3 instance
        self._private_key: str | None
        self._wallet_provider: WalletProvider | None
        self._account: str | None
    """

    def _send_tx(self, fn, value: int = 0, gas: int = 2_000_000, skip_preflight: bool = False) -> dict[str, Any]:
        """Build, sign, and send a transaction with nonce management and retry."""
        if not self._private_key and not self._wallet_provider:
            raise RuntimeError("private_key or wallet_provider required for write operations")

        nonce_mgr = NonceManager.for_account(self.w3, self._account)
        last_error = None
        class_name = type(self).__name__

        for attempt in range(MAX_RETRIES):
            nonce = nonce_mgr.get_nonce()
            try:
                # Fetch current gas price and add 20% buffer to avoid mempool stuck
                try:
                    gas_price = int(self.w3.eth.gas_price * 1.2)
                except Exception:
                    gas_price = 3_000_000_000  # 3 Gwei fallback
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
                        "from": tx["from"],
                        "to": tx["to"],
                        "data": tx["data"],
                        "value": tx.get("value", 0),
                        "gas": tx["gas"],
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

                if self._wallet_provider:
                    signed = self._wallet_provider.sign_transaction(tx)
                    raw_tx = signed["rawTransaction"]
                else:
                    signed = self.w3.eth.account.sign_transaction(tx, self._private_key)
                    raw_tx = signed.raw_transaction
                tx_hash = self.w3.eth.send_raw_transaction(raw_tx)
                receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
                if receipt["status"] == 0:
                    raise RuntimeError(
                        f"Transaction reverted on-chain: {receipt['transactionHash'].hex()}"
                    )
                return {
                    "transactionHash": receipt["transactionHash"].hex(),
                    "status": receipt["status"],
                    "receipt": receipt,
                }
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

                raise

        raise last_error  # type: ignore

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
