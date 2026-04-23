"""
ServiceRecord — the complete service record stored on IPFS.

During disputes, DVM voters download this record from the dataUrl
in the ResultSubmitted event and judge service quality against
the quality_standards in negotiation_terms.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


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
    def from_dict(cls, data: dict) -> RequestData:
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
    hash: str = ""
    content_url: str | None = None
    metrics: dict[str, Any] | None = None

    def to_dict(self) -> dict:
        result: dict = {
            "content": self.content,
            "content_type": self.content_type,
        }
        if self.hash:
            result["hash"] = self.hash
        if self.content_url is not None:
            result["content_url"] = self.content_url
        if self.metrics is not None:
            result["metrics"] = self.metrics
        return result

    @classmethod
    def from_dict(cls, data: dict) -> ResponseData:
        return cls(
            content=data.get("content", ""),
            content_type=data.get("content_type", "text/plain"),
            hash=data.get("hash", ""),
            content_url=data.get("content_url"),
            metrics=data.get("metrics"),
        )


@dataclass
class NegotiationTerms:
    """Snapshot of the agreed APEX TermSpecification. quality_standards is the dispute anchor."""

    deliverables: str = ""
    quality_standards: str = ""
    success_criteria: list[str] | None = None
    agreed_price: str = "0"
    currency: str = ""

    def to_dict(self) -> dict:
        result = {
            "deliverables": self.deliverables,
            "quality_standards": self.quality_standards,
            "agreed_price": self.agreed_price,
            "currency": self.currency,
        }
        if self.success_criteria is not None:
            result["success_criteria"] = self.success_criteria
        return result

    @classmethod
    def from_dict(cls, data: dict) -> NegotiationTerms:
        return cls(
            deliverables=data.get("deliverables", ""),
            quality_standards=data.get("quality_standards", ""),
            success_criteria=data.get("success_criteria"),
            agreed_price=data.get("agreed_price", "0"),
            currency=data.get("currency", ""),
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
    def from_dict(cls, data: dict) -> TimestampData:
        return cls(
            negotiated_at=data.get("negotiated_at", 0),
            requested_at=data.get("requested_at", 0),
            responded_at=data.get("responded_at", 0),
            submitted_at=data.get("submitted_at", 0),
        )


@dataclass
class NegotiationData:
    """
    Complete negotiation phase data, including both request and response with hashes.
    These hashes are anchored on-chain at createJobAndLock to prevent tampering.
    """

    request: dict[str, Any] | None = None
    request_hash: str = ""
    response: dict[str, Any] | None = None
    response_hash: str = ""

    def to_dict(self) -> dict:
        result: dict = {}
        if self.request is not None:
            result["request"] = self.request
        if self.request_hash:
            result["request_hash"] = self.request_hash
        if self.response is not None:
            result["response"] = self.response
        if self.response_hash:
            result["response_hash"] = self.response_hash
        return result

    @classmethod
    def from_dict(cls, data: dict) -> NegotiationData:
        return cls(
            request=data.get("request"),
            request_hash=data.get("request_hash", ""),
            response=data.get("response"),
            response_hash=data.get("response_hash", ""),
        )


@dataclass
class ServiceData:
    """
    Service execution result with response content and hash.
    serviceResponseHash is anchored on-chain at submitResult.

    Note: request_content is NOT stored here because:
    - The negotiation.request already contains the task_description
    - Storing it again would be redundant
    """

    response_content: str = ""
    response_hash: str = ""

    def to_dict(self) -> dict:
        result: dict = {"response_content": self.response_content}
        if self.response_hash:
            result["response_hash"] = self.response_hash
        return result

    @classmethod
    def from_dict(cls, data: dict) -> ServiceData:
        return cls(
            response_content=data.get("response_content", ""),
            response_hash=data.get("response_hash", ""),
        )


@dataclass
class OnChainReferences:
    """On-chain transaction references for cross-validation by DVM voters."""

    approve_tx_hash: str = ""
    create_job_tx_hash: str = ""
    accept_job_tx_hash: str = ""
    submit_result_tx_hash: str = ""
    assertion_id: str = ""

    def to_dict(self) -> dict:
        return {
            "approve_tx_hash": self.approve_tx_hash,
            "create_job_tx_hash": self.create_job_tx_hash,
            "accept_job_tx_hash": self.accept_job_tx_hash,
            "submit_result_tx_hash": self.submit_result_tx_hash,
            "assertion_id": self.assertion_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> OnChainReferences:
        return cls(
            approve_tx_hash=data.get("approve_tx_hash", ""),
            create_job_tx_hash=data.get("create_job_tx_hash", ""),
            accept_job_tx_hash=data.get("accept_job_tx_hash", ""),
            submit_result_tx_hash=data.get("submit_result_tx_hash", ""),
            assertion_id=data.get("assertion_id", ""),
        )


@dataclass
class ServiceRecord:
    """
    Complete service record stored on IPFS/storage.

    Structure:
      - negotiation: Full request/response from negotiation phase with hashes
        (hashes anchored at createJobAndLock by client)
      - service: Agent's response to the task with hash
        (hash anchored at submitResult by agent)
      - timestamps: Timeline of the service lifecycle
      - on_chain: All transaction hashes for full traceability

    On-chain hash anchoring:
      Negotiation phase (anchored at createJobAndLock by client):
        - negotiationRequestHash = keccak256(negotiation.request)
        - negotiationResponseHash = keccak256(negotiation.response)

      Service phase (anchored at submitResult by agent):
        - serviceRecordHash = keccak256(canonical JSON of this entire record)
        - serviceResponseHash = keccak256(service.response_content)
    """

    version: str = "1.0"
    job_id: int = 0
    agent_id: int = 0
    chain_id: int = 0
    contract_address: str = ""
    negotiation: NegotiationData | None = None
    service: ServiceData | None = None
    timestamps: TimestampData | None = None
    on_chain: OnChainReferences | None = None

    def to_dict(self) -> dict:
        result: dict = {
            "version": self.version,
            "job_id": self.job_id,
            "agent_id": self.agent_id,
            "chain_id": self.chain_id,
            "contract_address": self.contract_address,
        }
        if self.negotiation is not None:
            result["negotiation"] = self.negotiation.to_dict()
        if self.service is not None:
            result["service"] = self.service.to_dict()
        if self.timestamps is not None:
            result["timestamps"] = self.timestamps.to_dict()
        if self.on_chain is not None:
            result["on_chain"] = self.on_chain.to_dict()
        return result

    def canonical_json(self) -> str:
        """
        Canonical JSON for hashing. Excludes post-submission fields
        (submit_result_tx_hash, assertion_id) so the hash can be computed
        before the submitResult transaction is sent.
        """
        d = self.to_dict()
        if "on_chain" in d:
            d["on_chain"].pop("submit_result_tx_hash", None)
            d["on_chain"].pop("assertion_id", None)
        return json.dumps(d, sort_keys=True, separators=(",", ":"))

    def compute_hashes(self) -> dict:
        """
        Compute hashes for on-chain verification:

        Negotiation phase (anchored at createJobAndLock):
          negotiation_request_hash  = keccak256(JSON.stringify(negotiation.request))
          negotiation_response_hash = keccak256(JSON.stringify(negotiation.response))

        Service phase (anchored at submitResult):
          service_response_hash = keccak256(service.response_content)
          service_record_hash   = keccak256(canonical_json)

        Returns dict with hex strings (with 0x prefix).
        """
        import json

        from web3 import Web3

        def ensure_0x(h: str) -> str:
            return h if h.startswith("0x") else "0x" + h

        negotiation_request_hash = ""
        negotiation_response_hash = ""
        if self.negotiation:
            if self.negotiation.request:
                negotiation_request_hash = ensure_0x(
                    Web3.keccak(
                        text=json.dumps(
                            self.negotiation.request, sort_keys=True, separators=(",", ":")
                        )
                    ).hex()
                )
                self.negotiation.request_hash = negotiation_request_hash
            if self.negotiation.response:
                negotiation_response_hash = ensure_0x(
                    Web3.keccak(
                        text=json.dumps(
                            self.negotiation.response, sort_keys=True, separators=(",", ":")
                        )
                    ).hex()
                )
                self.negotiation.response_hash = negotiation_response_hash

        service_response_hash = ""
        if self.service and self.service.response_content:
            service_response_hash = ensure_0x(
                Web3.keccak(text=self.service.response_content).hex()
            )
            self.service.response_hash = service_response_hash

        service_record_hash = ensure_0x(Web3.keccak(text=self.canonical_json()).hex())

        return {
            "negotiation_request_hash": negotiation_request_hash,
            "negotiation_response_hash": negotiation_response_hash,
            "service_response_hash": service_response_hash,
            "service_record_hash": service_record_hash,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ServiceRecord:
        negotiation = None
        if data.get("negotiation"):
            negotiation = NegotiationData.from_dict(data["negotiation"])
        service = None
        if data.get("service"):
            service = ServiceData.from_dict(data["service"])
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
            negotiation=negotiation,
            service=service,
            timestamps=ts,
            on_chain=on_chain,
        )
