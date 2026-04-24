"""APEXJobOps — async job lifecycle operations for APEX provider agents.

Wraps ``APEXClient`` (synchronous) for use from async frameworks (FastAPI etc.).
All blocking web3 calls go through ``asyncio.to_thread(...)`` so the event loop
is never blocked.

Responsibilities
----------------
- Discover pending funded jobs for this agent.
- Verify jobs (status / provider / expiry / budget / negotiation quote).
- Submit deliverables (with optional off-chain upload via ``StorageProvider``).
- Auto-settle the provider's own jobs once the dispute window elapses
  (permissionless ``router.settle``; the provider just happens to be the
  natural operator with incentive to do so).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from ...config import NetworkConfig
from ...storage.interface import StorageProvider
from ...wallets.wallet_provider import WalletProvider
from ..client import APEXClient
from ..schema import SCHEMA_VERSION, DeliverableManifest
from ..types import JobStatus, Verdict

logger = logging.getLogger(__name__)


class APEXJobOps:
    """Async job-lifecycle operations for a provider agent.

    Parameters
    ----------
    wallet_provider
        Provider signing material (required).
    network
        Preset name or a ``NetworkConfig`` for custom deployments.
    storage_provider
        Optional off-chain storage for deliverable payloads.
    service_price
        Minimum acceptable budget in token raw units. Used by
        ``verify_job`` to reject under-priced jobs. Advertised decimals in
        402 responses are fetched dynamically from the payment token.
    """

    def __init__(
        self,
        wallet_provider: WalletProvider,
        network: str | NetworkConfig = "bsc-testnet",
        *,
        storage_provider: StorageProvider | None = None,
        service_price: int = 0,
    ) -> None:
        if wallet_provider is None:
            raise ValueError("wallet_provider is required for APEXJobOps")

        self._wallet_provider = wallet_provider
        self._network = network
        self._storage = storage_provider
        self._service_price = service_price

        self._client: APEXClient | None = None
        self._deliverable_urls: dict[int, str] = {}
        self._last_known_counter: int = 0
        self._startup_scan_done: bool = False
        self._pending_open_ids: set[int] = set()
        # Jobs in Submitted state we own, for auto-settle tracking.
        self._submitted_ids: set[int] = set()

    # ----------------------------------------------------------- construction

    def _get_client(self) -> APEXClient:
        if self._client is None:
            self._client = APEXClient(self._wallet_provider, self._network)
        return self._client

    @property
    def agent_address(self) -> str:
        return self._wallet_provider.address

    @property
    def apex_client(self) -> APEXClient:
        return self._get_client()

    # ------------------------------------------------------------- submission

    async def submit_result(
        self,
        job_id: int,
        response_content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a structured deliverable, upload it, and call ``submit`` on-chain.

        The on-chain ``deliverable`` (bytes32) is ``DeliverableManifest.manifest_hash()``
        — keccak256 of the canonical manifest JSON (all fields, not just content).
        The full manifest JSON is uploaded to storage and its URL is passed as
        ``optParams`` so verifiers can fetch, re-hash, and confirm integrity.
        """
        try:
            verification = await self.verify_job(job_id)
            if not verification.get("valid"):
                return {
                    "success": False,
                    "error": f"Job verification failed: {verification.get('error', 'unknown')}",
                }

            apex = self._get_client()

            chain_id = await asyncio.to_thread(lambda: apex.commerce.w3.eth.chain_id)
            manifest = DeliverableManifest(
                version=SCHEMA_VERSION,
                job_id=job_id,
                chain_id=chain_id,
                contracts={
                    "commerce": apex.commerce.address,
                    "router": apex.router.address,
                    "policy": apex.policy.address,
                },
                response={
                    "content": response_content,
                    "content_type": "text/plain",
                },
                submitted_at=int(time.time()),
                metadata=metadata or {},
            )
            data = manifest.to_dict()
            deliverable = manifest.manifest_hash()

            deliverable_url = ""
            if self._storage:
                deliverable_url = await self._storage.upload(data, f"job-{job_id}.json")
                logger.info(f"[APEXJobOps] Deliverable uploaded: {deliverable_url}")
                self._deliverable_urls[job_id] = deliverable_url

            result = await asyncio.to_thread(
                apex.submit, job_id, deliverable, {"deliverable_url": deliverable_url}
            )
            logger.info(f"[APEXJobOps] submit({job_id}) tx: {result['transactionHash']}")
            self._submitted_ids.add(job_id)
            return {
                "success": True,
                "txHash": result["transactionHash"],
                "deliverableUrl": deliverable_url,
                "deliverable": "0x" + deliverable.hex(),
            }
        except Exception as exc:
            logger.error(f"[APEXJobOps] submit({job_id}) failed: {exc}")
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------ reads

    async def get_job(self, job_id: int) -> dict[str, Any]:
        try:
            job = await asyncio.to_thread(self._get_client().get_job, job_id)
            return {
                "success": True,
                "jobId": job.id,
                "client": job.client,
                "provider": job.provider,
                "evaluator": job.evaluator,
                "description": job.description,
                "budget": job.budget,
                "expiredAt": job.expired_at,
                "status": job.status,
                "hook": job.hook,
            }
        except Exception as exc:
            logger.error(f"[APEXJobOps] get_job({job_id}) failed: {exc}")
            return {"success": False, "error": str(exc)}

    async def get_job_status(self, job_id: int) -> dict[str, Any]:
        result = await self.get_job(job_id)
        if not result.get("success"):
            return result
        return {"success": True, "status": result["status"]}

    async def get_response(self, job_id: int) -> dict[str, Any]:
        """Retrieve stored deliverable (cache -> local file -> on-chain URL)."""
        if not self._storage:
            return {"success": False, "error": "No storage configured"}

        url = self._deliverable_urls.get(job_id)
        if url:
            try:
                data = await self._storage.download(url)
                return {"success": True, **data}
            except Exception as exc:
                logger.warning(f"[APEXJobOps] get_response({job_id}) download failed: {exc}")

        if hasattr(self._storage, "_base"):
            try:
                filepath = self._storage._base / f"job-{job_id}.json"
                if filepath.exists():
                    data = json.loads(filepath.read_text(encoding="utf-8"))
                    return {"success": True, **data}
            except Exception as exc:
                logger.warning(f"[APEXJobOps] get_response({job_id}) file read failed: {exc}")

        try:
            apex = self._get_client()
            deliverable_url = await asyncio.to_thread(
                apex.get_deliverable_url, job_id
            )
            if deliverable_url:
                self._deliverable_urls[job_id] = deliverable_url
                data = await self._storage.download(deliverable_url)
                return {"success": True, **data}
        except Exception as exc:
            logger.warning(f"[APEXJobOps] get_response({job_id}) on-chain fallback failed: {exc}")

        return {"success": False, "error": f"Response not found for job {job_id}"}

    # ---------------------------------------------------- verification helper

    async def verify_job(self, job_id: int) -> dict[str, Any]:
        """Check job can be worked by this agent. Returns ``{valid, error, job, warnings}``."""
        try:
            job_result = await self.get_job(job_id)
            if not job_result.get("success"):
                msg = job_result.get("error", "Unknown error")
                is_net = any(k in msg.lower() for k in ["timeout", "connection", "network", "rpc"])
                return {
                    "valid": False,
                    "error": f"Failed to fetch job: {msg}",
                    "error_code": 503 if is_net else 500,
                }

            me = self.agent_address.lower()

            status = job_result.get("status")
            if status != JobStatus.FUNDED:
                status_name = status.name if hasattr(status, "name") else str(status)
                return {
                    "valid": False,
                    "error": f"Job status is {status_name}, expected FUNDED",
                    "error_code": 409,
                }

            if str(job_result.get("provider", "")).lower() != me:
                return {
                    "valid": False,
                    "error": "This agent is not the provider for this job",
                    "error_code": 403,
                }

            now = int(time.time())
            if job_result.get("expiredAt", 0) <= now:
                return {"valid": False, "error": "Job has expired", "error_code": 408}

            description = job_result.get("description", "")
            if description:
                try:
                    from ..negotiation import parse_job_description

                    parsed = parse_job_description(description)
                    if parsed and parsed.quote_expires_at:
                        if now > parsed.quote_expires_at:
                            return {
                                "valid": False,
                                "error": "Negotiation quote has expired",
                                "error_code": 410,
                            }
                except Exception:
                    pass

            if self._service_price > 0:
                budget = job_result.get("budget", 0)
                if budget < self._service_price:
                    decimals = await asyncio.to_thread(self._get_client().token_decimals)
                    return {
                        "valid": False,
                        "error": (
                            f"Job budget ({budget}) is below agent's"
                            f" service price ({self._service_price})"
                        ),
                        "error_code": 402,
                        "service_price": str(self._service_price),
                        "decimals": decimals,
                    }

            warnings = []
            evaluator = str(job_result.get("evaluator", "")).lower()
            client = str(job_result.get("client", "")).lower()
            if evaluator == client:
                warnings.append(
                    {
                        "code": "CLIENT_AS_EVALUATOR",
                        "message": (
                            "Evaluator equals client — client can self-reject"
                            " and refund after you submit."
                        ),
                    }
                )

            return {
                "valid": True,
                "job": job_result,
                "warnings": warnings if warnings else None,
            }
        except Exception as exc:
            msg = str(exc)
            is_net = any(k in msg.lower() for k in ["timeout", "connection", "network", "rpc"])
            return {
                "valid": False,
                "error": f"Failed to verify job: {msg}",
                "error_code": 503 if is_net else 500,
            }

    # ----------------------------------------------------- pending-job scanner

    async def _multicall_scan(self, job_ids: list[int]) -> dict[str, Any]:
        if not job_ids:
            return {"success": True, "jobs": []}

        apex = self._get_client()
        me = self.agent_address.lower()

        jobs = await asyncio.to_thread(apex.commerce.get_jobs_batch, list(job_ids))

        now = int(time.time())
        pending: list[dict[str, Any]] = []
        for job in jobs:
            if job is None:
                continue
            if job.provider.lower() != me:
                self._pending_open_ids.discard(job.id)
                continue
            if job.status == JobStatus.FUNDED and job.expired_at > now:
                pending.append(
                    {
                        "success": True,
                        "jobId": job.id,
                        "client": job.client,
                        "provider": job.provider,
                        "evaluator": job.evaluator,
                        "description": job.description,
                        "budget": job.budget,
                        "expiredAt": job.expired_at,
                        "status": job.status,
                        "hook": job.hook,
                    }
                )
                self._pending_open_ids.discard(job.id)
            elif job.status == JobStatus.OPEN:
                self._pending_open_ids.add(job.id)
            elif job.status == JobStatus.SUBMITTED:
                self._submitted_ids.add(job.id)
            else:
                self._pending_open_ids.discard(job.id)
                self._submitted_ids.discard(job.id)

        return {"success": True, "jobs": pending}

    async def _startup_scan(self) -> dict[str, Any]:
        apex = self._get_client()
        try:
            counter = await asyncio.to_thread(apex.commerce.job_counter)
        except Exception as exc:
            logger.warning(f"[APEXJobOps] startup scan counter failed: {exc}")
            self._startup_scan_done = True
            return {"success": False, "error": str(exc), "jobs": []}

        if counter == 0:
            self._startup_scan_done = True
            return {"success": True, "jobs": []}

        result = await self._multicall_scan(list(range(1, counter + 1)))
        self._last_known_counter = counter
        self._startup_scan_done = True
        logger.info(
            f"[APEXJobOps] Startup scan: {len(result['jobs'])} pending of {counter} total"
        )
        return result

    async def get_pending_jobs(self) -> dict[str, Any]:
        """Return funded, non-expired jobs assigned to this provider."""
        try:
            if not self._startup_scan_done:
                return await self._startup_scan()

            apex = self._get_client()
            counter = await asyncio.to_thread(apex.commerce.job_counter)
            scan_set: set[int] = set()
            if counter > self._last_known_counter:
                scan_set.update(range(self._last_known_counter + 1, counter + 1))
            scan_set.update(self._pending_open_ids)
            if not scan_set:
                return {"success": True, "jobs": []}

            result = await self._multicall_scan(sorted(scan_set))
            self._last_known_counter = counter
            return result
        except Exception as exc:
            logger.error(f"[APEXJobOps] get_pending_jobs failed: {exc}")
            return {"success": False, "error": str(exc), "jobs": []}

    # --------------------------------------------------------- auto-settle

    def track_for_settle(self, job_id: int) -> None:
        """Register a submitted job for the auto-settle loop."""
        self._submitted_ids.add(job_id)

    async def auto_settle_once(self) -> dict[str, Any]:
        """Single pass of the auto-settle loop.

        For each tracked ``Submitted`` job owned by this provider, read the
        current verdict via the Policy's ``check`` and, if it is APPROVE or
        REJECT, call ``router.settle(jobId)``. Pending verdicts are skipped
        and retried on the next pass.
        """
        if not self._submitted_ids:
            return {"success": True, "settled": [], "skipped": []}

        apex = self._get_client()
        me = self.agent_address.lower()
        settled: list[int] = []
        skipped: list[int] = []
        errors: list[tuple[int, str]] = []

        for job_id in list(self._submitted_ids):
            try:
                job = await asyncio.to_thread(apex.get_job, job_id)
            except Exception as exc:
                errors.append((job_id, f"get_job failed: {exc}"))
                continue

            if job.provider.lower() != me:
                # Stop tracking foreign jobs (defensive).
                self._submitted_ids.discard(job_id)
                continue
            if job.status != JobStatus.SUBMITTED:
                # Already settled by someone else, or moved to a terminal state.
                self._submitted_ids.discard(job_id)
                continue

            try:
                verdict, _reason = await asyncio.to_thread(apex.get_verdict, job_id)
            except Exception as exc:
                errors.append((job_id, f"get_verdict failed: {exc}"))
                continue

            if verdict == Verdict.PENDING:
                skipped.append(job_id)
                continue

            try:
                result = await asyncio.to_thread(apex.settle, job_id)
                logger.info(
                    f"[APEXJobOps] auto-settle({job_id}) verdict={verdict.name}"
                    f" tx={result['transactionHash']}"
                )
                settled.append(job_id)
                self._submitted_ids.discard(job_id)
            except Exception as exc:
                # Another settler may have won the race; re-check next pass.
                logger.warning(f"[APEXJobOps] settle({job_id}) failed: {exc}")
                errors.append((job_id, f"settle failed: {exc}"))

        return {
            "success": True,
            "settled": settled,
            "skipped": skipped,
            "errors": errors,
        }


async def run_auto_settle_loop(
    ops: APEXJobOps,
    interval: float = 30.0,
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Long-running background task: periodically call ``auto_settle_once``.

    Stops when ``stop_event`` is set (if provided) or on cancellation.
    Exceptions inside a pass are logged and the loop continues — permissionless
    ``settle`` calls are inherently racey and transient failures are expected.
    """
    logger.info(f"[APEXJobOps] auto-settle loop starting (interval={interval:.1f}s)")
    try:
        while True:
            try:
                await ops.auto_settle_once()
            except Exception as exc:
                logger.warning(f"[APEXJobOps] auto-settle pass error: {exc}")

            if stop_event is not None:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=interval)
                    break
                except asyncio.TimeoutError:
                    continue
            else:
                await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("[APEXJobOps] auto-settle loop cancelled")
        raise
