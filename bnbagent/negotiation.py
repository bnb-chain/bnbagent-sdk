"""
Negotiation data structures aligned with ACP (Agent Commerce Protocol).

V1 implements single-round HTTP negotiation:
  User sends requirements + quality standards → Agent returns price or rejects.

The TermSpecification follows ACP's structured terms:
  Agreed Service + Constraints + Compensation + Evaluation.
"""

from dataclasses import dataclass, field
from typing import Optional, List


class ReasonCode:
    """ACP standard rejection codes (aligned with whitepaper + PRD FR-06)."""

    PRICE_TOO_LOW = "0x01"
    DEADLINE_TOO_TIGHT = "0x02"
    INCAPABLE = "0x03"
    AMBIGUOUS_TERMS = "0x04"
    BUSY = "0x05"
    UNSUPPORTED = "0x06"


@dataclass
class TermSpecification:
    """
    ACP protocol term specification — the core output of negotiation.
    Shared between V1 (single-round HTTP) and V2 (multi-round Memo + on-chain PoA).

    Fields map to ACP's four categories:
      - Agreed Service: service_type, deliverables, quality_standards, success_criteria
      - Constraints: deadline_seconds
      - Compensation: price, currency
      - Evaluation: evaluation_required, evaluator_type
    """

    service_type: str
    deliverables: str
    quality_standards: str

    success_criteria: Optional[List[str]] = None

    deadline_seconds: Optional[int] = None

    price: Optional[str] = None
    currency: Optional[str] = None

    evaluation_required: bool = True
    evaluator_type: str = "uma_oov3"

    def to_dict(self) -> dict:
        result = {
            "service_type": self.service_type,
            "deliverables": self.deliverables,
            "quality_standards": self.quality_standards,
            "deadline_seconds": self.deadline_seconds,  # Always include (null if not set)
            "evaluation_required": self.evaluation_required,
            "evaluator_type": self.evaluator_type,
        }
        if self.success_criteria is not None:
            result["success_criteria"] = self.success_criteria
        if self.price is not None:
            result["price"] = self.price
        if self.currency is not None:
            result["currency"] = self.currency
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "TermSpecification":
        return cls(
            service_type=data["service_type"],
            deliverables=data["deliverables"],
            quality_standards=data["quality_standards"],
            success_criteria=data.get("success_criteria"),
            deadline_seconds=data.get("deadline_seconds"),
            price=data.get("price"),
            currency=data.get("currency"),
            evaluation_required=data.get("evaluation_required", True),
            evaluator_type=data.get("evaluator_type", "uma_oov3"),
        )


@dataclass
class NegotiationRequest:
    """
    User → Agent: pricing inquiry.

    User fills in task_description and terms (with quality_standards as the
    non-negotiable baseline). Agent must agree to standards before quoting.

    The request_hash is computed by the Client and anchored on-chain at
    createJobAndLock to prevent post-hoc tampering of the request.
    """

    task_description: str
    terms: TermSpecification

    context_urls: Optional[List[str]] = None
    request_id: Optional[str] = None

    def to_dict(self) -> dict:
        """Return the request content (without hash)."""
        result = {
            "task_description": self.task_description,
            "terms": self.terms.to_dict(),
        }
        if self.context_urls:
            result["context_urls"] = self.context_urls
        if self.request_id:
            result["request_id"] = self.request_id
        return result

    def compute_hash(self) -> str:
        """
        Compute keccak256 hash of the canonical request for on-chain anchoring.
        Returns hex string with 0x prefix.
        """
        import json
        from web3 import Web3

        canonical_json = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        h = Web3.keccak(text=canonical_json).hex()
        return h if h.startswith("0x") else "0x" + h

    def to_envelope(self) -> dict:
        """
        Return wrapped structure with request content and its hash.

        {
            "request": { task_description, terms, ... },
            "request_hash": "0x..."
        }
        """
        return {
            "request": self.to_dict(),
            "request_hash": self.compute_hash(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NegotiationRequest":
        return cls(
            task_description=data["task_description"],
            terms=TermSpecification.from_dict(data["terms"]),
            context_urls=data.get("context_urls"),
            request_id=data.get("request_id"),
        )

    @classmethod
    def from_envelope(cls, data: dict) -> tuple["NegotiationRequest", str]:
        """
        Parse from envelope structure { request: {...}, request_hash: "0x..." }.
        Returns (NegotiationRequest, request_hash).
        """
        request_data = data.get("request", data)
        request_hash = data.get("request_hash", "")
        return cls.from_dict(request_data), request_hash


@dataclass
class NegotiationResponse:
    """
    Agent → User: pricing response.

    If accepted, Agent fills in price/currency in terms.
    Agent may adjust deadline_seconds and success_criteria but NOT quality_standards.

    The response_hash is computed by the Agent and anchored on-chain by the Client
    at createJobAndLock to prevent post-hoc tampering of agreed terms.
    """

    accepted: bool

    terms: Optional[TermSpecification] = None
    estimated_completion_seconds: Optional[int] = None

    reason_code: Optional[str] = None
    reason: Optional[str] = None

    def to_dict(self) -> dict:
        """Return the response content (without hash)."""
        result: dict = {"accepted": self.accepted}
        if self.terms is not None:
            result["terms"] = self.terms.to_dict()
        if self.estimated_completion_seconds is not None:
            result["estimated_completion_seconds"] = self.estimated_completion_seconds
        if self.reason_code is not None:
            result["reason_code"] = self.reason_code
        if self.reason is not None:
            result["reason"] = self.reason
        return result

    def to_envelope(self) -> dict:
        """
        Return wrapped structure with response content and its hash.
        The hash is of the response content, so they are at different layers.

        {
            "response": { accepted, terms, ... },
            "response_hash": "0x..."
        }
        """
        return {
            "response": self.to_dict(),
            "response_hash": self.compute_hash(),
        }

    def compute_hash(self) -> str:
        """
        Compute keccak256 hash of the canonical response for on-chain anchoring.
        Returns hex string with 0x prefix.
        """
        import json
        from web3 import Web3

        canonical_data = {
            "accepted": self.accepted,
        }
        if self.terms is not None:
            canonical_data["terms"] = self.terms.to_dict()
        if self.estimated_completion_seconds is not None:
            canonical_data["estimated_completion_seconds"] = self.estimated_completion_seconds

        canonical_json = json.dumps(canonical_data, sort_keys=True, separators=(",", ":"))
        h = Web3.keccak(text=canonical_json).hex()
        return h if h.startswith("0x") else "0x" + h

    @classmethod
    def from_dict(cls, data: dict) -> "NegotiationResponse":
        terms = None
        if data.get("terms"):
            terms = TermSpecification.from_dict(data["terms"])
        return cls(
            accepted=data["accepted"],
            terms=terms,
            estimated_completion_seconds=data.get("estimated_completion_seconds"),
            reason_code=data.get("reason_code"),
            reason=data.get("reason"),
        )

    @classmethod
    def from_envelope(cls, data: dict) -> tuple["NegotiationResponse", str]:
        """
        Parse from envelope structure { response: {...}, response_hash: "0x..." }.
        Returns (NegotiationResponse, response_hash).
        """
        response_data = data.get("response", data)
        response_hash = data.get("response_hash", "")
        return cls.from_dict(response_data), response_hash
