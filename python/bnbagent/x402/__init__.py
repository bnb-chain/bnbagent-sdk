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
    DelegatedX402ExactPayerCapability,
    ExpectedB402Asset,
    ExpectedEIP3009Route,
    require_b402_wallet_route,
    require_expected_eip3009_route,
    resolve_b402_asset,
    resolve_expected_eip3009_route,
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
    "ExpectedEIP3009Route",
    "B402WalletRoute",
    "DelegatedX402ExactPayerCapability",
    "UnsupportedWalletRouteError",
    "resolve_b402_asset",
    "resolve_expected_eip3009_route",
    "require_b402_wallet_route",
    "require_expected_eip3009_route",
    "expected_asset_from_payment_option",
]
