"""
Negotiation data structures and handler aligned with ERC-8183 (ERC-8183 Protocol).

V1 implements single-round HTTP negotiation:
  User sends requirements + quality standards → Agent returns price or rejects.

The TermSpecification follows ERC-8183's structured terms:
  Agreed Service + Compensation + Evaluation.

NegotiationHandler provides a ready-to-use negotiation processor for agents:
  handler = NegotiationHandler(service_price="20e18", currency="0x...")
  result = handler.negotiate(request_data)

On-chain Description (v1 schema)
---------------------------------
build_job_description(result.to_dict()) produces a compact JSON string for
createJob(). It embeds the full agreed terms + provider signature so neither
party can tamper with the negotiation record after the job is on-chain.

  {
    "version": 1,
    "negotiated_at": <unix ts>,
    "quote_expires_at": <unix ts>,
    "task": "<task_description>",
    "terms": { "deliverables", "quality_standards",
               "success_criteria"? },
    "price": "<wei>",
    "currency": "<token address>",
    "negotiation_hash": "0x...",   # keccak256 of above (without hash/sig fields)
    "provider_sig": "0x..."         # EIP-191 signature over negotiation_hash
  }

UMA dispute voters read job.description verbatim from the assertion claim.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

# Maximum byte length of the on-chain job.description string. It is stored as
# a Solidity `string` (unbounded by type), so this is a self-imposed cap to
# bound createJob gas and keep the UMA assertion claim readable. ~536 bytes
# are fixed overhead (negotiation_hash, provider_sig, addresses, JSON keys);
# the remainder is user text shared across task + terms.
MAX_DESCRIPTION_BYTES = 4096


class DescriptionTooLongError(ValueError):
    """Raised by :func:`build_job_description` when the assembled on-chain
    description exceeds ``max_length``. Truncating would invalidate
    negotiation_hash / provider_sig, so the description is rejected instead."""


if TYPE_CHECKING:
    from .client import ERC8183Client
    from ..wallets.wallet_provider import WalletProvider


class ReasonCode:
    """ERC-8183 standard rejection codes (aligned with whitepaper + PRD FR-06)."""

    PRICE_TOO_LOW = "0x01"
    DEADLINE_TOO_TIGHT = "0x02"
    INCAPABLE = "0x03"
    AMBIGUOUS_TERMS = "0x04"
    BUSY = "0x05"
    UNSUPPORTED = "0x06"
    TASK_TOO_LONG = "0x07"  # task + terms exceed the on-chain description cap


@dataclass
class TermSpecification:
    """
    ERC-8183 protocol term specification — the core output of negotiation.
    Shared between V1 (single-round HTTP) and V2 (multi-round Memo + on-chain PoA).

    Fields map to ERC-8183's categories:
      - Agreed Service: deliverables, quality_standards, success_criteria
      - Compensation: price, currency
      - Evaluation: evaluation_required, evaluator_type
    """

    deliverables: str
    quality_standards: str

    success_criteria: list[str] | None = None

    price: str | None = None
    currency: str | None = None

    evaluation_required: bool = True
    evaluator_type: str = "uma_oov3"

    def to_dict(self) -> dict:
        result = {
            "deliverables": self.deliverables,
            "quality_standards": self.quality_standards,
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
            deliverables=data["deliverables"],
            quality_standards=data["quality_standards"],
            success_criteria=data.get("success_criteria"),
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
    Agent may adjust success_criteria but NOT quality_standards.

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
    chain_id: int | None = None
    verifying_contract: str | None = None

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
        if self.chain_id is not None:
            result["chain_id"] = self.chain_id
        if self.verifying_contract is not None:
            result["verifying_contract"] = self.verifying_contract
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


def _build_description_content(
    negotiation_result: dict,
    chain_id: int | None = None,
    verifying_contract: str | None = None,
) -> dict:
    """
    Extract and sanitize the signable content from a negotiation result dict.

    Returns the content dict (without negotiation_hash and provider_sig) that
    is used as input to keccak256 for the negotiation_hash.

    When ``chain_id`` and/or ``verifying_contract`` are provided, they are
    embedded in the content so the resulting signature is bound to a specific
    chain + commerce contract. This prevents replaying the same ``provider_sig``
    across EVM networks where the same provider key is configured.
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

    # Build terms section (quality fields only, no price/currency)
    terms: dict = {
        "deliverables": _sanitize_for_claim(response_terms.get("deliverables", "")),
        "quality_standards": _sanitize_for_claim(response_terms.get("quality_standards", "")),
    }
    success_criteria = response_terms.get("success_criteria")
    if success_criteria:
        terms["success_criteria"] = [_sanitize_for_claim(c) for c in success_criteria]

    negotiated_at = negotiation_result.get("negotiated_at") or response.get("negotiated_at") or int(time.time())
    quote_expires_at = negotiation_result.get("quote_expires_at") or response.get("quote_expires_at")

    content: dict = {
        "version": 1,
        "negotiated_at": negotiated_at,
        "task": _sanitize_for_claim(request.get("task_description", "")),
        "terms": terms,
        "price": price,
        "currency": currency,
    }
    if quote_expires_at is not None:
        content["quote_expires_at"] = quote_expires_at
    if chain_id is not None:
        content["chain_id"] = chain_id
    if verifying_contract is not None:
        from web3 import Web3

        content["verifying_contract"] = Web3.to_checksum_address(verifying_contract)

    return content


def build_job_description(
    negotiation_result: dict, max_length: int = MAX_DESCRIPTION_BYTES
) -> str:
    """
    Build a compact JSON description string for createJob() from a negotiation result.

    The description is stored on-chain in Job.description and is embedded verbatim
    in the UMA assertion claim so dispute voters can see the agreed terms directly.

    The provider_sig (if present) allows anyone to verify the provider agreed to
    these exact terms: ecrecover(negotiation_hash, provider_sig) == job.provider.

    Args:
        negotiation_result: Dict from NegotiationResult.to_dict() or the HTTP
                            /negotiate endpoint response.
        max_length: Maximum byte length of the output string (default
                    MAX_DESCRIPTION_BYTES). If exceeded, raises
                    DescriptionTooLongError — truncating here would change the
                    signed content and break provider_sig verification.

    Returns:
        Compact JSON string suitable for createJob(description=...).

    Raises:
        ValueError: If the negotiation was not accepted or required fields are missing.
        DescriptionTooLongError: If the assembled description exceeds max_length.
    """
    # Propagate chain_id / verifying_contract from the result so the on-chain
    # description string contains the SAME fields that were keccak'd to produce
    # negotiation_hash. Without this, downstream verifiers that re-derive the
    # hash from the on-chain JSON would always get a different value than what
    # provider_sig actually signed.
    content = _build_description_content(
        negotiation_result,
        chain_id=negotiation_result.get("chain_id"),
        verifying_contract=negotiation_result.get("verifying_contract"),
    )

    # Append negotiation_hash and provider_sig from the result
    negotiation_hash = negotiation_result.get("negotiation_hash", "")
    provider_sig = negotiation_result.get("provider_sig", "")
    if negotiation_hash:
        content["negotiation_hash"] = negotiation_hash
    if provider_sig:
        content["provider_sig"] = provider_sig

    description = json.dumps(content, sort_keys=True, separators=(",", ":"))

    if len(description) > max_length:
        raise DescriptionTooLongError(
            f"on-chain description is {len(description)} bytes, exceeds "
            f"max_length={max_length}; shorten task_description / terms. "
            f"Truncating would invalidate negotiation_hash / provider_sig."
        )

    return description


def parse_job_description(description: str) -> "JobDescription | None":
    """Parse a structured on-chain job description (schema v1+).

    Returns a ``JobDescription`` if the description is a valid structured JSON,
    or ``None`` for plain-text / unstructured descriptions.

    Args:
        description: The job.description string from on-chain.
    """
    from .schema import JobDescription
    return JobDescription.from_str(description)


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
        handler = NegotiationHandler.from_erc8183_client(
            erc8183_client=erc8183_client,
            service_price="20000000000000000000",
        )

        # In your /negotiate endpoint:
        result = handler.negotiate(request_data)
        return result.to_dict()
    """

    MAX_QUOTE_TTL_SECONDS = 300  # 5 minutes — bounds the lifetime of provider_sig

    def __init__(
        self,
        service_price: str,
        currency: str,
        estimated_completion_seconds: int = 120,
        require_quality_standards: bool = True,
        wallet_provider: WalletProvider | None = None,
        quote_ttl_seconds: int = 300,
        chain_id: int | None = None,
        verifying_contract: str | None = None,
    ):
        """
        Initialize the negotiation handler.

        Args:
            service_price: Price in token smallest unit (e.g., "20000000000000000000" for 20 tokens)
            currency: BEP20 token contract address
            estimated_completion_seconds: Estimated time to complete the service
            require_quality_standards: Whether to require quality_standards in request
            wallet_provider: Wallet for signing negotiation_hash. When set, the
                             NegotiationResult will include provider_sig allowing
                             clients to verify the agent agreed to the terms.
            quote_ttl_seconds: How long the price quote is valid (default: 300s).
                               Capped at MAX_QUOTE_TTL_SECONDS so leaked / replayed
                               provider_sig values cannot accumulate value over time.
            chain_id: When set, embedded in the signed content so the signature
                      is bound to a specific chain. Prevents cross-chain replay
                      when the same provider key is configured on multiple EVMs.
            verifying_contract: When set, embedded in the signed content to bind
                                the signature to a specific commerce contract.
                                Use :meth:`from_erc8183_client` to auto-populate
                                both fields from a live ERC-8183 client.
        """
        if not isinstance(quote_ttl_seconds, int) or isinstance(quote_ttl_seconds, bool):
            raise ValueError(
                f"quote_ttl_seconds must be int, got {type(quote_ttl_seconds).__name__}"
            )
        if quote_ttl_seconds <= 0 or quote_ttl_seconds > self.MAX_QUOTE_TTL_SECONDS:
            raise ValueError(
                f"quote_ttl_seconds must be in (0, {self.MAX_QUOTE_TTL_SECONDS}], "
                f"got {quote_ttl_seconds}"
            )

        self._service_price = service_price
        self._currency = currency
        self._estimated_completion = estimated_completion_seconds
        self._require_quality_standards = require_quality_standards
        self._wallet_provider = wallet_provider
        self._quote_ttl_seconds = quote_ttl_seconds
        self._chain_id = chain_id
        self._verifying_contract = verifying_contract

        if wallet_provider is not None and chain_id is None:
            logger.warning(
                "[NegotiationHandler] wallet_provider is set but chain_id is None; "
                "provider_sig will not be bound to a specific chain. "
                "Pass chain_id (or use from_erc8183_client) to prevent cross-chain replay."
            )

    @classmethod
    def from_erc8183_client(
        cls,
        erc8183_client: ERC8183Client,
        service_price: str,
        estimated_completion_seconds: int = 120,
        require_quality_standards: bool = True,
        wallet_provider: WalletProvider | None = None,
        quote_ttl_seconds: int = 300,
    ) -> NegotiationHandler:
        """
        Create a NegotiationHandler with currency fetched from the ERC-8183 contract.

        Args:
            erc8183_client: ERC8183Client instance for on-chain queries
            service_price: Price in token smallest unit
            estimated_completion_seconds: Estimated completion time
            require_quality_standards: Whether to require quality_standards
            wallet_provider: Wallet for signing negotiation results
            quote_ttl_seconds: Quote validity period in seconds

        Returns:
            NegotiationHandler with currency from contract

        Example:
            from bnbagent import ERC8183Client, EVMWalletProvider, NegotiationHandler

            wallet = EVMWalletProvider(password="...", private_key=os.environ["PRIVATE_KEY"])
            erc8183 = ERC8183Client(wallet, network="bsc-testnet")

            handler = NegotiationHandler.from_erc8183_client(
                erc8183_client=erc8183,
                service_price=os.environ["ERC8183_SERVICE_PRICE"],
            )
        """
        currency = erc8183_client.payment_token

        return cls(
            service_price=service_price,
            currency=currency,
            estimated_completion_seconds=estimated_completion_seconds,
            require_quality_standards=require_quality_standards,
            wallet_provider=wallet_provider,
            quote_ttl_seconds=quote_ttl_seconds,
            chain_id=erc8183_client.network.chain_id,
            verifying_contract=erc8183_client.commerce.address,
        )

    @staticmethod
    def _ensure_hex_prefix(h: str) -> str:
        """Ensure hash has 0x prefix."""
        return h if h.startswith("0x") else "0x" + h

    def negotiate(
        self,
        request_data: dict,
        *,
        price: str | None = None,
        estimated_completion_seconds: int | None = None,
    ) -> NegotiationResult:
        """
        Process a negotiation request and return the result.

        If wallet_provider is set, the result includes:
          - negotiation_hash: keccak256 of the canonical description content
          - provider_sig: EIP-191 signature over negotiation_hash

        Args:
            request_data: The incoming request dict (task_description, terms, ...)
            price: Optional per-request price (token smallest-unit uint256
                string) overriding the construction-time ``service_price`` for
                this call only. Must be seller-controlled (e.g. an effort
                estimate), NOT echoed from untrusted client input.
            estimated_completion_seconds: Optional per-request ETA override.

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

        if self._require_quality_standards and not req.terms.quality_standards:
            return self._reject(
                request_data=req.to_dict(),
                request_hash=request_hash,
                reason_code=ReasonCode.AMBIGUOUS_TERMS,
                reason="quality_standards is required in terms.",
            )

        # Per-request overrides fall back to the construction-time defaults.
        if price is not None:
            try:
                if int(price) < 0:
                    raise ValueError
            except (TypeError, ValueError):
                return self._reject(
                    request_data=req.to_dict(),
                    request_hash=request_hash,
                    reason_code=ReasonCode.AMBIGUOUS_TERMS,
                    reason=f"price must be a non-negative integer string, got {price!r}",
                )
        effective_price = price if price is not None else self._service_price
        effective_eta = (
            estimated_completion_seconds
            if estimated_completion_seconds is not None
            else self._estimated_completion
        )

        now = int(time.time())
        quote_expires_at = now + self._quote_ttl_seconds

        response_terms = TermSpecification(
            deliverables=req.terms.deliverables,
            quality_standards=req.terms.quality_standards,
            success_criteria=req.terms.success_criteria,
            price=effective_price,
            currency=self._currency,
        )

        response = NegotiationResponse(
            accepted=True,
            terms=response_terms,
            estimated_completion_seconds=effective_eta,
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

                content = _build_description_content(
                    partial_dict,
                    chain_id=self._chain_id,
                    verifying_contract=self._verifying_contract,
                )
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
            except Exception as e:
                # Signing failure is non-fatal: return the quote without a
                # provider_sig, but log so operators can detect wallet issues.
                logger.warning(
                    "[NegotiationHandler] sign_message failed: %s; "
                    "returning quote without provider_sig",
                    e,
                )
                negotiation_hash = ""
                provider_sig = ""

        # Store negotiated_at in the response dict for build_job_description
        response_dict = response.to_dict()
        response_dict["negotiated_at"] = now

        # Echo chain_id / verifying_contract into the result so build_job_description
        # writes them into the on-chain JSON. Without this, the on-chain description
        # would lack the fields that negotiation_hash was computed over, and
        # downstream verifiers couldn't reconstruct the signed digest.
        bound_chain_id = self._chain_id if negotiation_hash else None
        bound_contract = self._verifying_contract if negotiation_hash else None

        result = NegotiationResult(
            request=req.to_dict(),
            request_hash=request_hash,
            response=response_dict,
            response_hash=response_hash,
            negotiation_hash=negotiation_hash,
            provider_sig=provider_sig,
            chain_id=bound_chain_id,
            verifying_contract=bound_contract,
        )

        # Reject up front if the on-chain description would exceed the cap.
        # Measuring the fully-assembled string (incl. negotiation_hash +
        # provider_sig) means we never hand back a quote the client cannot
        # turn into a job, and never silently truncate a signed record.
        try:
            build_job_description(result.to_dict())
        except DescriptionTooLongError:
            return self._reject(
                request_data=req.to_dict(),
                request_hash=request_hash,
                reason_code=ReasonCode.TASK_TOO_LONG,
                reason=(
                    "task_description + terms exceed the on-chain description "
                    f"size limit ({MAX_DESCRIPTION_BYTES} bytes)"
                ),
            )

        return result

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
