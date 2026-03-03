"""
ServiceRecord — the complete service record stored on IPFS.

During disputes, DVM voters download this record from the dataUrl
in the ResultSubmitted event and judge service quality against
the quality_standards in negotiation_terms.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


@dataclass
class RequestData:
    """User's original request, captured for dispute evidence."""

    content: str
    content_type: str = "text/plain"
    hash: str = ""

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "content_type": self.content_type,
            "hash": self.hash,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RequestData":
        return cls(
            content=data["content"],
            content_type=data.get("content_type", "text/plain"),
            hash=data.get("hash", ""),
        )


@dataclass
class ResponseData:
    """Agent's response. For file/image results, use content_url instead of content."""

    content: str = ""
    content_type: str = "text/plain"
    content_url: Optional[str] = None
    metrics: Optional[Dict[str, Any]] = None

    def to_dict(self) -> dict:
        result: dict = {
            "content": self.content,
            "content_type": self.content_type,
        }
        if self.content_url is not None:
            result["content_url"] = self.content_url
        if self.metrics is not None:
            result["metrics"] = self.metrics
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "ResponseData":
        return cls(
            content=data.get("content", ""),
            content_type=data.get("content_type", "text/plain"),
            content_url=data.get("content_url"),
            metrics=data.get("metrics"),
        )


@dataclass
class NegotiationTerms:
    """Snapshot of the agreed ACP TermSpecification. quality_standards is the dispute anchor."""

    service_type: str = ""
    deliverables: str = ""
    quality_standards: str = ""
    success_criteria: Optional[List[str]] = None
    agreed_price: str = "0"
    currency: str = ""
    deadline_seconds: Optional[int] = None

    def to_dict(self) -> dict:
        result = {
            "service_type": self.service_type,
            "deliverables": self.deliverables,
            "quality_standards": self.quality_standards,
            "agreed_price": self.agreed_price,
            "currency": self.currency,
        }
        if self.success_criteria is not None:
            result["success_criteria"] = self.success_criteria
        if self.deadline_seconds is not None:
            result["deadline_seconds"] = self.deadline_seconds
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "NegotiationTerms":
        return cls(
            service_type=data.get("service_type", ""),
            deliverables=data.get("deliverables", ""),
            quality_standards=data.get("quality_standards", ""),
            success_criteria=data.get("success_criteria"),
            agreed_price=data.get("agreed_price", "0"),
            currency=data.get("currency", ""),
            deadline_seconds=data.get("deadline_seconds"),
        )


@dataclass
class TimestampData:
    negotiated_at: int = 0
    requested_at: int = 0
    responded_at: int = 0
    submitted_at: int = 0

    def to_dict(self) -> dict:
        return {
            "negotiated_at": self.negotiated_at,
            "requested_at": self.requested_at,
            "responded_at": self.responded_at,
            "submitted_at": self.submitted_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TimestampData":
        return cls(
            negotiated_at=data.get("negotiated_at", 0),
            requested_at=data.get("requested_at", 0),
            responded_at=data.get("responded_at", 0),
            submitted_at=data.get("submitted_at", 0),
        )


@dataclass
class OnChainReferences:
    """On-chain transaction references for cross-validation by DVM voters."""

    create_job_tx_hash: str = ""
    accept_job_tx_hash: str = ""
    submit_result_tx_hash: str = ""
    assertion_id: str = ""

    def to_dict(self) -> dict:
        result: dict = {}
        if self.create_job_tx_hash:
            result["create_job_tx_hash"] = self.create_job_tx_hash
        if self.accept_job_tx_hash:
            result["accept_job_tx_hash"] = self.accept_job_tx_hash
        if self.submit_result_tx_hash:
            result["submit_result_tx_hash"] = self.submit_result_tx_hash
        if self.assertion_id:
            result["assertion_id"] = self.assertion_id
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "OnChainReferences":
        return cls(
            create_job_tx_hash=data.get("create_job_tx_hash", ""),
            accept_job_tx_hash=data.get("accept_job_tx_hash", ""),
            submit_result_tx_hash=data.get("submit_result_tx_hash", ""),
            assertion_id=data.get("assertion_id", ""),
        )


@dataclass
class ServiceRecord:
    """
    Complete service record stored on IPFS/storage.

    Three on-chain hashes anchor this record:
      requestHash  = keccak256(request.content)   — submitted by client
      responseHash = keccak256(response.content)   — submitted by agent
      resultHash   = keccak256(canonical JSON of this entire record) — submitted by agent
    """

    version: str = "1.0"
    job_id: int = 0
    agent_id: int = 0
    chain_id: int = 0
    contract_address: str = ""
    request: Optional[RequestData] = None
    response: Optional[ResponseData] = None
    negotiation_terms: Optional[NegotiationTerms] = None
    timestamps: Optional[TimestampData] = None
    on_chain: Optional[OnChainReferences] = None

    def to_dict(self) -> dict:
        result: dict = {
            "version": self.version,
            "job_id": self.job_id,
            "agent_id": self.agent_id,
            "chain_id": self.chain_id,
            "contract_address": self.contract_address,
        }
        if self.request is not None:
            result["request"] = self.request.to_dict()
        if self.response is not None:
            result["response"] = self.response.to_dict()
        if self.negotiation_terms is not None:
            result["negotiation_terms"] = self.negotiation_terms.to_dict()
        if self.timestamps is not None:
            result["timestamps"] = self.timestamps.to_dict()
        if self.on_chain is not None:
            result["on_chain"] = self.on_chain.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "ServiceRecord":
        request = None
        if data.get("request"):
            request = RequestData.from_dict(data["request"])
        response = None
        if data.get("response"):
            response = ResponseData.from_dict(data["response"])
        terms = None
        if data.get("negotiation_terms"):
            terms = NegotiationTerms.from_dict(data["negotiation_terms"])
        ts = None
        if data.get("timestamps"):
            ts = TimestampData.from_dict(data["timestamps"])
        on_chain = None
        if data.get("on_chain"):
            on_chain = OnChainReferences.from_dict(data["on_chain"])
        return cls(
            version=data.get("version", "1.0"),
            job_id=data.get("job_id", 0),
            agent_id=data.get("agent_id", 0),
            chain_id=data.get("chain_id", 0),
            contract_address=data.get("contract_address", ""),
            request=request,
            response=response,
            negotiation_terms=terms,
            timestamps=ts,
            on_chain=on_chain,
        )
