"""TwakX402Payer — delegated x402 payments through the twak CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .budget import SessionBudgetTracker
from .errors import (
    X402AmountExceededError,
    X402NoPayableRouteError,
    X402PolicyError,
    X402RecipientMismatchError,
)
from .payer import X402PaymentResult, X402Quote

if TYPE_CHECKING:
    from ..wallets.twak_provider import TWAKProvider

#: Default cap on the challenge's claimed ``maxTimeoutSeconds`` (design F-2).
#: Deliberately wider than SigningPolicy's 600s window: field-verified Bazaar
#: spec endpoints (onesource) advertise 3600s, and on the twak path the
#: *actual* signing validity window is set by twak internally — this check
#: only constrains what the challenge claims, a different risk class.
DEFAULT_MAX_TIMEOUT_SECONDS = 3600


class TwakX402Payer:
    """X402Payer backed by the twak CLI's built-in x402 client.

    This is the SDK-primitives guard layer of the three-layer defense in
    depth (design §3.2): the application policy layer (host allowlists,
    USD budgets) sits above and calls this payer; twak's own
    ``--max-payment`` hard cap sits below — the per-call cap is therefore
    enforced **twice by design** (once by the precheck here, once inside
    twak). SigningPolicy itself cannot run on this path (the EIP-712
    payload is built, signed and discarded inside the twak process), so
    each of its rules has a semantic equivalent in the quote-terms
    precheck: domain ↔ ``asset``, validity window ↔ ``maxTimeoutSeconds``.
    """

    def __init__(
        self,
        provider: TWAKProvider,
        *,
        session_budget: SessionBudgetTracker | None = None,
        expected_pay_to: str | None = None,
        expected_asset: str | None = None,
        max_timeout_seconds: int = DEFAULT_MAX_TIMEOUT_SECONDS,
    ) -> None:
        """
        Args:
            provider: The :class:`~bnbagent.wallets.TWAKProvider` whose CLI
                executes quotes and payments.
            session_budget: Optional cumulative spend tracker, keyed by
                token (asset) address. Shared with the X402Signer path.
            expected_pay_to: When set, the quoted ``payTo`` must byte-equal
                this address (case-insensitive hex compare).
            expected_asset: When set, the quoted ``asset`` must equal this
                token address (case-insensitive hex compare).
            max_timeout_seconds: Reject challenges claiming a payment
                window wider than this (default 3600s, design F-2).
        """
        self._provider = provider
        self._session_budget = session_budget
        self._expected_pay_to = expected_pay_to
        self._expected_asset = expected_asset
        self._max_timeout_seconds = max_timeout_seconds

    def quote(
        self, url: str, *, method: str = "GET", body: str | None = None
    ) -> X402Quote:
        """Fetch the 402 challenge for ``url``.

        Read-only: never triggers wallet creation (design F-3 — the
        provider's ``x402_quote`` does not call ``_ensure_wallet``).
        """
        data = self._provider.x402_quote(url, method=method, body=body)
        return X402Quote.from_cli(data)

    def request(
        self,
        url: str,
        *,
        max_payment: int,
        method: str = "GET",
        body: str | None = None,
        prefer_method: str | None = None,
        auto_approve: bool = False,
    ) -> X402PaymentResult:
        """Fetch ``url``, paying up to ``max_payment`` atomic units.

        Flow: quote → pick route → five-point precheck → reserve budget →
        ``twak x402 request`` pinned to the prechecked route → result built
        from the quoted terms (the CLI surfaces no settlement receipt,
        gaps S-7).

        TOCTOU note: twak re-discovers the challenge between our quote and
        its payment. ``--prefer-network``/``--prefer-asset`` pin the route
        and ``--max-payment`` caps the damage, narrowing (not eliminating)
        the window in which the server could swap terms.
        """
        quoted = self.quote(url, method=method, body=body)
        if not quoted.accepts:
            raise X402NoPayableRouteError(
                f"no payable route for {url}: the quote's accepts list is "
                "empty (the twak client filters out routes on chains it "
                "cannot pay)"
            )
        option = next((o for o in quoted.accepts if o.preferred), quoted.accepts[0])

        # --- five-point precheck on the quoted terms (design §3.2) -------
        # 1. payTo: byte-equal vs the caller's committed recipient.
        if (
            self._expected_pay_to is not None
            and option.pay_to.lower() != self._expected_pay_to.lower()
        ):
            raise X402RecipientMismatchError(
                f"quoted payTo {option.pay_to} != expected "
                f"{self._expected_pay_to}"
            )
        # 2. asset: for EIP-3009 the asset address IS the EIP-712 domain
        #    verifyingContract — this is the SigningPolicy domain allowlist
        #    check relocated to the quote terms (the payload itself never
        #    crosses the process boundary).
        if (
            self._expected_asset is not None
            and option.asset.lower() != self._expected_asset.lower()
        ):
            raise X402PolicyError(
                f"quoted asset {option.asset} != expected "
                f"{self._expected_asset} (asset is the EIP-712 "
                "verifyingContract for EIP-3009 routes)"
            )
        # 3. amount: per-call cap (re-enforced below by twak --max-payment).
        if option.amount > max_payment:
            raise X402AmountExceededError(
                f"quoted amount {option.amount} exceeds max_payment "
                f"{max_payment} for {option.asset}"
            )
        # 4. claimed validity window (when the option carries one).
        #    Default 3600s, NOT SigningPolicy's 600s (design F-2): twak sets
        #    the actual signing window internally; we only bound the claim.
        if (
            option.max_timeout_seconds is not None
            and option.max_timeout_seconds > self._max_timeout_seconds
        ):
            raise X402PolicyError(
                f"quoted maxTimeoutSeconds {option.max_timeout_seconds} "
                f"exceeds the configured cap {self._max_timeout_seconds}"
            )
        # 5. network/asset route pinning: passed through to the request as
        #    --prefer-network/--prefer-asset below (TOCTOU narrowing — twak
        #    re-discovers the challenge and must land on the route we
        #    prechecked). The quote's CAIP-2 network string ("eip155:56")
        #    is passed verbatim — twak accepts CAIP or chain key.

        # Session budget: reserve the QUOTED amount before the slow CLI
        # call and roll back on failure (reserve/rollback keeps the
        # check-and-increment atomic while the payment runs outside the
        # lock). We debit the quoted amount because the CLI surfaces no
        # settlement receipt to reconcile against (gaps S-7).
        if self._session_budget is not None:
            self._session_budget.reserve(option.asset, option.amount)
        try:
            response = self._provider.x402_request(
                url,
                max_payment=max_payment,
                method=method,
                body=body,
                prefer_network=option.network,
                prefer_asset=option.asset,
                prefer_method=prefer_method,
                auto_approve=auto_approve,
            )
        except Exception:
            if self._session_budget is not None:
                self._session_budget.rollback(option.asset, option.amount)
            raise

        return X402PaymentResult(
            success=True,
            response=response,
            amount=option.amount,
            asset=option.asset,
            network=option.network,
            pay_to=option.pay_to,
            transaction=self._extract_tx_hash(response),
        )

    @staticmethod
    def _extract_tx_hash(response: Any) -> str | None:
        """Best-effort tx hash from the endpoint body (most bodies have none)."""
        if isinstance(response, dict):
            tx = response.get("tx_hash") or response.get("txHash")
            return str(tx) if tx else None
        return None
