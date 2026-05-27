"""X402Signer — x402-specific signing wrapper around a WalletProvider.

The wallet's :class:`SigningPolicy` defends against the *structural* class
of blind-sign attacks (unknown domain, denylisted primary type, validity
window). X402Signer adds the *transactional* layer on top:

- expected-recipient byte-equal verification (the LLM cannot quietly
  redirect a payment by altering ``message['to']``)
- per-call max_value cap per token (so a malicious 402 challenge with an
  inflated ``value`` is rejected before signing)
- session-cumulative budget tracker (rate-limits a compromised agent
  even if individual calls are within max_value)

x402 SchemeExactEVM and EIP-3009 ``TransferWithAuthorization`` are the
primary intended primary types; callers signing other types via this
wrapper should ensure the message has ``to`` and ``value`` fields with
the same semantics.
"""

from __future__ import annotations

import logging
from typing import Any

from web3 import Web3

from ..signing import PolicyViolation
from ..wallets import WalletProvider
from .budget import SessionBudgetTracker
from .errors import (
    X402AmountExceededError,
    X402BudgetExhaustedError,
    X402PolicyError,
    X402RecipientMismatchError,
)

logger = logging.getLogger(__name__)


class X402Signer:
    """Constrained signer for x402 payment flows.

    Construct once per :class:`bnbagent.WalletProvider` per scope/session.
    Pass the resulting signer to agent tool functions instead of the raw
    wallet — the closure then cannot bypass the policy stack.
    """

    def __init__(
        self,
        wallet: WalletProvider,
        *,
        max_value_per_call: dict[str, int] | None = None,
        session_budget: dict[str, int] | None = None,
    ) -> None:
        """
        Args:
            wallet: The underlying wallet provider. Its own SigningPolicy
                still applies — X402Signer never bypasses it.
            max_value_per_call: ``{token_address: max_base_units}`` cap on
                ``message['value']`` for any sign_payment call against that
                token (e.g. ``{U_MAINNET: 1_000_000}`` for 1 USDC if 18
                decimals; choose units consistent with the on-chain token).
                Missing token → no per-call cap.
            session_budget: ``{token_address: total_base_units}`` cap on
                cumulative spend across all sign_payment calls in this
                signer's lifetime. Independent of per-call cap.
        """
        self._wallet = wallet
        self._max_value: dict[str, int] = {}
        if max_value_per_call:
            for addr, cap in max_value_per_call.items():
                self._max_value[Web3.to_checksum_address(addr)] = int(cap)
        self._budget = SessionBudgetTracker(session_budget)

    @property
    def wallet_address(self) -> str:
        return self._wallet.address

    @property
    def budget(self) -> SessionBudgetTracker:
        return self._budget

    def sign_payment(
        self,
        *,
        domain: dict[str, Any],
        types: dict[str, Any],
        message: dict[str, Any],
        expected_to: str,
    ) -> dict[str, Any]:
        """Sign an x402 / EIP-3009 payment after all guards pass.

        Args:
            domain: EIP-712 domain; ``chainId`` + ``verifyingContract``
                must be present (checked by wallet's SigningPolicy).
            types: EIP-712 types dict.
            message: Struct values. Must include ``to`` and ``value`` for
                X402Signer's recipient/amount guards.
            expected_to: Address the caller commits to as the payee.
                Compared byte-equal (case-insensitive) against
                ``message['to']``. Any drift → X402RecipientMismatchError.

        Returns:
            Signature dict from the wallet (``signature``, ``messageHash``,
            etc.).

        Raises:
            X402RecipientMismatchError: ``message['to']`` differs from
                ``expected_to``.
            X402AmountExceededError: ``message['value']`` exceeds per-call
                ``max_value_per_call`` for this token.
            X402BudgetExhaustedError: session budget would be exceeded.
            X402PolicyError: wraps an underlying
                :class:`bnbagent.signing.PolicyViolation`.
        """
        verifying = Web3.to_checksum_address(domain["verifyingContract"])

        # ── L0 recipient (cheapest check, fail fast) ───────────────
        msg_to = message.get("to")
        if not isinstance(msg_to, str):
            raise X402RecipientMismatchError(
                f"message['to'] is missing or not an address: {msg_to!r}"
            )
        if msg_to.lower() != expected_to.lower():
            raise X402RecipientMismatchError(
                f"expected_to={expected_to} does not match "
                f"message['to']={msg_to} — refusing to sign"
            )

        # ── L1 per-call value cap ─────────────────────────────────
        value = int(message.get("value", 0))
        cap = self._max_value.get(verifying)
        if cap is not None and value > cap:
            raise X402AmountExceededError(
                f"value {value} exceeds max_value_per_call={cap} for token {verifying}"
            )

        # ── L1.5 signer binding: message['from'] must be this wallet ──
        # A forged 'from' (with policy-compliant to/value) would otherwise
        # reserve budget and sign. On-chain EIP-3009 rejects the mismatched
        # signer, but the session budget is already spent — a DoS on payment
        # capability. Check before reserve() so rejected calls cost nothing.
        msg_from = message.get("from")
        if not isinstance(msg_from, str):
            raise X402RecipientMismatchError(
                f"message['from'] is missing or not an address: {msg_from!r}"
            )
        try:
            wallet_cs = Web3.to_checksum_address(self._wallet.address)
            msg_from_cs = Web3.to_checksum_address(msg_from)
        except (ValueError, TypeError) as e:
            raise X402RecipientMismatchError(
                f"message['from'] is not a valid address: {msg_from!r}"
            ) from e
        if msg_from_cs != wallet_cs:
            raise X402RecipientMismatchError(
                f"message['from']={msg_from_cs} does not match wallet "
                f"{wallet_cs} — refusing to sign"
            )

        # ── L2 session budget (atomic reserve; rollback on any failure) ──
        # reserve() does the check+increment under a single lock so two
        # concurrent sign_payment calls cannot both pass the budget check
        # and overspend. The reservation is released by rollback() if the
        # downstream sign fails — preserving "rejected signs never consume
        # budget" under concurrency.
        self._budget.reserve(verifying, value)

        # ── L3 wallet sign (SigningPolicy enforces here) ───────────
        try:
            signed = self._wallet.sign_typed_data(domain, types, message)
        except PolicyViolation as e:
            self._budget.rollback(verifying, value)
            # Re-raise as X402-layer error for caller convenience while
            # preserving full context via __cause__.
            raise X402PolicyError(str(e)) from e
        except BaseException:
            # Any other failure (incl. KeyboardInterrupt) must release the
            # reservation; bare except is intentional so the budget never
            # silently locks up.
            self._budget.rollback(verifying, value)
            raise

        logger.info(
            "x402 payment signed: token=%s value=%s to=%s expected_to=%s",
            verifying, value, msg_to, expected_to,
        )
        return signed
