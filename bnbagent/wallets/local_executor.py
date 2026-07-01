"""LocalExecutor — build + sign + broadcast :class:`Intent`s locally.

This is the default :class:`~bnbagent.wallets.intents.IntentExecutor`: it
encodes the intent's mechanical ``call``, signs it with a local
:class:`~bnbagent.wallets.WalletProvider`, and broadcasts it via web3
(optionally through a paymaster). It is protocol-agnostic — it never
inspects ``Intent.name``/``kwargs`` and works purely off ``Intent.call``.

It lives alongside the wallet providers because executing intents is a
wallet-domain concern: a self-broadcasting wallet *is* its own executor,
while every pure-signing wallet (EVM, hardware, ...) shares this one
adapter to bridge local signing onto core's web3 send infrastructure.

The transaction-sending logic here is extracted from
``erc8004/contract.py:ContractInterface._execute_transaction`` without
behavioral change: same paymaster path, pre-flight simulation, nonce
management, retry/backoff and receipt handling.
"""

from __future__ import annotations

import concurrent.futures as _cf
import logging
import time
from typing import TYPE_CHECKING, Any

from web3 import Web3
from web3.contract.contract import ContractFunction
from web3.exceptions import TimeExhausted

from ..core.contract_mixin import (
    DEFAULT_RECEIPT_TIMEOUT,  # noqa: F401  — re-exported for back-compat
    MAX_RETRIES,
    MIN_GAS_PRICE_WEI,
    RETRY_BASE_DELAY,
    get_default_receipt_timeout,
    min_gas_price_wei,
)
from ..core.nonce_manager import NonceManager
from ..core.paymaster import Paymaster
from ..exceptions import TransactionPendingError
from .intents import Intent, IntentExecutor

if TYPE_CHECKING:
    from .wallet_provider import WalletProvider

logger = logging.getLogger(__name__)


class LocalExecutor(IntentExecutor):
    """Default executor: build, sign (via a local wallet) and broadcast.

    Args:
        web3: Connected ``Web3`` instance.
        wallet_provider: Local signer used to sign the transaction.
        paymaster: Optional paymaster for gas sponsorship. When provided,
            used for nonce retrieval and transaction sending.
        receipt_timeout: Seconds to wait for a transaction receipt. ``None``
            (default) resolves the SDK default at execute time.
    """

    def __init__(
        self,
        web3: Web3,
        wallet_provider: WalletProvider,
        paymaster: Paymaster | None = None,
        receipt_timeout: int | None = None,
    ):
        self.web3 = web3
        self.wallet_provider = wallet_provider
        self.paymaster = paymaster
        # None = resolve the SDK default lazily at execute time, so a runtime
        # set_default_receipt_timeout() is honored even by a cached executor.
        self.receipt_timeout = receipt_timeout

    def execute(self, intent: Intent) -> dict[str, Any]:
        """Execute an intent's mechanical ``call``.

        Returns ``{"transactionHash": str, "receipt": TxReceipt}``.
        """
        function = intent.call
        if function is None:
            raise ValueError(
                "LocalExecutor requires Intent.call (a web3 ContractFunction); "
                f"got None for intent '{intent.name or intent.description}'"
            )
        return self._execute_function(function, description=intent.description)

    def _run_preflight(self, transaction: dict, description: str) -> None:
        """Simulate the transaction via eth_call before broadcasting.

        Surfaces revert reasons early without spending gas.
        """
        call_params = {
            "from": transaction.get("from"),
            "to": transaction.get("to"),
            "data": transaction.get("data", "0x"),
            "value": transaction.get("value", 0),
            "gas": transaction.get("gas", 2_000_000),
        }
        with _cf.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.web3.eth.call, call_params)
            try:
                future.result(timeout=10)
            except _cf.TimeoutError:
                logger.warning(
                    "[LocalExecutor] Pre-flight eth_call timed out for %s, proceeding",
                    description,
                )
            except Exception as preflight_err:
                err_str = str(preflight_err)
                if "'0x'" in err_str or err_str.strip().endswith(", '0x')"):
                    logger.warning(
                        "[LocalExecutor] Pre-flight returned opaque 0x revert for %s, proceeding",
                        description,
                    )
                else:
                    logger.error(
                        "[LocalExecutor] Pre-flight revert for %s: %s",
                        description,
                        preflight_err,
                    )
                    raise RuntimeError(
                        f"Transaction would revert: {preflight_err}"
                    ) from preflight_err

    def _execute_function(
        self,
        function: ContractFunction,
        description: str = "transaction",
    ) -> dict[str, Any]:
        """Build, sign, send, and wait for receipt for a contract function.

        When a paymaster is configured, attempt a sponsored broadcast; if the
        transaction is not sponsorable (or the paymaster is unreachable), fall
        back to self-pay rather than failing. This lets the per-(protocol,
        network) sponsorship matrix be resolved at runtime by MegaFuel's
        ``pm_isSponsorable`` — e.g. ERC-8183 mainnet writes (never sponsored)
        self-pay automatically while testnet writes are sponsored — with no
        sponsorship policy hard-coded into the executor.
        """
        try:
            wallet_address = self.wallet_provider.address
            gas_estimate = function.estimate_gas({"from": wallet_address})
            logger.debug(f"Gas estimate: {gas_estimate}")
            gas_limit = int(gas_estimate * 1.2)  # Add 20% buffer

            tx_hash: bytes | None = None
            tx_hash_hex = ""
            if self.paymaster is not None:
                sponsored = self._try_sponsored(
                    function, gas_limit, wallet_address, description
                )
                if sponsored is not None:
                    tx_hash, tx_hash_hex = sponsored
            if tx_hash is None:
                tx_hash, tx_hash_hex = self._send_self_pay(
                    function, gas_limit, wallet_address, description
                )

            # Wait for receipt (always via Web3, regardless of how it was sent).
            timeout = (
                self.receipt_timeout
                if self.receipt_timeout is not None
                else get_default_receipt_timeout()
            )
            try:
                receipt = self.web3.eth.wait_for_transaction_receipt(
                    tx_hash, timeout=timeout
                )
            except TimeExhausted as exc:
                # The tx was broadcast (tx_hash is known) but did not confirm
                # in time. This is NOT fatal — surface it as pending with the
                # hash preserved so the caller can check later / retry safely,
                # rather than masking it as a failed transaction.
                logger.warning(
                    "[LocalExecutor] %s broadcast but no receipt after %ss: %s",
                    description,
                    timeout,
                    tx_hash_hex,
                )
                raise TransactionPendingError(
                    tx_hash=tx_hash_hex, timeout_seconds=timeout
                ) from exc

            if receipt["status"] == 0:
                logger.error(
                    "[LocalExecutor] %s reverted on-chain: tx=%s block=%s gasUsed=%s",
                    description,
                    tx_hash_hex,
                    receipt["blockNumber"],
                    receipt["gasUsed"],
                )
                raise RuntimeError(f"Transaction reverted on-chain: {tx_hash_hex}")

            logger.debug(f"Transaction confirmed: {receipt}")

            return {
                "transactionHash": tx_hash_hex,
                "receipt": receipt,
            }

        except TransactionPendingError:
            # Already logged as pending above; not a failure — propagate as-is.
            raise
        except Exception as e:
            logger.error(f"Failed to execute {description}: {str(e)}")
            raise

    def _try_sponsored(
        self,
        function: ContractFunction,
        gas_limit: int,
        wallet_address: str,
        description: str,
    ) -> tuple[bytes, str] | None:
        """Attempt a paymaster-sponsored broadcast.

        Returns ``(tx_hash, tx_hash_hex)`` on success, or ``None`` when the tx
        is not sponsorable or the paymaster is unreachable — the caller then
        falls back to self-pay. A genuine pre-flight revert propagates (self-pay
        could not fix it). The sponsored *send* itself is not retried into a
        self-pay fallback (avoids any double-broadcast risk once submitted).
        """
        try:
            nonce = self.paymaster.eth_getTransactionCount(wallet_address, "pending")
        except Exception as exc:
            logger.warning(
                "[LocalExecutor] paymaster nonce fetch failed for %s (%s); self-paying",
                description, exc,
            )
            return None

        transaction = function.build_transaction(
            {
                "from": wallet_address,
                "chainId": self.web3.eth.chain_id,
                "nonce": nonce,
                "gas": gas_limit,
                "gasPrice": max(
                    self.web3.eth.gas_price,
                    min_gas_price_wei(self.web3.eth.chain_id),
                ),
            }
        )
        logger.debug(f"Building sponsored {description} transaction: {transaction}")

        # Pre-flight simulation to surface a revert reason before spending gas.
        self._run_preflight(transaction, description)

        try:
            sponsorable = self.paymaster.isSponsorable(transaction)
        except Exception as exc:
            logger.warning(
                "[LocalExecutor] isSponsorable check failed for %s (%s); self-paying",
                description, exc,
            )
            return None
        if not sponsorable:
            logger.info(
                "[LocalExecutor] %s is not sponsorable on this network; self-paying gas",
                description,
            )
            return None

        transaction["gasPrice"] = 0
        signed_txn = self.wallet_provider.sign_transaction(transaction)
        tx_hash_hex = self.paymaster.eth_sendRawTransaction(
            signed_txn["rawTransaction"].hex(), tx_options={"UserAgent": "bnbagent/v1.0.0"}
        )
        if not tx_hash_hex.startswith("0x"):
            tx_hash_hex = "0x" + tx_hash_hex
        tx_hash = bytes.fromhex(tx_hash_hex[2:])
        logger.debug(f"Transaction sent via paymaster: {tx_hash_hex}")
        return tx_hash, tx_hash_hex

    def _send_self_pay(
        self,
        function: ContractFunction,
        gas_limit: int,
        wallet_address: str,
        description: str,
    ) -> tuple[bytes, str]:
        """Standard Web3 broadcast: the wallet pays its own gas, with
        NonceManager + retry on transient (nonce / rate-limit) errors."""
        nonce_mgr = NonceManager.for_account(self.web3, wallet_address)
        last_error: Exception | None = None

        # Per-chain gas-price floor, resolved once (chain_id is an RPC call).
        try:
            floor_wei = min_gas_price_wei(self.web3.eth.chain_id)
        except Exception:
            floor_wei = MIN_GAS_PRICE_WEI

        for attempt in range(MAX_RETRIES):
            nonce = nonce_mgr.get_nonce()
            try:
                # Floor at the per-chain minimum and add 20% headroom so a low
                # eth_gasPrice on quiet networks doesn't leave the tx stranded
                # in mempool below the validator cutoff.
                try:
                    gas_price = max(int(self.web3.eth.gas_price * 1.2), floor_wei)
                except Exception:
                    gas_price = floor_wei

                transaction = function.build_transaction(
                    {
                        "from": wallet_address,
                        "chainId": self.web3.eth.chain_id,
                        "nonce": nonce,
                        "gasPrice": gas_price,
                        "gas": gas_limit,
                    }
                )
                logger.debug(f"Building self-pay {description} transaction: {transaction}")

                self._run_preflight(transaction, description)

                signed_txn = self.wallet_provider.sign_transaction(transaction)
                tx_hash = self.web3.eth.send_raw_transaction(
                    signed_txn["rawTransaction"]
                )
                tx_hash_hex = tx_hash.hex()
                if not tx_hash_hex.startswith("0x"):
                    tx_hash_hex = "0x" + tx_hash_hex
                logger.debug(f"Transaction sent via Web3: {tx_hash_hex}")
                return tx_hash, tx_hash_hex
            except Exception as send_err:
                last_error = send_err
                error_str = str(send_err).lower()

                if nonce_mgr.handle_error(send_err, nonce) and attempt < MAX_RETRIES - 1:
                    logger.warning(
                        f"[LocalExecutor] Nonce error, retry "
                        f"{attempt + 1}/{MAX_RETRIES}"
                    )
                    continue

                is_rate_limit = "429" in error_str or "too many requests" in error_str
                if is_rate_limit and attempt < MAX_RETRIES - 1:
                    delay = RETRY_BASE_DELAY * (2**attempt)
                    logger.warning(
                        f"[LocalExecutor] Rate limited, retry "
                        f"{attempt + 1}/{MAX_RETRIES} in {delay:.1f}s"
                    )
                    time.sleep(delay)
                    continue

                # Non-retryable: invalidate cached nonce so the next caller
                # re-seeds from chain rather than leaving a gap.
                nonce_mgr.reset()
                raise

        # All retries exhausted with retryable errors.
        raise last_error  # type: ignore[misc]
