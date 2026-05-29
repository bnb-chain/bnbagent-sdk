"""x402 / EIP-3009 payment signing layer.

Public API::

    from bnbagent.x402 import (
        X402Signer,
        SessionBudgetTracker,
        X402SignerError,
        X402RecipientMismatchError,
        X402AmountExceededError,
        X402BudgetExhaustedError,
        X402PolicyError,
    )
"""

from __future__ import annotations

from .budget import SessionBudgetTracker
from .errors import (
    X402AmountExceededError,
    X402BudgetExhaustedError,
    X402PolicyError,
    X402RecipientMismatchError,
    X402SignerError,
)
from .signer import X402Signer

__all__ = [
    "X402Signer",
    "SessionBudgetTracker",
    "X402SignerError",
    "X402RecipientMismatchError",
    "X402AmountExceededError",
    "X402BudgetExhaustedError",
    "X402PolicyError",
]
