"""ERC-8183 Protocol — AgenticCommerce kernel + EvaluatorRouter + OptimisticPolicy.

Public surface:

- ``ERC8183Client``   — high-level facade (most callers).
- ``CommerceClient`` / ``RouterClient`` / ``PolicyClient`` — sub-clients for
  users who need direct access to a single layer.
- ``Job`` / ``JobStatus`` / ``Verdict`` — shared types.
- ``NegotiationHandler`` — off-chain negotiation helpers.
- ``JobDescription`` / ``DeliverableManifest`` — canonical schema classes for
  on-chain description and off-chain deliverable JSON.
"""

from __future__ import annotations

from .client import DEFAULT_APPROVE_FLOOR_UNITS, ERC8183Client
from .commerce import CommerceClient
from .constants import get_erc8183_config
from .module import ERC8183Module, create_module
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
from .schema import SCHEMA_VERSION, DeliverableManifest, JobDescription
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
    "ERC8183Client",
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
    # Schema
    "JobDescription",
    "DeliverableManifest",
    "SCHEMA_VERSION",
    # Module
    "get_erc8183_config",
    "ERC8183Module",
    "create_module",
]
