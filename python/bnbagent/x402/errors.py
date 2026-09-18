"""Errors raised by X402Signer."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..networks import AssetId


class X402SignerError(Exception):
    """Base class for X402Signer-layer refusals."""


class X402RecipientMismatchError(X402SignerError):
    """``message['to']`` did not byte-equal the caller-supplied ``expected_to``.

    Forces the caller to commit to a destination address before invoking
    the signer; defends against an upstream LLM tool quietly altering the
    payee in a 402 challenge.
    """


class X402AmountExceededError(X402SignerError):
    """``message['value']`` exceeded the per-call ``max_value`` for this token."""


class X402BudgetExhaustedError(X402SignerError):
    """The session budget for this token would be exceeded by this call."""


class X402PolicyError(X402SignerError):
    """A SigningPolicy violation surfaced from the underlying wallet."""


class X402NoPayableRouteError(X402SignerError):
    """The quote's ``accepts`` list held no route this client can pay.

    The quoting client filters out routes on chains it does not support,
    so an empty list means the endpoint and the wallet share no network.
    """


class UnsupportedWalletRouteError(X402SignerError):
    """A wallet cannot pay the requested method for the exact expected asset."""

    def __init__(
        self,
        *,
        wallet_kind: str,
        network: str,
        chain_id: int,
        asset_id: AssetId,
        transfer_method: str,
    ) -> None:
        self.wallet_kind = wallet_kind
        self.network = network
        self.chain_id = chain_id
        self.asset_id = asset_id
        self.transfer_method = transfer_method
        super().__init__(
            "unsupported B402 wallet route: "
            f"wallet_kind={wallet_kind}, network={network}, chain_id={chain_id}, "
            f"asset_id={asset_id.value}, transfer_method={transfer_method}; "
            "the expected asset is fixed and no cross-asset fallback was attempted"
        )
