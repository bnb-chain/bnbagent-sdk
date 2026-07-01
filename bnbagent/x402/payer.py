"""X402Payer — the delegated-payment seam (design §3.2, P5).

Where :class:`~bnbagent.x402.signer.X402Signer` exposes the *signing
primitive* ("sign these bytes"), ``X402Payer`` is the seam one level up:
"handle this payment" — the abstraction proven by a decade of L402/aperture
(out-of-process lnd payments behind a maxCost guard) and by the official
x402 ``on_payment_required`` escape hatch. Wallets whose payment machinery
lives outside the SDK (an external CLI, a custodial API) plug in here as a
whole, instead of pretending to be byte signers.

The contract is distilled from two real implementations (the
second-implementation discipline): studio's ``fetch_with_payment`` buyer and
the ``twak x402 quote/request`` CLI client. Implementations may accept extra
keyword arguments beyond the Protocol's signatures (e.g. route-preference
hints); callers relying only on the Protocol stay portable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class X402PaymentOption:
    """One payable route from a 402 challenge (a quote ``accepts`` entry).

    Field-verified against ``twak x402 quote --json`` output (design §10.2):
    the CLI pre-filters routes its client cannot pay, so every option here
    is nominally payable by the backing wallet.
    """

    #: CAIP-2 network identifier, e.g. ``"eip155:56"``.
    network: str
    #: x402 scheme, e.g. ``"exact"``.
    scheme: str
    #: Token contract address. For EIP-3009 routes this address is also the
    #: EIP-712 domain ``verifyingContract``.
    asset: str
    token_name: str | None
    #: Price in atomic token units (parsed from the CLI's decimal string).
    amount: int
    pay_to: str
    #: e.g. ``"eip3009"`` or ``"permit2"``.
    transfer_method: str | None
    #: The challenge's claimed payment-validity window, in seconds.
    max_timeout_seconds: int | None
    preferred: bool
    requires_approval: bool
    description: str | None

    @classmethod
    def from_cli(cls, entry: dict) -> X402PaymentOption:
        """Map a camelCase CLI ``accepts`` entry; missing optionals → None/False."""
        timeout = entry.get("maxTimeoutSeconds")
        return cls(
            network=str(entry["network"]),
            scheme=str(entry.get("scheme", "exact")),
            asset=str(entry["asset"]),
            token_name=entry.get("tokenName"),
            amount=int(entry["amount"]),
            pay_to=str(entry["payTo"]),
            transfer_method=entry.get("transferMethod"),
            max_timeout_seconds=int(timeout) if timeout is not None else None,
            preferred=bool(entry.get("preferred", False)),
            requires_approval=bool(entry.get("requiresApproval", False)),
            description=entry.get("description"),
        )


@dataclass(frozen=True)
class X402Quote:
    """A parsed 402 challenge: the resource plus its payable routes.

    ``accepts`` may be empty — the quoting client filters out routes on
    chains it cannot pay (field-verified: x402.org on base-sepolia yields
    ``accepts: []`` from the twak client).
    """

    url: str
    description: str | None
    mime_type: str | None
    accepts: tuple[X402PaymentOption, ...]
    summary: str | None
    #: The raw parsed CLI/HTTP quote JSON, for fields not modeled here.
    raw: dict

    @classmethod
    def from_cli(cls, data: dict) -> X402Quote:
        resource = data.get("resource") or {}
        return cls(
            url=str(resource.get("url", "")),
            description=resource.get("description"),
            mime_type=resource.get("mimeType"),
            accepts=tuple(
                X402PaymentOption.from_cli(entry)
                for entry in data.get("accepts") or ()
            ),
            summary=data.get("summary"),
            raw=data,
        )


@dataclass(frozen=True)
class X402PaymentResult:
    """Outcome of a delegated x402 payment.

    ``response`` is the paid endpoint's response body **verbatim** —
    arbitrary JSON, exactly what an unpaid request would have returned.

    The payment metadata fields are Optional **by design**: the twak CLI
    does not surface a settlement receipt in stdout (gaps S-7 — the
    ``PAYMENT-RESPONSE`` header is consumed internally and only echoed
    human-readably on stderr). Delegated payers therefore fill
    ``amount``/``asset``/``network``/``pay_to`` from the **quoted** option
    they paid against, not from settlement. ``transaction`` is best-effort:
    some endpoints echo a tx hash in their own body (e.g. pieverse's
    ``tx_hash``), most don't.
    """

    success: bool
    #: The endpoint's response body, verbatim.
    response: Any
    amount: int | None = None
    asset: str | None = None
    network: str | None = None
    pay_to: str | None = None
    transaction: str | None = None


@runtime_checkable
class X402Payer(Protocol):
    """Structural contract for delegated x402 payment backends.

    Two methods, aligned with both the CLI verbs and x402 semantics —
    deliberately not ``pay()``: a cache hit on ``request`` may not pay at
    all. Implementations may accept extra keyword arguments.
    """

    def quote(
        self, url: str, *, method: str = "GET", body: str | None = None
    ) -> X402Quote:
        """Fetch the 402 challenge for ``url`` without paying."""
        ...

    def request(
        self,
        url: str,
        *,
        max_payment: int,
        method: str = "GET",
        body: str | None = None,
    ) -> X402PaymentResult:
        """Fetch ``url``, completing an x402 payment up to ``max_payment``
        atomic units if challenged."""
        ...
