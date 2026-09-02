"""x402 payment layer — signing primitives and delegated payers.

Public API::

    from bnbagent.x402 import (
        X402Signer,
        X402Payer,
        X402Quote,
        X402PaymentOption,
        X402PaymentResult,
        TwakX402Payer,
        SessionBudgetTracker,
        X402SignerError,
        X402RecipientMismatchError,
        X402AmountExceededError,
        X402BudgetExhaustedError,
        X402PolicyError,
        X402NoPayableRouteError,
    )
"""

from __future__ import annotations

from .assets import (
    B402WalletRoute,
    ExpectedB402Asset,
    require_b402_wallet_route,
    resolve_b402_asset,
)
from .budget import SessionBudgetTracker
from .errors import (
    UnsupportedWalletRouteError,
    X402AmountExceededError,
    X402BudgetExhaustedError,
    X402NoPayableRouteError,
    X402PolicyError,
    X402RecipientMismatchError,
    X402SignerError,
)
from .payer import (
    X402Payer,
    X402PaymentOption,
    X402PaymentResult,
    X402Quote,
    expected_asset_from_payment_option,
)
from .signer import X402Signer
from .twak import TwakX402Payer

__all__ = [
    "X402Signer",
    "X402Payer",
    "X402Quote",
    "X402PaymentOption",
    "X402PaymentResult",
    "TwakX402Payer",
    "SessionBudgetTracker",
    "X402SignerError",
    "X402RecipientMismatchError",
    "X402AmountExceededError",
    "X402BudgetExhaustedError",
    "X402PolicyError",
    "X402NoPayableRouteError",
    "ExpectedB402Asset",
    "B402WalletRoute",
    "UnsupportedWalletRouteError",
    "resolve_b402_asset",
    "require_b402_wallet_route",
    "expected_asset_from_payment_option",
]
