"""SigningPolicy — declarative ruleset for guarding EIP-712 typed-data signing.

Public API::

    from bnbagent.signing import (
        SigningPolicy,
        PolicyViolation,
        check,
        infer_primary_type,
        # named type sets
        EIP3009_TYPES,
        PERMIT_UNBOUNDED_TYPES,
        PERMIT2_SIGNATURE_TRANSFER_TYPES,
    )
"""

from __future__ import annotations

from .checks import check, infer_primary_type
from .errors import PolicyViolation
from .policy import (
    EIP3009_TYPES,
    PERMIT2_SIGNATURE_TRANSFER_TYPES,
    PERMIT_UNBOUNDED_TYPES,
    SigningPolicy,
)

__all__ = [
    "SigningPolicy",
    "PolicyViolation",
    "check",
    "infer_primary_type",
    "EIP3009_TYPES",
    "PERMIT_UNBOUNDED_TYPES",
    "PERMIT2_SIGNATURE_TRANSFER_TYPES",
]
