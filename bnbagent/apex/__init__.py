"""APEX Protocol v1 — AgenticCommerce kernel + EvaluatorRouter + OptimisticPolicy.

Public surface:

- ``APEXClient``   — high-level facade (most callers).
- ``CommerceClient`` / ``RouterClient`` / ``PolicyClient`` — sub-clients for
  users who need direct access to a single layer.
- ``Job`` / ``JobStatus`` / ``Verdict`` — shared types.
- ``NegotiationHandler`` / ``ServiceRecord`` — off-chain negotiation helpers.
"""

from __future__ import annotations

from .client import DEFAULT_APPROVE_FLOOR_UNITS, APEXClient
from .commerce import CommerceClient
from .constants import get_apex_config
from .module import APEXModule, create_module
from .negotiation import (
    NegotiationHandler,
    NegotiationRequest,
    NegotiationResponse,
    NegotiationResult,
    ReasonCode,
    TermSpecification,
)
from .policy import PolicyClient
from .router import RouterClient
from .service_record import (
    NegotiationTerms,
    OnChainReferences,
    RequestData,
    ResponseData,
    ServiceRecord,
    TimestampData,
)
from .types import (
    REASON_APPROVED,
    REASON_REJECTED,
    ZERO_ADDRESS,
    ZERO_REASON,
    Job,
    JobStatus,
    Verdict,
)

__all__ = [
    # Facade + sub-clients
    "APEXClient",
    "CommerceClient",
    "RouterClient",
    "PolicyClient",
    "DEFAULT_APPROVE_FLOOR_UNITS",
    # Types
    "Job",
    "JobStatus",
    "Verdict",
    "REASON_APPROVED",
    "REASON_REJECTED",
    "ZERO_ADDRESS",
    "ZERO_REASON",
    # Negotiation
    "NegotiationRequest",
    "NegotiationResponse",
    "TermSpecification",
    "ReasonCode",
    "NegotiationHandler",
    "NegotiationResult",
    # Service record
    "ServiceRecord",
    "RequestData",
    "ResponseData",
    "NegotiationTerms",
    "TimestampData",
    "OnChainReferences",
    # Module
    "get_apex_config",
    "APEXModule",
    "create_module",
]
