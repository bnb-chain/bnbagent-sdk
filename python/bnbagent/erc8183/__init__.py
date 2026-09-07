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

from ..exceptions import JobPaymentTokenMismatchError
from .client import DEFAULT_APPROVE_FLOOR_UNITS, ERC8183Client, TokenMetadata
from .commerce import CommerceClient
from .constants import get_erc8183_config
from .job_ops import (
    ERR_JOB_TOKEN_MISMATCH,
    ERR_QUOTE_INVALID,
    ERC8183JobOps,
    funded_job_watcher,
)
from .negotiation import (
    NegotiationHandler,
    NegotiationRequest,
    NegotiationResponse,
    NegotiationResult,
    QuoteSigningError,
    ReasonCode,
    TermSpecification,
)
from .policy import PolicyClient
from .quote_verify import QuoteSignatureVerdict, verify_quote_signature
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
    "TokenMetadata",
    "JobPaymentTokenMismatchError",
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
    "QuoteSigningError",
    # Schema
    "JobDescription",
    "DeliverableManifest",
    "SCHEMA_VERSION",
    # Headless provider primitives
    "ERC8183JobOps",
    "funded_job_watcher",
    "ERR_QUOTE_INVALID",
    "ERR_JOB_TOKEN_MISMATCH",
    "QuoteSignatureVerdict",
    "verify_quote_signature",
    # Per-network defaults
    "get_erc8183_config",
]
