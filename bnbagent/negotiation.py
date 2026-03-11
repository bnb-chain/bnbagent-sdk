"""
Negotiation data structures and handler aligned with ACP (Agent Commerce Protocol).

V1 implements single-round HTTP negotiation:
  User sends requirements + quality standards → Agent returns price or rejects.

The TermSpecification follows ACP's structured terms:
  Agreed Service + Constraints + Compensation + Evaluation.

NegotiationHandler provides a ready-to-use negotiation processor for agents:
  handler = NegotiationHandler(base_price="20e18", currency="0x...")
  result = handler.negotiate(request_data)
"""

from dataclasses import dataclass, field
from typing import Optional, List, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from .apex_client import ApexClient


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


@dataclass
class NegotiationResult:
    """Result of NegotiationHandler.negotiate() containing all components needed for the flow."""

    request: dict
    request_hash: str
    response: dict
    response_hash: str

    @property
    def accepted(self) -> bool:
        """Whether the negotiation was accepted."""
        return self.response.get("accepted", False)

    def to_dict(self) -> dict:
        """Return the full negotiation envelope."""
        return {
            "request": self.request,
            "request_hash": self.request_hash,
            "response": self.response,
            "response_hash": self.response_hash,
        }


class PriceTooLowError(ValueError):
    """Raised when agent's base_price is below the contract's minimum service fee."""

    def __init__(self, base_price: int, min_service_fee: int, decimals: int = 18):
        self.base_price = base_price
        self.min_service_fee = min_service_fee
        self.decimals = decimals

        base_human = base_price / (10 ** decimals)
        min_human = min_service_fee / (10 ** decimals)
        super().__init__(
            f"base_price ({base_human:.4f} tokens) is below minimum service fee "
            f"({min_human:.4f} tokens). The minimum exists because 10% of the payment "
            f"is used as UMA bond, and UMA requires a minimum bond of "
            f"{min_human / 10:.4f} tokens."
        )


class NegotiationHandler:
    """
    Ready-to-use negotiation handler for agents.

    Encapsulates the common negotiation logic:
    - Validates incoming requests
    - Checks service type support
    - Validates required fields (quality_standards)
    - Enforces minimum price (from APEX contract)
    - Returns properly structured response with hashes

    Minimum Price Constraint:
        UMA OOv3 requires a minimum bond (currently 1 TUSD on testnet).
        The APEX contract takes 10% of the payment as bond, so:
        minServiceFee = minBond * 10 = 10 TUSD

        You can pass min_service_fee directly or use from_apex_client()
        to fetch it automatically from the contract.

    Example:
        # Option 1: Manual minimum (if you know it)
        handler = NegotiationHandler(
            base_price="20000000000000000000",  # 20 tokens (18 decimals)
            currency="0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565",
            min_service_fee=10000000000000000000,  # 10 tokens minimum
        )

        # Option 2: Auto-fetch from contract (recommended)
        handler = NegotiationHandler.from_apex_client(
            apex_client=apex_client,
            base_price="20000000000000000000",
            supported_service_types=["blockchain-news"],
        )

        # In your /negotiate endpoint:
        result = handler.negotiate(request_data)
        return result.to_dict()
    """

    def __init__(
        self,
        base_price: str,
        currency: str,
        supported_service_types: Optional[List[str]] = None,
        estimated_completion_seconds: int = 120,
        require_quality_standards: bool = True,
        min_service_fee: Optional[int] = None,
        validate_price: bool = True,
    ):
        """
        Initialize the negotiation handler.

        Args:
            base_price: Price in token smallest unit (e.g., "20000000000000000000" for 20 tokens)
            currency: ERC20 token contract address
            supported_service_types: List of supported service types (None = accept all)
            estimated_completion_seconds: Estimated time to complete the service
            require_quality_standards: Whether to require quality_standards in request
            min_service_fee: Minimum allowed service fee (from APEX contract)
            validate_price: If True and min_service_fee provided, raise PriceTooLowError
                           if base_price < min_service_fee

        Raises:
            PriceTooLowError: If validate_price=True and base_price < min_service_fee
        """
        self._base_price = base_price
        self._currency = currency
        self._min_service_fee = min_service_fee
        self._supported_types: Optional[Set[str]] = None
        if supported_service_types:
            self._supported_types = {t.lower() for t in supported_service_types}
        self._estimated_completion = estimated_completion_seconds
        self._require_quality_standards = require_quality_standards

        if validate_price and min_service_fee is not None:
            base_int = int(base_price)
            if base_int < min_service_fee:
                raise PriceTooLowError(base_int, min_service_fee)

    @classmethod
    def from_apex_client(
        cls,
        apex_client: "ApexClient",
        base_price: str,
        supported_service_types: Optional[List[str]] = None,
        estimated_completion_seconds: int = 120,
        require_quality_standards: bool = True,
        validate_price: bool = True,
    ) -> "NegotiationHandler":
        """
        Create a NegotiationHandler with min_service_fee fetched from the APEX contract.

        This is the recommended way to create a handler as it ensures your price
        meets the minimum required by the UMA bond mechanism.

        Args:
            apex_client: ApexClient instance connected to the Apex contract
            base_price: Price in token smallest unit
            supported_service_types: List of supported service types
            estimated_completion_seconds: Estimated completion time
            require_quality_standards: Whether to require quality_standards
            validate_price: If True, raise PriceTooLowError if base_price < min

        Returns:
            NegotiationHandler with min_service_fee configured

        Raises:
            PriceTooLowError: If validate_price=True and price is too low

        Example:
            from bnbagent import ApexClient, NegotiationHandler
            from web3 import Web3

            w3 = Web3(Web3.HTTPProvider(os.environ["RPC_URL"]))
            apex = ApexClient(w3, os.environ["APEX_CONTRACT_ADDRESS"])

            handler = NegotiationHandler.from_apex_client(
                apex_client=apex,
                base_price=os.environ["AGENT_PRICE"],
                supported_service_types=["translation"],
            )
        """
        min_fee = apex_client.min_service_fee()
        currency = apex_client.payment_token()

        return cls(
            base_price=base_price,
            currency=currency,
            supported_service_types=supported_service_types,
            estimated_completion_seconds=estimated_completion_seconds,
            require_quality_standards=require_quality_standards,
            min_service_fee=min_fee,
            validate_price=validate_price,
        )

    @property
    def min_service_fee(self) -> Optional[int]:
        """Return the minimum service fee (None if not configured)."""
        return self._min_service_fee

    @staticmethod
    def _ensure_hex_prefix(h: str) -> str:
        """Ensure hash has 0x prefix."""
        return h if h.startswith("0x") else "0x" + h

    def negotiate(self, request_data: dict) -> NegotiationResult:
        """
        Process a negotiation request and return the result.

        Args:
            request_data: The incoming request dict (task_description, terms, ...)

        Returns:
            NegotiationResult with request, request_hash, response, response_hash
        """
        try:
            req = NegotiationRequest.from_dict(request_data)
        except (KeyError, TypeError) as e:
            return self._reject(
                request_data=request_data,
                reason_code=ReasonCode.AMBIGUOUS_TERMS,
                reason=f"Invalid request format: {e}",
            )

        request_hash = self._ensure_hex_prefix(req.compute_hash())

        service_type = req.terms.service_type.lower()
        if self._supported_types and service_type not in self._supported_types:
            return self._reject(
                request_data=req.to_dict(),
                request_hash=request_hash,
                reason_code=ReasonCode.UNSUPPORTED,
                reason=f"Unsupported service type: {service_type}. Supported: {', '.join(sorted(self._supported_types))}",
            )

        if self._require_quality_standards and not req.terms.quality_standards:
            return self._reject(
                request_data=req.to_dict(),
                request_hash=request_hash,
                reason_code=ReasonCode.AMBIGUOUS_TERMS,
                reason="quality_standards is required in terms.",
            )

        response_terms = TermSpecification(
            service_type=req.terms.service_type,
            deliverables=req.terms.deliverables,
            quality_standards=req.terms.quality_standards,
            success_criteria=req.terms.success_criteria,
            deadline_seconds=req.terms.deadline_seconds,
            price=self._base_price,
            currency=self._currency,
        )

        response = NegotiationResponse(
            accepted=True,
            terms=response_terms,
            estimated_completion_seconds=self._estimated_completion,
        )

        response_hash = self._ensure_hex_prefix(response.compute_hash())

        return NegotiationResult(
            request=req.to_dict(),
            request_hash=request_hash,
            response=response.to_dict(),
            response_hash=response_hash,
        )

    def _reject(
        self,
        request_data: dict,
        reason_code: str,
        reason: str,
        request_hash: str = "",
    ) -> NegotiationResult:
        """Build a rejection response."""
        response = NegotiationResponse(
            accepted=False,
            reason_code=reason_code,
            reason=reason,
        )
        response_hash = self._ensure_hex_prefix(response.compute_hash()) if request_hash else ""
        return NegotiationResult(
            request=request_data,
            request_hash=request_hash,
            response=response.to_dict(),
            response_hash=response_hash,
        )
