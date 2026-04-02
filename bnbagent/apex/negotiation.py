"""
Negotiation data structures and handler aligned with APEX (Agent Payment Exchange Protocol).

V1 implements single-round HTTP negotiation:
  User sends requirements + quality standards → Agent returns price or rejects.

The TermSpecification follows APEX's structured terms:
  Agreed Service + Constraints + Compensation + Evaluation.

NegotiationHandler provides a ready-to-use negotiation processor for agents:
  handler = NegotiationHandler(service_price="20e18", currency="0x...")
  result = handler.negotiate(request_data)

On-chain Description (v1 schema)
---------------------------------
build_job_description(result.to_dict()) produces a compact JSON string for
createJob(). It embeds the full agreed terms + provider signature so neither
party can tamper with the negotiation record after the job is on-chain.

  {
    "v": 1,
    "negotiated_at": <unix ts>,
    "quote_expires_at": <unix ts>,
    "task": "<task_description>",
    "terms": { "service_type", "deliverables", "quality_standards",
               "success_criteria"?, "deadline_seconds" },
    "price": "<wei>",
    "currency": "<token address>",
    "negotiation_hash": "0x...",   # keccak256 of above (without hash/sig fields)
    "provider_sig": "0x..."         # EIP-191 signature over negotiation_hash
  }

UMA dispute voters read job.description verbatim from the assertion claim.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import APEXClient
    from ..wallets.wallet_provider import WalletProvider


class ReasonCode:
    """APEX standard rejection codes (aligned with whitepaper + PRD FR-06)."""

    PRICE_TOO_LOW = "0x01"
    DEADLINE_TOO_TIGHT = "0x02"
    INCAPABLE = "0x03"
    AMBIGUOUS_TERMS = "0x04"
    BUSY = "0x05"
    UNSUPPORTED = "0x06"


@dataclass
class TermSpecification:
    """
    APEX protocol term specification — the core output of negotiation.
    Shared between V1 (single-round HTTP) and V2 (multi-round Memo + on-chain PoA).

    Fields map to APEX's four categories:
      - Agreed Service: service_type, deliverables, quality_standards, success_criteria
      - Constraints: deadline_seconds
      - Compensation: price, currency
      - Evaluation: evaluation_required, evaluator_type
    """

    service_type: str
    deliverables: str
    quality_standards: str

    success_criteria: list[str] | None = None

    deadline_seconds: int | None = None

    price: str | None = None
    currency: str | None = None

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
    def from_dict(cls, data: dict) -> TermSpecification:
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

    context_urls: list[str] | None = None
    request_id: str | None = None

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
    def from_dict(cls, data: dict) -> NegotiationRequest:
        return cls(
            task_description=data["task_description"],
            terms=TermSpecification.from_dict(data["terms"]),
            context_urls=data.get("context_urls"),
            request_id=data.get("request_id"),
        )

    @classmethod
    def from_envelope(cls, data: dict) -> tuple[NegotiationRequest, str]:
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

    terms: TermSpecification | None = None
    estimated_completion_seconds: int | None = None
    quote_expires_at: int | None = None

    reason_code: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict:
        """Return the response content (without hash)."""
        result: dict = {"accepted": self.accepted}
        if self.terms is not None:
            result["terms"] = self.terms.to_dict()
        if self.estimated_completion_seconds is not None:
            result["estimated_completion_seconds"] = self.estimated_completion_seconds
        if self.quote_expires_at is not None:
            result["quote_expires_at"] = self.quote_expires_at
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
        from web3 import Web3

        canonical_data: dict = {
            "accepted": self.accepted,
        }
        if self.terms is not None:
            canonical_data["terms"] = self.terms.to_dict()
        if self.estimated_completion_seconds is not None:
            canonical_data["estimated_completion_seconds"] = self.estimated_completion_seconds
        if self.quote_expires_at is not None:
            canonical_data["quote_expires_at"] = self.quote_expires_at

        canonical_json = json.dumps(canonical_data, sort_keys=True, separators=(",", ":"))
        h = Web3.keccak(text=canonical_json).hex()
        return h if h.startswith("0x") else "0x" + h

    @classmethod
    def from_dict(cls, data: dict) -> NegotiationResponse:
        terms = None
        if data.get("terms"):
            terms = TermSpecification.from_dict(data["terms"])
        return cls(
            accepted=data["accepted"],
            terms=terms,
            estimated_completion_seconds=data.get("estimated_completion_seconds"),
            quote_expires_at=data.get("quote_expires_at"),
            reason_code=data.get("reason_code"),
            reason=data.get("reason"),
        )

    @classmethod
    def from_envelope(cls, data: dict) -> tuple[NegotiationResponse, str]:
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
    negotiation_hash: str = ""
    provider_sig: str = ""

    @property
    def accepted(self) -> bool:
        """Whether the negotiation was accepted."""
        return self.response.get("accepted", False)

    def to_dict(self) -> dict:
        """Return the full negotiation envelope."""
        result = {
            "request": self.request,
            "request_hash": self.request_hash,
            "response": self.response,
            "response_hash": self.response_hash,
        }
        if self.negotiation_hash:
            result["negotiation_hash"] = self.negotiation_hash
        if self.provider_sig:
            result["provider_sig"] = self.provider_sig
        return result


def _sanitize_for_claim(s: str) -> str:
    """
    Sanitize a string for embedding in the UMA assertion claim.

    Replaces [ and ] with ( and ) to prevent injection into the UMA claim's
    section markers ([REQUEST], [RESPONSE], [VERIFY]). Also strips null bytes
    and ASCII control characters (except tab/newline which are benign in JSON).
    """
    if not isinstance(s, str):
        return str(s)
    result = s.replace("[", "(").replace("]", ")")
    # Strip ASCII control chars (0x00–0x1F) except tab (0x09) and newline (0x0A)
    result = "".join(ch for ch in result if ord(ch) >= 0x20 or ch in ("\t", "\n"))
    return result


def _build_description_content(negotiation_result: dict) -> dict:
    """
    Extract and sanitize the signable content from a negotiation result dict.

    Returns the content dict (without negotiation_hash and provider_sig) that
    is used as input to keccak256 for the negotiation_hash.
    """
    response = negotiation_result.get("response", {})
    request = negotiation_result.get("request", {})

    if not response.get("accepted"):
        raise ValueError("Cannot build description from a rejected negotiation")

    response_terms = response.get("terms", {})
    price = response_terms.get("price") or ""
    currency = response_terms.get("currency") or ""

    if not price:
        raise ValueError("Negotiation response missing price")
    if not currency:
        raise ValueError("Negotiation response missing currency")

    # Build terms section (service/quality fields only, no price/currency)
    terms: dict = {
        "service_type": _sanitize_for_claim(response_terms.get("service_type", "")),
        "deliverables": _sanitize_for_claim(response_terms.get("deliverables", "")),
        "quality_standards": _sanitize_for_claim(response_terms.get("quality_standards", "")),
        "deadline_seconds": response_terms.get("deadline_seconds"),
    }
    success_criteria = response_terms.get("success_criteria")
    if success_criteria:
        terms["success_criteria"] = [_sanitize_for_claim(c) for c in success_criteria]

    negotiated_at = negotiation_result.get("negotiated_at") or response.get("negotiated_at") or int(time.time())
    quote_expires_at = negotiation_result.get("quote_expires_at") or response.get("quote_expires_at")

    content: dict = {
        "v": 1,
        "negotiated_at": negotiated_at,
        "task": _sanitize_for_claim(request.get("task_description", "")),
        "terms": terms,
        "price": price,
        "currency": currency,
    }
    if quote_expires_at is not None:
        content["quote_expires_at"] = quote_expires_at

    return content


def build_job_description(negotiation_result: dict, max_length: int = 2000) -> str:
    """
    Build a compact JSON description string for createJob() from a negotiation result.

    The description is stored on-chain in Job.description and is embedded verbatim
    in the UMA assertion claim so dispute voters can see the agreed terms directly.

    The provider_sig (if present) allows anyone to verify the provider agreed to
    these exact terms: ecrecover(negotiation_hash, provider_sig) == job.provider.

    Args:
        negotiation_result: Dict from NegotiationResult.to_dict() or the HTTP
                            /negotiate endpoint response.
        max_length: Maximum byte length of the output string (default 2000).
                    If exceeded, the task field is truncated.

    Returns:
        Compact JSON string suitable for createJob(description=...).

    Raises:
        ValueError: If the negotiation was not accepted or required fields are missing.
    """
    content = _build_description_content(negotiation_result)

    # Append negotiation_hash and provider_sig from the result
    negotiation_hash = negotiation_result.get("negotiation_hash", "")
    provider_sig = negotiation_result.get("provider_sig", "")
    if negotiation_hash:
        content["negotiation_hash"] = negotiation_hash
    if provider_sig:
        content["provider_sig"] = provider_sig

    description = json.dumps(content, sort_keys=True, separators=(",", ":"))

    # Truncate task field if over max_length
    if len(description) > max_length:
        overage = len(description) - max_length
        task = content.get("task", "")
        if len(task) > overage + 3:
            content["task"] = task[: len(task) - overage - 3] + "..."
            description = json.dumps(content, sort_keys=True, separators=(",", ":"))

    return description


def parse_job_description(description: str) -> dict | None:
    """
    Parse a structured on-chain job description (schema v1+).

    Returns the parsed dict if the description is a valid structured JSON with
    a 'v' version field, or None for legacy plain-text descriptions.

    Args:
        description: The job.description string from on-chain.

    Returns:
        Parsed dict, or None if not a structured description.
    """
    if not description or not description.strip().startswith("{"):
        return None
    try:
        parsed = json.loads(description)
        if isinstance(parsed, dict) and "v" in parsed:
            return parsed
        return None
    except (json.JSONDecodeError, ValueError):
        return None


class NegotiationHandler:
    """
    Ready-to-use negotiation handler for agents.

    Encapsulates the common negotiation logic:
    - Validates incoming requests
    - Checks service type support
    - Validates required fields (quality_standards)
    - Returns properly structured response with hashes
    - Signs the negotiation hash with the agent's wallet (if wallet_provider set)

    Example:
        handler = NegotiationHandler(
            service_price="20000000000000000000",  # 20 tokens (18 decimals)
            currency="0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565",
            wallet_provider=wallet,               # enables provider_sig
            quote_ttl_seconds=3600,               # quote valid for 1 hour
        )

        # Or auto-fetch currency from contract:
        handler = NegotiationHandler.from_apex_client(
            apex_client=apex_client,
            service_price="20000000000000000000",
            supported_service_types=["blockchain-news"],
        )

        # In your /negotiate endpoint:
        result = handler.negotiate(request_data)
        return result.to_dict()
    """

    def __init__(
        self,
        service_price: str,
        currency: str,
        supported_service_types: list[str] | None = None,
        estimated_completion_seconds: int = 120,
        require_quality_standards: bool = True,
        wallet_provider: WalletProvider | None = None,
        quote_ttl_seconds: int = 3600,
    ):
        """
        Initialize the negotiation handler.

        Args:
            service_price: Price in token smallest unit (e.g., "20000000000000000000" for 20 tokens)
            currency: BEP20 token contract address
            supported_service_types: List of supported service types (None = accept all)
            estimated_completion_seconds: Estimated time to complete the service
            require_quality_standards: Whether to require quality_standards in request
            wallet_provider: Wallet for signing negotiation_hash. When set, the
                             NegotiationResult will include provider_sig allowing
                             clients to verify the agent agreed to the terms.
            quote_ttl_seconds: How long the price quote is valid (default: 3600s = 1h).
                               Sets quote_expires_at = now + quote_ttl_seconds.
        """
        self._service_price = service_price
        self._currency = currency
        self._supported_types: set[str] | None = None
        if supported_service_types:
            self._supported_types = {t.lower() for t in supported_service_types}
        self._estimated_completion = estimated_completion_seconds
        self._require_quality_standards = require_quality_standards
        self._wallet_provider = wallet_provider
        self._quote_ttl_seconds = quote_ttl_seconds

    @classmethod
    def from_apex_client(
        cls,
        apex_client: APEXClient,
        service_price: str,
        supported_service_types: list[str] | None = None,
        estimated_completion_seconds: int = 120,
        require_quality_standards: bool = True,
        wallet_provider: WalletProvider | None = None,
        quote_ttl_seconds: int = 3600,
    ) -> NegotiationHandler:
        """
        Create a NegotiationHandler with currency fetched from the ERC-8183 contract.

        Args:
            apex_client: APEXClient instance for on-chain queries
            service_price: Price in token smallest unit
            supported_service_types: List of supported service types
            estimated_completion_seconds: Estimated completion time
            require_quality_standards: Whether to require quality_standards
            wallet_provider: Wallet for signing negotiation results
            quote_ttl_seconds: Quote validity period in seconds

        Returns:
            NegotiationHandler with currency from contract

        Example:
            from bnbagent import APEXClient, NegotiationHandler
            from web3 import Web3

            w3 = Web3(Web3.HTTPProvider(os.environ["RPC_URL"]))
            apex = APEXClient(w3, os.environ["ERC8183_ADDRESS"])

            handler = NegotiationHandler.from_apex_client(
                apex_client=apex,
                service_price=os.environ["SERVICE_PRICE"],
                supported_service_types=["translation"],
            )
        """
        currency = apex_client.payment_token()

        return cls(
            service_price=service_price,
            currency=currency,
            supported_service_types=supported_service_types,
            estimated_completion_seconds=estimated_completion_seconds,
            require_quality_standards=require_quality_standards,
            wallet_provider=wallet_provider,
            quote_ttl_seconds=quote_ttl_seconds,
        )

    @staticmethod
    def _ensure_hex_prefix(h: str) -> str:
        """Ensure hash has 0x prefix."""
        return h if h.startswith("0x") else "0x" + h

    def negotiate(self, request_data: dict) -> NegotiationResult:
        """
        Process a negotiation request and return the result.

        If wallet_provider is set, the result includes:
          - negotiation_hash: keccak256 of the canonical description content
          - provider_sig: EIP-191 signature over negotiation_hash

        Args:
            request_data: The incoming request dict (task_description, terms, ...)

        Returns:
            NegotiationResult with request, request_hash, response, response_hash,
            and (if wallet configured) negotiation_hash + provider_sig.
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
                reason=(
                    f"Unsupported service type: {service_type}."
                    f" Supported: {', '.join(sorted(self._supported_types))}"
                ),
            )

        if self._require_quality_standards and not req.terms.quality_standards:
            return self._reject(
                request_data=req.to_dict(),
                request_hash=request_hash,
                reason_code=ReasonCode.AMBIGUOUS_TERMS,
                reason="quality_standards is required in terms.",
            )

        now = int(time.time())
        quote_expires_at = now + self._quote_ttl_seconds

        response_terms = TermSpecification(
            service_type=req.terms.service_type,
            deliverables=req.terms.deliverables,
            quality_standards=req.terms.quality_standards,
            success_criteria=req.terms.success_criteria,
            deadline_seconds=req.terms.deadline_seconds,
            price=self._service_price,
            currency=self._currency,
        )

        response = NegotiationResponse(
            accepted=True,
            terms=response_terms,
            estimated_completion_seconds=self._estimated_completion,
            quote_expires_at=quote_expires_at,
        )

        response_hash = self._ensure_hex_prefix(response.compute_hash())

        # Build partial result to compute negotiation_hash
        partial_result = NegotiationResult(
            request=req.to_dict(),
            request_hash=request_hash,
            response=response.to_dict(),
            response_hash=response_hash,
        )
        partial_dict = partial_result.to_dict()
        partial_dict["negotiated_at"] = now

        negotiation_hash = ""
        provider_sig = ""

        if self._wallet_provider:
            try:
                from web3 import Web3

                content = _build_description_content(partial_dict)
                canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
                h = Web3.keccak(text=canonical).hex()
                negotiation_hash = h if h.startswith("0x") else "0x" + h

                sig_result = self._wallet_provider.sign_message(negotiation_hash)
                sig_bytes = sig_result.get("signature", b"")
                provider_sig = (
                    sig_bytes.hex()
                    if isinstance(sig_bytes, (bytes, bytearray))
                    else str(sig_bytes)
                )
                if provider_sig and not provider_sig.startswith("0x"):
                    provider_sig = "0x" + provider_sig
            except Exception:
                # Signing failure is non-fatal; proceed without sig
                negotiation_hash = ""
                provider_sig = ""

        # Store negotiated_at in the response dict for build_job_description
        response_dict = response.to_dict()
        response_dict["negotiated_at"] = now

        return NegotiationResult(
            request=req.to_dict(),
            request_hash=request_hash,
            response=response_dict,
            response_hash=response_hash,
            negotiation_hash=negotiation_hash,
            provider_sig=provider_sig,
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
