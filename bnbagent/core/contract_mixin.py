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

    def _send_tx(self, fn, value: int = 0, gas: int = 2_000_000) -> dict[str, Any]:
        """Build, sign, and send a transaction with nonce management and retry."""
        if not self._private_key and not self._wallet_provider:
            raise RuntimeError("private_key or wallet_provider required for write operations")

        nonce_mgr = NonceManager.for_account(self.w3, self._account)
        last_error = None
        class_name = type(self).__name__

        for attempt in range(MAX_RETRIES):
            nonce = nonce_mgr.get_nonce()
            try:
                tx = fn.build_transaction(
                    {
                        "from": self._account,
                        "nonce": nonce,
                        "gas": gas,
                        "value": value,
                    }
                )
                if self._wallet_provider:
                    signed = self._wallet_provider.sign_transaction(tx)
                    raw_tx = signed["rawTransaction"]
                else:
                    signed = self.w3.eth.account.sign_transaction(tx, self._private_key)
                    raw_tx = signed.raw_transaction
                tx_hash = self.w3.eth.send_raw_transaction(raw_tx)
                receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
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
