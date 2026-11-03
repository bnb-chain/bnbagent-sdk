"""Read-only ``RegistryPort`` / ``CommercePort`` / ``PolicyPort`` over ``eth_call`` and logs.

Writes are refused: registry and policy writes raise ``ChainIntegrationError(code="read_only")``
and ``submit_result`` returns ``SubmitResult(success=False, error_code="read_only")``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ...chain.manifest import manifest_hash, same_hash
from ...exceptions import ChainIntegrationError
from ...ports import DeliverableView, JobStatus, JobView, StoragePort, SubmitResult
from . import abi
from .client import JsonRpcClient, from_hex
from .storage import decode_data_uri, read_only

ZERO_ADDRESS = "0x" + "00" * 20
ZERO_HASH = "0x" + "00" * 32
# IACP.JobStatus order (bnbagent.erc8183.types.JobStatus).
JOB_STATUSES: tuple[JobStatus, ...] = (
    "open",
    "funded",
    "submitted",
    "completed",
    "rejected",
    "expired",
)
LOG_WINDOW = 10  # blocks either side of the submission block (as the SDK)


def _address(value: Any) -> str:
    return str(value).lower()


def _nonzero(address: str) -> str | None:
    return None if int(address, 16) == 0 else address


async def _download(storage: StoragePort, uri: str) -> dict[str, Any]:
    if uri.startswith("data:"):
        return decode_data_uri(uri)
    return await storage.download(uri)


class _Contract:
    def __init__(self, rpc: JsonRpcClient, address: str, abi_name: str):
        self.rpc = rpc
        self.address = address
        self.abi = abi.load(abi_name)

    async def view(self, fn: str, *args: Any, to: str | None = None) -> tuple[Any, ...]:
        data = await self.rpc.eth_call(to or self.address, self.abi.encode_call(fn, *args))
        return self.abi.decode_output(fn, data)

    async def view_or_none(self, fn: str, *args: Any) -> tuple[Any, ...] | None:
        """Like ``view`` but a revert (e.g. a nonexistent token) yields ``None``."""
        try:
            return await self.view(fn, *args)
        except ChainIntegrationError as exc:
            if exc.code == "reverted":
                return None
            raise


class JsonRpcRegistry:
    """Read-only ``RegistryPort`` (ERC-8004 identity registry)."""

    def __init__(
        self, rpc: JsonRpcClient, *, chain_id: int, registry_address: str, storage: StoragePort
    ):
        self.chain_id = chain_id
        self.registry_address = registry_address
        self.storage = storage
        self._c = _Contract(rpc, registry_address, abi.IDENTITY)

    async def get_agent_uri(self, agent_id: int) -> str | None:
        out = await self._c.view_or_none("tokenURI", agent_id)
        return (out[0] or None) if out else None

    async def read_registration(self, agent_id: int) -> dict[str, Any] | None:
        uri = await self.get_agent_uri(agent_id)
        return None if uri is None else await _download(self.storage, uri)

    async def get_metadata(self, agent_id: int, key: str) -> str | None:
        out = await self._c.view_or_none("getMetadata", agent_id, key)
        if not out or not out[0]:
            return None
        try:
            return bytes(out[0]).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ChainIntegrationError(
                f"metadata {key!r} is not UTF-8", code="bad_response"
            ) from exc

    async def owner_of(self, agent_id: int) -> str | None:
        out = await self._c.view_or_none("ownerOf", agent_id)
        return _nonzero(_address(out[0])) if out else None

    async def set_agent_uri(self, agent_id: int, uri: str) -> str:
        raise read_only("set_agent_uri")

    async def set_metadata(self, agent_id: int, key: str, value: str) -> str:
        raise read_only("set_metadata")


class JsonRpcPolicy:
    """Read-only ``PolicyPort`` (OptimisticPolicy)."""

    def __init__(self, rpc: JsonRpcClient, *, policy_address: str):
        self.policy_address = policy_address
        self._c = _Contract(rpc, policy_address, abi.POLICY)

    async def dispute_window(self) -> int:
        return int((await self._c.view("disputeWindow"))[0])

    async def vote_window(self) -> int | None:
        return None  # OptimisticPolicy has no voting deadline

    async def is_disputed(self, job_id: int) -> bool:
        return bool((await self._c.view("disputed", job_id))[0])

    async def is_voter(self, address: str) -> bool:
        return bool((await self._c.view("isVoter", address))[0])

    async def has_voted(self, job_id: int, address: str) -> bool:
        return bool((await self._c.view("hasVoted", job_id, address))[0])

    async def dispute(self, job_id: int) -> str:
        raise read_only("dispute")

    async def vote_reject(self, job_id: int) -> str:
        raise read_only("vote_reject")


def job_view(raw: tuple[Any, ...]) -> JobView:
    """``getJob`` tuple ``(id, client, provider, evaluator, description, budget, expiredAt,
    status, hook, submittedAt, deliverable)`` as a ``JobView``."""
    job_id, client, provider, evaluator, description, budget, expired_at, status = raw[:8]
    submitted_at, deliverable = raw[9], raw[10]
    if not 0 <= status < len(JOB_STATUSES):
        raise ChainIntegrationError(f"unknown job status {status}", code="bad_response")
    digest = "0x" + bytes(deliverable).hex()
    return JobView(
        job_id=int(job_id),
        client=_address(client),
        provider=_address(provider),
        status=JOB_STATUSES[status],
        deliverable=None if digest == ZERO_HASH else digest,
        submitted_at=int(submitted_at) or None,
        expired_at=int(expired_at) or None,
        description=description or None,
        budget=int(budget),
        evaluator=_nonzero(_address(evaluator)),
    )


class JsonRpcCommerce:
    """Read-only ``CommercePort`` (ERC-8183 AgenticCommerce + the policy's submission log)."""

    def __init__(
        self,
        rpc: JsonRpcClient,
        *,
        chain_id: int,
        commerce_address: str,
        policy_address: str,
        storage: StoragePort,
        router_address: str | None = None,
    ):
        self.chain_id = chain_id
        self.commerce_address = commerce_address
        self.policy_address = policy_address
        self.router_address = router_address
        self.storage = storage
        self.rpc = rpc
        self._c = _Contract(rpc, commerce_address, abi.COMMERCE)
        self._policy_abi = abi.load(abi.POLICY)
        self._router = _Contract(rpc, router_address, abi.ROUTER) if router_address else None

    async def get_job(self, job_id: int) -> JobView:
        (raw,) = await self._c.view("getJob", job_id)
        if int(raw[0]) == 0 and int(raw[1], 16) == 0:
            raise ChainIntegrationError(f"no job {job_id}", code="not_found")
        return job_view(raw)

    async def _policy_for(self, job_id: int) -> str:
        if self._router is not None:
            out = await self._router.view_or_none("jobPolicy", job_id)
            if out and int(out[0], 16) != 0:
                return _address(out[0])
        return self.policy_address

    async def _first_block_at_or_after(self, timestamp: int, head: int) -> int:
        low, high = 0, head
        while low < high:
            mid = (low + high) // 2
            block = await self.rpc.eth_getBlockByNumber(mid)
            if not block:
                raise ChainIntegrationError(f"block {mid} not found", code="bad_response")
            if from_hex(block["timestamp"]) < timestamp:
                low = mid + 1
            else:
                high = mid
        return low

    async def get_deliverable_url(self, job_id: int) -> str | None:
        """``deliverable_url`` from the ``optParams`` of the policy's ``JobInitialised`` log.

        Like the SDK: locate the submission block from ``submittedAt`` by binary search over
        block timestamps, then scan a few blocks around it. ``None`` while unsubmitted or when
        the submission carries no usable URL; a submitted job without a log raises (retryable),
        since that means the RPC returned an incomplete result.
        """
        return await self._deliverable_url(await self.get_job(job_id))

    async def _deliverable_url(self, job: JobView) -> str | None:
        job_id = job.job_id
        if not job.submitted_at:
            return None
        head = await self.rpc.head()
        hint = await self._first_block_at_or_after(job.submitted_at, head)
        policy = await self._policy_for(job_id)
        logs = await self.rpc.eth_getLogs(
            {
                "address": policy,
                "topics": [self._policy_abi.event_topic("JobInitialised"), abi.topic_uint(job_id)],
                "fromBlock": max(0, hint - LOG_WINDOW),
                "toBlock": min(head, hint + LOG_WINDOW),
            }
        )
        if not logs:
            raise ChainIntegrationError(
                f"no JobInitialised log for submitted job {job_id} near block {hint}",
                code="log_missing",
                retryable=True,
            )
        raw = self._policy_abi.decode_event("JobInitialised", logs[0]).get("optParams", b"")
        try:
            params = json.loads(bytes(raw).decode("utf-8")) if raw else None
        except (UnicodeDecodeError, ValueError):
            return None
        url = params.get("deliverable_url") if isinstance(params, Mapping) else None
        return url if isinstance(url, str) and url else None

    async def get_deliverable(self, job_id: int) -> DeliverableView | None:
        job = await self.get_job(job_id)
        url = await self._deliverable_url(job)
        if url is None:
            return None
        manifest = await _download(self.storage, url)
        digest = manifest_hash(manifest)
        response = manifest.get("response")
        response = response if isinstance(response, Mapping) else {}
        metadata = manifest.get("metadata")
        return DeliverableView(
            url=url,
            content=str(response.get("content", "")),
            content_type=str(response.get("content_type", "text/plain")),
            metadata=metadata if isinstance(metadata, Mapping) else {},
            manifest=manifest,
            manifest_hash=digest,
            verified=same_hash(digest, job.deliverable),
        )

    async def submit_result(
        self, job_id: int, content: str, metadata: Mapping[str, Any] | None = None
    ) -> SubmitResult:
        return SubmitResult(
            success=False,
            error_code="read_only",
            error="the JSON-RPC adapter is read-only; use integrations.bnbagent to submit",
        )


__all__ = ["JOB_STATUSES", "JsonRpcCommerce", "JsonRpcPolicy", "JsonRpcRegistry", "job_view"]
