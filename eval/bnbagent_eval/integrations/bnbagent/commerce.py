"""``CommercePort`` and ``PolicyPort`` over ``ERC8183Client`` / ``ERC8183JobOps``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...chain.manifest import manifest_hash, parse_manifest, same_hash
from ...exceptions import ChainIntegrationError
from ...ports import DeliverableView, JobView, SubmitResult
from . import compat
from ._convert import ZERO_ADDRESS, job_view
from ._errors import NOT_FOUND, READ_ONLY, call, to_chain_error, tx_hash
from .storage import UriResolver

__all__ = ["BnbagentCommerce", "BnbagentPolicy"]


class BnbagentCommerce:
    """``CommercePort``: reads via ``ERC8183Client``, submission via ``ERC8183JobOps``.

    ``job_ops`` is ``None`` for read-only use; ``submit_result`` then fails with ``read_only``.
    """

    def __init__(
        self, client: Any, *, job_ops: Any | None = None, resolver: UriResolver | None = None
    ):
        self.client = client
        self.job_ops = job_ops
        self.resolver = resolver or UriResolver()
        self.chain_id = int(client.network.chain_id)
        self.commerce_address = str(client.commerce.address)

    async def get_job(self, job_id: int) -> JobView:
        raw = await call(self.client.get_job, job_id, op="get_job")
        # getJob on an unknown id returns the zero struct.
        if int(getattr(raw, "id", job_id) or 0) == 0 and (
            str(getattr(raw, "client", ZERO_ADDRESS)).lower() == ZERO_ADDRESS
        ):
            raise ChainIntegrationError(f"get_job: no job {job_id}", code=NOT_FOUND)
        try:
            return job_view(raw)
        except Exception as exc:
            raise to_chain_error(exc, op="get_job") from exc

    async def get_deliverable_url(self, job_id: int) -> str | None:
        url = await call(self.client.get_deliverable_url, job_id, op="get_deliverable_url")
        return url or None

    async def get_deliverable(self, job_id: int) -> DeliverableView | None:
        url = await self.get_deliverable_url(job_id)
        if url is None:
            return None
        raw = await self.resolver.read(url)
        job = await self.get_job(job_id)
        try:
            manifest: dict[str, Any] | None = parse_manifest(raw)
        except ValueError:
            manifest = None  # not a valid v1 manifest: report it unverified
        digest = manifest_hash(manifest if manifest is not None else raw)
        doc = manifest if manifest is not None else raw
        response = doc.get("response") if isinstance(doc.get("response"), Mapping) else {}
        return DeliverableView(
            url=url,
            content=str(response.get("content", "")),
            content_type=str(response.get("content_type", "text/plain")),
            metadata=dict(doc.get("metadata") or {}),
            manifest=doc,
            manifest_hash=digest,
            verified=manifest is not None and same_hash(digest, job.deliverable),
        )

    async def submit_result(
        self, job_id: int, content: str, metadata: Mapping[str, Any] | None = None
    ) -> SubmitResult:
        if self.job_ops is None:
            return SubmitResult(
                success=False, error_code=READ_ONLY, error="submit_result needs a wallet"
            )
        try:
            result = await self.job_ops.submit_result(
                job_id, content, None if metadata is None else dict(metadata)
            )
        except Exception as exc:
            err = to_chain_error(exc, op="submit_result")
            return SubmitResult(
                success=False, error_code=err.code, error=str(err), retryable=err.retryable
            )
        if result.get("success"):
            return SubmitResult(
                success=True,
                tx_hash=tx_hash(result, op="submit_result"),
                deliverable_url=result.get("deliverableUrl"),
            )
        return SubmitResult(
            success=False,
            error_code=str(result.get("error_code") or "internal_error"),
            error=str(result.get("error") or "submit failed"),
            retryable=bool(result.get("retryable", False)),
            tx_hash=result.get("tx_hash"),
        )


class BnbagentPolicy:
    """``PolicyPort``: reads via ``client.policy`` (``PolicyClient``), writes via ``client``."""

    def __init__(self, client: Any):
        self.client = client
        self.policy = client.policy

    async def dispute_window(self) -> int:
        return int(await call(self.policy.dispute_window, op="dispute_window"))

    async def vote_window(self) -> int | None:
        if not compat.features().policy_vote_window:
            return None  # OptimisticPolicy (SDK 0.5.x) has no vote deadline
        return int(await call(self.policy.vote_window, op="vote_window"))

    async def is_disputed(self, job_id: int) -> bool:
        return bool(await call(self.policy.disputed, job_id, op="disputed"))

    async def is_voter(self, address: str) -> bool:
        return bool(await call(self.policy.is_voter, address, op="is_voter"))

    async def has_voted(self, job_id: int, address: str) -> bool:
        return bool(await call(self.policy.has_voted, job_id, address, op="has_voted"))

    async def dispute(self, job_id: int) -> str:
        return tx_hash(await call(self.client.dispute, job_id, op="dispute"), op="dispute")

    async def vote_reject(self, job_id: int) -> str:
        result = await call(self.client.vote_reject, job_id, op="vote_reject")
        return tx_hash(result, op="vote_reject")
