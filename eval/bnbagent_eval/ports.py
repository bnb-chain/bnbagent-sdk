"""Ports: the only interface between bnbagent-eval and a chain / SDK.

Chain workflows (``chain/``), the ``erc8004`` target, ``onchain.*`` metrics and the server
depend on these Protocols and neutral value types — never on SDK types. Adapters in
``integrations/`` implement them: ``integrations.bnbagent`` (bnbagent SDK, read/write) and
``integrations.jsonrpc`` (plain JSON-RPC, read-only). ``bnbagent_eval.testing.fakes`` provides
in-memory implementations for tests and examples.

Chain-facing methods are ``async``; signing is synchronous (pure computation). ``PayerPort``
mirrors the SDK's synchronous ``X402Payer.request`` so an SDK payer can be used directly.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

JobStatus = Literal["open", "funded", "submitted", "completed", "rejected", "expired"]

# --------------------------------------------------------------------------- value types


@dataclass(frozen=True)
class JobView:
    """An ERC-8183 job, independent of any SDK."""

    job_id: int
    client: str
    provider: str
    status: JobStatus
    deliverable: str | None = None  # 0x-prefixed bytes32 manifest hash once submitted
    submitted_at: int | None = None  # unix seconds
    expired_at: int | None = None  # unix seconds
    description: str | None = None  # raw on-chain description (JSON text for SDK jobs)
    budget: int | None = None
    evaluator: str | None = None


@dataclass(frozen=True)
class DeliverableView:
    """A job's off-chain deliverable, checked against the on-chain hash."""

    url: str
    content: str
    content_type: str
    metadata: Mapping[str, Any]
    manifest: Mapping[str, Any]
    manifest_hash: str  # 0x-prefixed keccak of the canonical manifest
    verified: bool  # manifest_hash == job.deliverable


@dataclass(frozen=True)
class SubmitResult:
    success: bool
    error_code: str | None = None
    error: str | None = None
    retryable: bool = False
    tx_hash: str | None = None
    deliverable_url: str | None = None


@dataclass(frozen=True)
class AssetSpec:
    """A payment token and how it can be paid (x402)."""

    chain_id: int
    address: str
    symbol: str
    decimals: int
    methods: tuple[str, ...]  # "eip3009" and/or "permit2-exact"
    eip712_domain: Mapping[str, Any] | None = None  # name / version for EIP-3009


# --------------------------------------------------------------------------- ports


@runtime_checkable
class RegistryPort(Protocol):
    """ERC-8004 identity registry."""

    chain_id: int
    registry_address: str

    async def get_agent_uri(self, agent_id: int) -> str | None: ...

    async def read_registration(self, agent_id: int) -> dict[str, Any] | None:
        """The decoded registration file (data:, https: or ipfs: URIs)."""
        ...

    async def set_agent_uri(self, agent_id: int, uri: str) -> str:
        """Returns the transaction hash."""
        ...

    async def set_metadata(self, agent_id: int, key: str, value: str) -> str: ...

    async def get_metadata(self, agent_id: int, key: str) -> str | None: ...

    async def owner_of(self, agent_id: int) -> str | None: ...


@runtime_checkable
class CommercePort(Protocol):
    """ERC-8183 commerce kernel (jobs, deliverables, submission)."""

    chain_id: int
    commerce_address: str

    async def get_job(self, job_id: int) -> JobView: ...

    async def get_deliverable_url(self, job_id: int) -> str | None: ...

    async def get_deliverable(self, job_id: int) -> DeliverableView | None:
        """Fetch the deliverable manifest and verify it against the on-chain hash."""
        ...

    async def submit_result(
        self, job_id: int, content: str, metadata: Mapping[str, Any] | None = None
    ) -> SubmitResult: ...


@runtime_checkable
class PolicyPort(Protocol):
    """ERC-8183 optimistic policy (disputes and voting)."""

    async def dispute_window(self) -> int:
        """Seconds after submission during which the client may dispute."""
        ...

    async def vote_window(self) -> int | None:
        """Seconds after a dispute during which voters may vote (None: no limit)."""
        ...

    async def is_disputed(self, job_id: int) -> bool: ...

    async def is_voter(self, address: str) -> bool: ...

    async def has_voted(self, job_id: int, address: str) -> bool: ...

    async def dispute(self, job_id: int) -> str: ...

    async def vote_reject(self, job_id: int) -> str: ...


@runtime_checkable
class StoragePort(Protocol):
    """Off-chain storage of JSON documents (IPFS, HTTPS, local)."""

    async def upload(self, data: Mapping[str, Any], name: str) -> str:
        """Returns the URI."""
        ...

    async def download(self, uri: str) -> dict[str, Any]: ...

    async def exists(self, uri: str) -> bool: ...


@runtime_checkable
class SignerPort(Protocol):
    """An EVM signer. ``supports`` capabilities: ``sign.typed_data``, ``sign.message``."""

    address: str

    def supports(self, capability: str) -> bool: ...

    def sign_typed_data(self, typed_data: Mapping[str, Any]) -> str:
        """EIP-712 full message (``types``, ``primaryType``, ``domain``, ``message``) → 0x sig."""
        ...

    def sign_message(self, message: bytes) -> str:
        """EIP-191 personal_sign over raw bytes → 0x signature."""
        ...


@runtime_checkable
class ChainReaderPort(Protocol):
    """Read-only chain access (receipts, balances, calls, logs) at an optional pinned block."""

    chain_id: int

    async def block_number(self) -> int: ...

    async def get_transaction(self, tx_hash: str) -> dict[str, Any] | None: ...

    async def get_receipt(self, tx_hash: str) -> dict[str, Any] | None: ...

    async def get_balance(
        self, address: str, *, token: str | None = None, block: int | None = None
    ) -> int: ...

    async def call(self, to: str, data: str, *, block: int | None = None) -> str: ...

    async def get_logs(self, filter: Mapping[str, Any]) -> list[dict[str, Any]]: ...


@runtime_checkable
class SettlementPort(Protocol):
    """Settles x402 payments (facilitator or direct transferWithAuthorization)."""

    async def authorization_state(self, asset: str, payer: str, nonce: str) -> bool:
        """True if the EIP-3009 authorization nonce was already used on-chain."""
        ...

    async def settle(self, payment: Mapping[str, Any]) -> str:
        """Returns the settlement transaction hash."""
        ...


@runtime_checkable
class AssetCatalogPort(Protocol):
    def assets(self, chain_id: int) -> Sequence[AssetSpec]: ...


@runtime_checkable
class PayerPort(Protocol):
    """Structurally identical to the SDK's ``X402Payer.request`` (synchronous).

    ``max_payment`` is an integer in the payment token's atomic units (e.g. ``100_000`` for
    0.10 of a 6-decimal token), not a decimal string.
    """

    def request(
        self, url: str, *, max_payment: int, method: str = "GET", body: str | None = None
    ) -> Any: ...


class StaticAssetCatalog:
    """``AssetCatalogPort`` from a fixed list: payment tokens for any EVM chain, no SDK needed."""

    def __init__(self, assets: Sequence[AssetSpec]):
        self._assets = list(assets)

    def assets(self, chain_id: int) -> list[AssetSpec]:
        return [a for a in self._assets if a.chain_id == chain_id]


@dataclass
class ChainPorts:
    """Every port in one bundle; unused ones may be ``None``."""

    registry: RegistryPort | None = None
    commerce: CommercePort | None = None
    policy: PolicyPort | None = None
    storage: StoragePort | None = None
    signer: SignerPort | None = None
    reader: ChainReaderPort | None = None
    settlement: SettlementPort | None = None
    assets: AssetCatalogPort | None = None
    payer: PayerPort | None = None
    extras: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "AssetCatalogPort",
    "AssetSpec",
    "ChainPorts",
    "ChainReaderPort",
    "CommercePort",
    "DeliverableView",
    "JobStatus",
    "JobView",
    "PayerPort",
    "PolicyPort",
    "RegistryPort",
    "SettlementPort",
    "SignerPort",
    "StoragePort",
    "SubmitResult",
]
