"""Canonical schema definitions for APEX v1 on-chain and off-chain data structures.

Two public classes:

- ``JobDescription``      — structured form of ``job.description`` stored on-chain.
- ``DeliverableManifest`` — structured form of the off-chain deliverable JSON whose
                             URL is passed as ``submit(optParams)``.

Both classes are versioned. ``from_dict`` / ``from_str`` raise ``ValueError`` on an
unrecognised ``version`` so indexers fail loudly on format changes rather than
silently misreading fields.

On-chain hash contract
----------------------
``DeliverableManifest.manifest_hash()`` returns the ``bytes32`` that the provider
passes to ``AgenticCommerceUpgradeable.submit(jobId, deliverable, optParams)``:

    deliverable (bytes32) = keccak256(canonical manifest JSON)  # full manifest commitment
    optParams   (bytes)   = deliverable_url (UTF-8)              # retrieval pointer

The canonical form is ``json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))``
— deterministic across all platforms. Verifiers (voters, indexers) reproduce it by
fetching the manifest JSON and applying the same serialisation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from eth_utils import keccak

SCHEMA_VERSION = 1
_SUPPORTED_VERSIONS = {SCHEMA_VERSION}


# ---------------------------------------------------------------------------
# DeliverableManifest
# ---------------------------------------------------------------------------


@dataclass
class DeliverableManifest:
    """Off-chain deliverable JSON uploaded to storage after ``submit``.

    Fields
    ------
    version       : Schema version. Currently ``1``.
    job_id        : On-chain job id.
    chain_id      : EVM chain id (e.g. 97 for BSC testnet).
    contracts     : Addresses of ``{ commerce, router, policy }`` at submit time.
    response      : ``{ content: str, content_type: str }`` — the actual delivery.
    submitted_at  : Unix timestamp (seconds) when ``submit`` was called.
    metadata      : Arbitrary extra fields. Open for extensions; bump version when
                    a field becomes required.
    """

    version: int
    job_id: int
    chain_id: int
    contracts: dict[str, str]
    response: dict[str, str]
    submitted_at: int
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ hash

    def manifest_hash(self) -> bytes:
        """Return ``keccak256(canonical manifest JSON)`` as 32 bytes.

        This is the ``deliverable`` bytes32 passed to
        ``AgenticCommerceUpgradeable.submit``. Verifiers (voters, indexers)
        reproduce it by fetching the manifest from the URL in ``optParams``
        and calling ``DeliverableManifest.from_dict(fetched).manifest_hash()``.
        """
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return keccak(text=canonical)

    def verify(self, on_chain_hash: bytes) -> bool:
        """Return ``True`` if ``on_chain_hash`` matches ``manifest_hash()``."""
        return self.manifest_hash() == on_chain_hash

    # ---------------------------------------------------------- serialisation

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "job_id": self.job_id,
            "chain_id": self.chain_id,
            "contracts": self.contracts,
            "response": self.response,
            "submitted_at": self.submitted_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DeliverableManifest:
        version = d["version"]
        if version not in _SUPPORTED_VERSIONS:
            raise ValueError(
                f"Unsupported DeliverableManifest version {version!r}. "
                f"Supported: {sorted(_SUPPORTED_VERSIONS)}"
            )
        response = d["response"]
        if "content" not in response:
            raise ValueError("DeliverableManifest.response must contain 'content'")
        for field in ("job_id", "chain_id", "contracts", "submitted_at"):
            if field not in d:
                raise ValueError(f"DeliverableManifest missing required field: '{field}'")
        return cls(
            version=version,
            job_id=d["job_id"],
            chain_id=d["chain_id"],
            contracts=d["contracts"],
            response=response,
            submitted_at=d["submitted_at"],
            metadata=d.get("metadata", {}),
        )


# ---------------------------------------------------------------------------
# JobDescription
# ---------------------------------------------------------------------------


@dataclass
class JobDescription:
    """Structured form of ``job.description`` stored on-chain at ``createJob``.

    Built by ``bnbagent.apex.negotiation.build_job_description`` and parsed
    back by ``JobDescription.from_str``.

    Fields
    ------
    version          : Schema version. Currently ``1``.
    negotiated_at    : Unix timestamp when negotiation completed.
    task             : Human-readable task description (sanitised for on-chain).
    terms            : ``{ deliverables, quality_standards, success_criteria? }``
    price            : Agreed price in token smallest unit (string to avoid overflow).
    currency         : Payment token address.
    quote_expires_at : Optional quote expiry timestamp.
    negotiation_hash : Optional keccak256 of canonical negotiation content (0x-prefixed).
    provider_sig     : Optional EIP-191 provider signature over ``negotiation_hash``.
    """

    version: int
    negotiated_at: int
    task: str
    terms: dict[str, Any]
    price: str
    currency: str
    quote_expires_at: int | None = None
    negotiation_hash: str | None = None
    provider_sig: str | None = None

    # ---------------------------------------------------------- serialisation

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "version": self.version,
            "negotiated_at": self.negotiated_at,
            "task": self.task,
            "terms": self.terms,
            "price": self.price,
            "currency": self.currency,
        }
        if self.quote_expires_at is not None:
            d["quote_expires_at"] = self.quote_expires_at
        if self.negotiation_hash is not None:
            d["negotiation_hash"] = self.negotiation_hash
        if self.provider_sig is not None:
            d["provider_sig"] = self.provider_sig
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> JobDescription:
        version = d["version"]
        if version not in _SUPPORTED_VERSIONS:
            raise ValueError(
                f"Unsupported JobDescription version {version!r}. "
                f"Supported: {sorted(_SUPPORTED_VERSIONS)}"
            )
        return cls(
            version=version,
            negotiated_at=d["negotiated_at"],
            task=d["task"],
            terms=d["terms"],
            price=d["price"],
            currency=d["currency"],
            quote_expires_at=d.get("quote_expires_at"),
            negotiation_hash=d.get("negotiation_hash"),
            provider_sig=d.get("provider_sig"),
        )

    @classmethod
    def from_str(cls, description: str) -> JobDescription | None:
        """Parse a ``job.description`` string.

        Returns ``None`` for plain-text descriptions (legacy or unstructured).
        Returns ``None`` if the JSON has no ``version`` field.
        Raises ``ValueError`` if the version is present but unsupported.
        """
        if not description or not description.strip().startswith("{"):
            return None
        try:
            parsed = json.loads(description)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(parsed, dict) or "version" not in parsed:
            return None
        return cls.from_dict(parsed)
