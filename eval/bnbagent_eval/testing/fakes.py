"""In-memory port implementations (tests, examples, offline demos). No SDK, no network."""

from __future__ import annotations

import base64
import dataclasses
import json
from collections.abc import Mapping, Sequence
from typing import Any

from ..chain.erc8004 import DATA_URI_PREFIX, encode_data_uri
from ..chain.manifest import build_manifest, manifest_hash, same_hash
from ..hashing import hash_obj, keccak_hex
from ..ports import AssetSpec, DeliverableView, JobView, StaticAssetCatalog, SubmitResult  # noqa: F401 - StaticAssetCatalog re-exported


def decode_data_uri(uri: str) -> dict[str, Any]:
    if not uri.startswith(DATA_URI_PREFIX):
        raise ValueError("not a JSON data URI")
    return json.loads(base64.b64decode(uri[len(DATA_URI_PREFIX) :]))


class MemoryStorage:
    """``StoragePort``: content-addressed ``memory://<keccak>`` URIs."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    async def upload(self, data: Mapping[str, Any], name: str) -> str:
        uri = f"memory://{hash_obj(dict(data))[2:]}/{name}"
        self.docs[uri] = json.loads(json.dumps(dict(data)))
        return uri

    async def download(self, uri: str) -> dict[str, Any]:
        if uri.startswith(DATA_URI_PREFIX):
            return decode_data_uri(uri)
        if uri not in self.docs:
            raise FileNotFoundError(uri)
        return json.loads(json.dumps(self.docs[uri]))

    async def exists(self, uri: str) -> bool:
        return uri in self.docs or uri.startswith(DATA_URI_PREFIX)


class FakeRegistry:
    """``RegistryPort`` with agents held in memory."""

    def __init__(
        self,
        *,
        chain_id: int = 97,
        registry_address: str = "0x" + "8004" * 10,
        storage: MemoryStorage | None = None,
        owner: str | None = None,
    ):
        self.chain_id = chain_id
        self.registry_address = registry_address
        self.storage = storage
        self.uris: dict[int, str] = {}
        self.metadata: dict[tuple[int, str], str] = {}
        self.owners: dict[int, str] = {}
        self.default_owner = owner
        self.transactions: list[tuple[str, Any]] = []

    def register(
        self, agent_id: int, registration: Mapping[str, Any], owner: str | None = None
    ) -> None:
        self.uris[agent_id] = encode_data_uri(registration)
        if owner or self.default_owner:
            self.owners[agent_id] = owner or self.default_owner

    async def get_agent_uri(self, agent_id: int) -> str | None:
        return self.uris.get(agent_id)

    async def read_registration(self, agent_id: int) -> dict[str, Any] | None:
        uri = self.uris.get(agent_id)
        if uri is None:
            return None
        if uri.startswith(DATA_URI_PREFIX):
            return decode_data_uri(uri)
        if self.storage is None:
            raise ValueError(f"cannot resolve {uri} without storage")
        return await self.storage.download(uri)

    async def set_agent_uri(self, agent_id: int, uri: str) -> str:
        self.uris[agent_id] = uri
        return self._tx("set_agent_uri", (agent_id, uri))

    async def set_metadata(self, agent_id: int, key: str, value: str) -> str:
        self.metadata[(agent_id, key)] = value
        return self._tx("set_metadata", (agent_id, key, value))

    async def get_metadata(self, agent_id: int, key: str) -> str | None:
        return self.metadata.get((agent_id, key))

    async def owner_of(self, agent_id: int) -> str | None:
        return self.owners.get(agent_id)

    def _tx(self, name: str, args: Any) -> str:
        self.transactions.append((name, args))
        return keccak_hex(f"{name}{len(self.transactions)}{args!r}")


class FakeCommerce:
    """``CommercePort``: jobs and deliverables in memory; manifests built as the SDK does."""

    def __init__(
        self,
        *,
        chain_id: int = 97,
        commerce_address: str = "0x" + "8183" * 10,
        router: str = "0x" + "aa" * 20,
        policy: str = "0x" + "bb" * 20,
        storage: MemoryStorage | None = None,
        clock=None,
    ):
        self.chain_id = chain_id
        self.commerce_address = commerce_address
        self.contracts = {"commerce": commerce_address, "router": router, "policy": policy}
        self.storage = storage or MemoryStorage()
        self.jobs: dict[int, JobView] = {}
        self.deliverable_urls: dict[int, str] = {}
        self.submissions: list[dict[str, Any]] = []
        self.fail_next_submit: SubmitResult | None = None
        self.clock = clock

    def add_job(self, job: JobView) -> None:
        self.jobs[job.job_id] = job

    async def get_job(self, job_id: int) -> JobView:
        if job_id not in self.jobs:
            raise KeyError(f"no job {job_id}")
        return self.jobs[job_id]

    async def get_deliverable_url(self, job_id: int) -> str | None:
        return self.deliverable_urls.get(job_id)

    async def get_deliverable(self, job_id: int) -> DeliverableView | None:
        url = self.deliverable_urls.get(job_id)
        if url is None:
            return None
        manifest = await self.storage.download(url)
        digest = manifest_hash(manifest)
        response = manifest.get("response", {})
        return DeliverableView(
            url=url,
            content=response.get("content", ""),
            content_type=response.get("content_type", "text/plain"),
            metadata=manifest.get("metadata", {}),
            manifest=manifest,
            manifest_hash=digest,
            verified=same_hash(digest, self.jobs[job_id].deliverable),
        )

    async def submit_result(
        self, job_id: int, content: str, metadata: Mapping[str, Any] | None = None
    ) -> SubmitResult:
        if self.fail_next_submit is not None:
            result, self.fail_next_submit = self.fail_next_submit, None
            return result
        job = self.jobs[job_id]
        if job.status != "funded":
            return SubmitResult(
                success=False, error_code="wrong_status", error=f"job is {job.status}"
            )
        manifest = build_manifest(
            job_id=job_id,
            chain_id=self.chain_id,
            contracts=self.contracts,
            content=content,
            metadata=metadata,
        )
        url = await self.storage.upload(manifest, f"job-{job_id}.json")
        digest = manifest_hash(manifest)
        now = int(self.clock()) if self.clock else job.submitted_at
        self.jobs[job_id] = dataclasses.replace(
            job, status="submitted", deliverable=digest, submitted_at=now
        )
        self.deliverable_urls[job_id] = url
        self.submissions.append(
            {"job_id": job_id, "content": content, "metadata": dict(metadata or {}), "url": url}
        )
        return SubmitResult(
            success=True, tx_hash=keccak_hex(f"submit{job_id}"), deliverable_url=url
        )

    def tamper(self, job_id: int, new_content: str) -> None:
        """Replace the stored deliverable content without updating the on-chain hash."""
        url = self.deliverable_urls[job_id]
        self.storage.docs[url]["response"]["content"] = new_content


class FakePolicy:
    """``PolicyPort``: optimistic policy with a whitelisted voter set."""

    def __init__(
        self,
        *,
        dispute_window: int = 3600,
        vote_window: int | None = 86400,
        voters: Sequence[str] = (),
        caller: str | None = None,
    ):
        self._dispute_window = dispute_window
        self._vote_window = vote_window
        self.voters = {v.lower() for v in voters}
        self.disputed: set[int] = set()
        self.votes: dict[int, set[str]] = {}
        self.caller = caller

    async def dispute_window(self) -> int:
        return self._dispute_window

    async def vote_window(self) -> int | None:
        return self._vote_window

    async def is_disputed(self, job_id: int) -> bool:
        return job_id in self.disputed

    async def is_voter(self, address: str) -> bool:
        return address.lower() in self.voters

    async def has_voted(self, job_id: int, address: str) -> bool:
        return address.lower() in self.votes.get(job_id, set())

    async def dispute(self, job_id: int) -> str:
        self.disputed.add(job_id)
        return keccak_hex(f"dispute{job_id}")

    async def vote_reject(self, job_id: int) -> str:
        self.votes.setdefault(job_id, set()).add((self.caller or "").lower())
        return keccak_hex(f"vote{job_id}")


class FakeChainReader:
    """``ChainReaderPort`` serving canned receipts, transactions, balances and call results."""

    def __init__(self, *, chain_id: int = 97, block: int = 1_000):
        self.chain_id = chain_id
        self.block = block
        self.receipts: dict[str, dict[str, Any]] = {}
        self.transactions: dict[str, dict[str, Any]] = {}
        self.balances: dict[tuple[str, str | None, int | None], int] = {}
        self.calls: dict[tuple[str, str], str] = {}
        self.logs: list[dict[str, Any]] = []

    async def block_number(self) -> int:
        return self.block

    async def get_transaction(self, tx_hash: str) -> dict[str, Any] | None:
        return self.transactions.get(tx_hash)

    async def get_receipt(self, tx_hash: str) -> dict[str, Any] | None:
        return self.receipts.get(tx_hash)

    async def get_balance(
        self, address: str, *, token: str | None = None, block: int | None = None
    ) -> int:
        key = (address.lower(), token.lower() if token else None, block)
        if key in self.balances:
            return self.balances[key]
        return self.balances.get((address.lower(), token.lower() if token else None, None), 0)

    async def call(self, to: str, data: str, *, block: int | None = None) -> str:
        return self.calls.get((to.lower(), data), "0x")

    async def get_logs(self, filter: Mapping[str, Any]) -> list[dict[str, Any]]:
        return list(self.logs)


class FakeSettlement:
    """``SettlementPort`` recording settlements; authorization nonces become used."""

    def __init__(self) -> None:
        self.used: set[tuple[str, str, str]] = set()
        self.settled: list[dict[str, Any]] = []

    async def authorization_state(self, asset: str, payer: str, nonce: str) -> bool:
        return (asset.lower(), payer.lower(), nonce.lower()) in self.used

    async def settle(self, payment: Mapping[str, Any]) -> str:
        auth = payment.get("authorization", {})
        self.used.add(
            (
                str(payment.get("asset", "")).lower(),
                str(auth.get("from", "")).lower(),
                str(auth.get("nonce", "")).lower(),
            )
        )
        self.settled.append(dict(payment))
        return keccak_hex(f"settle{len(self.settled)}")


class FakeSigner:
    """A ``SignerPort`` with configurable capabilities and no cryptography (tests only)."""

    def __init__(
        self,
        address: str = "0x" + "11" * 20,
        capabilities: Sequence[str] = ("sign.typed_data", "sign.message"),
    ):
        self.address = address
        self._caps = set(capabilities)

    def supports(self, capability: str) -> bool:
        return capability in self._caps

    def sign_typed_data(self, typed_data: Mapping[str, Any]) -> str:
        return keccak_hex(json.dumps(typed_data, sort_keys=True, default=str))

    def sign_message(self, message: bytes) -> str:
        return keccak_hex(message)


__all__ = [
    "FakeChainReader",
    "FakeCommerce",
    "FakePolicy",
    "FakeRegistry",
    "FakeSettlement",
    "FakeSigner",
    "MemoryStorage",
    "StaticAssetCatalog",
    "decode_data_uri",
    "encode_data_uri",
]
