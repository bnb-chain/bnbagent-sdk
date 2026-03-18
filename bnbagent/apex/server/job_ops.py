"""
APEXJobOps — simplified async job lifecycle operations for APEX agents.

Wraps APEXClient with async operations for agent-side job handling.

Example:
    ops = APEXJobOps(
        rpc_url="https://bsc-testnet.bnbchain.org",
        erc8183_address="0x...",
        private_key="0x...",
        storage_provider=ipfs_provider,  # optional
    )

    await ops.submit_result(
        job_id=123,
        response_content="Agent response...",
    )
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from web3 import Web3

if TYPE_CHECKING:
    from ...wallets.wallet_provider import WalletProvider

from ...storage.interface import StorageProvider
from ..client import APEXClient, APEXStatus

logger = logging.getLogger(__name__)


class APEXJobOps:
    """
    Simplified job lifecycle operations for agents using ERC-8183.

    Combines APEXClient and optional storage handling into a single interface.

    Async/Sync Boundary
    --------------------
    APEXClient is **synchronous** — web3.py's HTTPProvider performs blocking I/O
    and there is no mature async web3 transport.  Converting APEXClient to async
    would be a large, risky change.

    APEXJobOps is **async** so it can be used from async frameworks (FastAPI, etc.)
    without blocking the event loop.  Every call to a synchronous APEXClient method
    is wrapped in ``asyncio.to_thread()`` to offload the blocking I/O to a thread.

    Storage providers (StorageProvider) are **async** — their ``upload()`` method
    is awaited directly.
    """

    def __init__(
        self,
        rpc_url: str,
        erc8183_address: str,
        private_key: str = "",
        storage_provider: StorageProvider | None = None,
        chain_id: int = 97,
        wallet_provider: WalletProvider | None = None,
    ):
        """
        Initialize job operations.

        Args:
            rpc_url: RPC endpoint URL
            erc8183_address: AgenticCommerceUpgradeable contract address (ERC-8183)
            private_key: Agent wallet private key (optional if wallet_provider set)
            storage_provider: Optional storage provider for response upload
            chain_id: Chain ID (default: 97 for BSC Testnet)
            wallet_provider: Optional wallet provider for signing transactions (preferred)
        """
        self._rpc_url = rpc_url
        self._erc8183_address = erc8183_address
        if private_key:
            self._private_key = private_key if private_key.startswith("0x") else f"0x{private_key}"
        else:
            self._private_key = None
        self._storage = storage_provider
        self._chain_id = chain_id
        self._wallet_provider = wallet_provider
        self._client: APEXClient | None = None

    def _get_client(self) -> APEXClient:
        """Get or create APEXClient instance (sync — no I/O on first call beyond ABI load)."""
        if self._client is None:
            from ...core.abi_loader import create_web3

            w3 = create_web3(self._rpc_url)
            self._client = APEXClient(
                web3=w3,
                contract_address=self._erc8183_address,
                private_key=self._private_key,
                wallet_provider=self._wallet_provider,
            )
        return self._client

    @property
    def agent_address(self) -> str:
        """Get the agent's wallet address."""
        if self._wallet_provider:
            return self._wallet_provider.address
        client = self._get_client()
        return client._account or ""

    async def submit_result(
        self,
        job_id: int,
        response_content: str,
        metadata: dict[str, Any] | None = None,
        include_job_context: bool = True,
        include_negotiation_history: bool = True,
    ) -> dict[str, Any]:
        """
        Submit job result on-chain.

        Args:
            job_id: The job ID
            response_content: Agent's response content
            metadata: Optional metadata to include in storage
            include_job_context: Include job details in IPFS data (default: True)
            include_negotiation_history: Include budget negotiation history (default: True)

        Returns:
            Dict with success status, transaction hash, and data URL
        """
        try:
            # Defense-in-depth: verify job before submitting (SDK-H01)
            verification = await self.verify_job(job_id)
            if not verification.get("valid"):
                return {
                    "success": False,
                    "error": f"Job verification failed: {verification.get('error', 'unknown')}",
                }

            client = self._get_client()

            data_url = ""
            submit_timestamp = int(time.time())

            # Build deliverable data structure
            deliverable_data: dict[str, Any] = {
                "response": response_content,
            }

            # Include job context if requested
            if include_job_context or include_negotiation_history:
                job_info = await asyncio.to_thread(client.get_job, job_id)

                if include_job_context:
                    # Get payment token address
                    try:
                        payment_token = await asyncio.to_thread(client.payment_token)
                    except Exception:
                        payment_token = ""

                    # Include ALL ERC-8183 Job fields
                    deliverable_data["job"] = {
                        "id": job_id,
                        "description": job_info.get("description", ""),
                        "budget": str(job_info.get("budget", 0)),
                        "client": job_info.get("client", ""),
                        "provider": job_info.get("provider", ""),
                        "evaluator": job_info.get("evaluator", ""),
                        "hook": job_info.get("hook", ""),
                        "expired_at": job_info.get("expiredAt", 0),
                        "payment_token": payment_token,
                    }

                # Include negotiation history if requested
                if include_negotiation_history:
                    try:
                        budget_events = await asyncio.to_thread(
                            client.get_budget_set_events, job_id, 0
                        )

                        deliverable_data["negotiation"] = {
                            "budget_history": [
                                {
                                    "amount": str(event.get("amount")),
                                    "block": event.get("blockNumber"),
                                    "tx": event.get("transactionHash"),
                                }
                                for event in budget_events
                            ],
                        }
                    except Exception as e:
                        logger.warning(f"[APEXJobOps] Failed to get negotiation history: {e}")
                        deliverable_data["negotiation"] = {"budget_history": []}

            # Build metadata with timestamps
            meta = metadata.copy() if metadata else {}
            if "timestamps" not in meta:
                meta["timestamps"] = {}
            meta["timestamps"]["submitted_at"] = submit_timestamp
            deliverable_data["metadata"] = meta

            # Use job-{jobId}.json naming convention
            filename = f"job-{job_id}.json"

            # Storage providers are async — call upload() directly.
            # Do NOT use save_sync() here; we are already in an async context.
            if self._storage:
                data_url = await self._storage.upload(deliverable_data, filename)
                logger.info(f"[APEXJobOps] Response uploaded: {data_url}")

            if data_url:
                deliverable_hash = Web3.keccak(text=data_url)
                # Pass data_url as opt_params for evaluator hooks (e.g., APEXEvaluator)
                opt_params = data_url.encode("utf-8")
            else:
                deliverable_hash = Web3.keccak(
                    text=json.dumps(deliverable_data, sort_keys=True, separators=(",", ":"))
                )
                opt_params = b""

            result = await asyncio.to_thread(client.submit, job_id, deliverable_hash, opt_params)

            logger.info(f"[APEXJobOps] submit({job_id}) success: {result['transactionHash']}")
            return {
                "success": True,
                "txHash": result["transactionHash"],
                "dataUrl": data_url,
                "deliverableHash": "0x" + deliverable_hash.hex(),
            }

        except Exception as e:
            logger.error(f"[APEXJobOps] submit({job_id}) failed: {e}")
            return {"success": False, "error": str(e)}

    async def get_job(self, job_id: int) -> dict[str, Any]:
        """
        Get job details from chain.

        Args:
            job_id: The job ID to query

        Returns:
            Job details dict with success status, or error dict on failure
        """
        try:
            client = self._get_client()
            job = await asyncio.to_thread(client.get_job, job_id)
            return {"success": True, **job}
        except Exception as e:
            logger.error(f"[APEXJobOps] get_job({job_id}) failed: {e}")
            return {"success": False, "error": str(e)}

    async def get_job_status(self, job_id: int) -> dict[str, Any]:
        """
        Get job status from chain.

        Args:
            job_id: The job ID to query

        Returns:
            Dict with success status and APEXStatus value, or error dict on failure
        """
        try:
            client = self._get_client()
            status = await asyncio.to_thread(client.get_job_status, job_id)
            return {"success": True, "status": status}
        except Exception as e:
            logger.error(f"[APEXJobOps] get_job_status({job_id}) failed: {e}")
            return {"success": False, "error": str(e)}

    async def get_pending_jobs(
        self,
        from_block: int | None = None,
        to_block: str = "latest",
        max_block_range: int = 45000,
    ) -> dict[str, Any]:
        """
        Get funded jobs assigned to this agent.

        Scans JobFunded events and filters for jobs where this agent is the provider
        and status is FUNDED.

        Args:
            from_block: Starting block number (default: latest - max_block_range)
            to_block: Ending block number or "latest"
            max_block_range: Maximum block range to query (default: 45000, under BSC 50k limit)

        Returns:
            Dict with success status and list of pending job dicts, or error on failure
        """
        try:
            client = self._get_client()
            my_address = self.agent_address.lower()

            # Calculate from_block if not specified (avoid exceeding RPC block range limits)
            if from_block is None:
                latest_block = await asyncio.to_thread(lambda: client.w3.eth.block_number)
                from_block = max(0, latest_block - max_block_range)

            logger.debug(f"[APEXJobOps] Querying JobFunded events from block {from_block}")
            events = await asyncio.to_thread(client.get_job_funded_events, from_block, to_block)
            logger.debug(f"[APEXJobOps] Found {len(events)} JobFunded events")

            pending_jobs = []
            for event in events:
                job_id = event["jobId"]
                job_result = await self.get_job(job_id)
                if not job_result.get("success"):
                    logger.warning(f"[APEXJobOps] Failed to get job #{job_id}")
                    continue

                provider = job_result.get("provider", "").lower()
                status = job_result.get("status")
                logger.debug(
                    f"[APEXJobOps] Job #{job_id}:"
                    f" provider={provider},"
                    f" status={status},"
                    f" my_address={my_address}"
                )

                if provider == my_address and status == APEXStatus.FUNDED:
                    pending_jobs.append(job_result)
                    logger.info(f"[APEXJobOps] Job #{job_id} matched! Adding to pending jobs")

            return {"success": True, "jobs": pending_jobs}
        except Exception as e:
            logger.error(f"[APEXJobOps] get_pending_jobs failed: {e}")
            return {"success": False, "error": str(e), "jobs": []}

    async def verify_job(self, job_id: int) -> dict[str, Any]:
        """
        Verify if a job can be processed by this agent.

        Checks:
        - Job exists
        - Job status is FUNDED
        - This agent is the provider
        - Job has not expired
        - Security warnings (e.g., evaluator == client)

        Args:
            job_id: The job ID to verify

        Returns:
            Dict with valid status, job details, warnings, and error details
        """
        try:
            job_result = await self.get_job(job_id)

            if not job_result.get("success"):
                error_msg = job_result.get("error", "Unknown error")
                is_network_error = any(
                    kw in error_msg.lower() for kw in ["timeout", "connection", "network", "rpc"]
                )
                return {
                    "valid": False,
                    "error": f"Failed to fetch job: {error_msg}",
                    "error_code": 503 if is_network_error else 500,
                }

            my_address = self.agent_address.lower()

            if job_result.get("status") != APEXStatus.FUNDED:
                status = job_result.get("status")
                status_name = status.name if hasattr(status, "name") else str(status)
                return {
                    "valid": False,
                    "error": f"Job status is {status_name}, expected FUNDED",
                    "error_code": 409,
                }

            if job_result.get("provider", "").lower() != my_address:
                return {
                    "valid": False,
                    "error": "This agent is not the provider for this job",
                    "error_code": 403,
                }

            import time

            if job_result.get("expiredAt", 0) <= int(time.time()):
                return {
                    "valid": False,
                    "error": "Job has expired",
                    "error_code": 408,
                }

            # Security warnings
            warnings = []
            evaluator = job_result.get("evaluator", "").lower()
            client = job_result.get("client", "").lower()

            if evaluator == client:
                warnings.append(
                    {
                        "code": "CLIENT_AS_EVALUATOR",
                        "message": (
                            "Evaluator is same as client -"
                            " client can reject and get"
                            " refund after you submit"
                        ),
                    }
                )

            return {
                "valid": True,
                "job": job_result,
                "warnings": warnings if warnings else None,
            }

        except Exception as e:
            error_msg = str(e)
            is_network_error = any(
                kw in error_msg.lower() for kw in ["timeout", "connection", "network", "rpc"]
            )
            return {
                "valid": False,
                "error": f"Failed to verify job: {error_msg}",
                "error_code": 503 if is_network_error else 500,
            }

    @property
    def apex_client(self) -> APEXClient:
        """Get the underlying APEXClient instance."""
        return self._get_client()


async def run_job_loop(
    job_ops: APEXJobOps,
    on_task: Callable[..., Any],
    poll_interval: int = 10,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Background loop: discover funded jobs → verify → process → submit.

    Encapsulates the standard APEX agent polling pattern. Users provide a task
    handler; the SDK handles discovery, verification, submission, and errors.

    Args:
        job_ops: APEXJobOps instance (from APEXState.job_ops)
        on_task: Callback invoked for each funded job. Receives the job dict
                 and returns the result. Supports four signatures::

                     def on_task(job: dict) -> str                    # sync
                     async def on_task(job: dict) -> str              # async
                     def on_task(job: dict) -> tuple[str, dict]       # sync + per-job metadata
                     async def on_task(job: dict) -> tuple[str, dict] # async + per-job metadata

        poll_interval: Seconds between polling cycles (default: 10)
        metadata: Default metadata attached to every submission.
                  Per-job metadata from ``on_task`` is merged on top.
    """
    is_async = inspect.iscoroutinefunction(on_task)
    agent_addr = job_ops.agent_address
    logger.info(f"[JobRunner] Starting job loop for {agent_addr}, poll every {poll_interval}s")

    while True:
        try:
            result = await job_ops.get_pending_jobs()

            if not result.get("success"):
                logger.warning(f"[JobRunner] get_pending_jobs error: {result.get('error')}")
                await asyncio.sleep(poll_interval)
                continue

            for job in result.get("jobs", []):
                job_id = job["jobId"]
                description = job.get("description", "")
                logger.info(f"[JobRunner] Processing job #{job_id}: {description[:80]}")

                # Verify
                verification = await job_ops.verify_job(job_id)
                if not verification["valid"]:
                    logger.warning(
                        f"[JobRunner] Job #{job_id} verification failed: "
                        f"{verification.get('error')}"
                    )
                    continue

                # Process — call user's task handler
                try:
                    if is_async:
                        task_result = await on_task(job)
                    else:
                        task_result = await asyncio.to_thread(on_task, job)
                except Exception as e:
                    logger.error(f"[JobRunner] on_task failed for job #{job_id}: {e}")
                    continue

                # Parse result: str or (str, dict)
                if isinstance(task_result, tuple):
                    response_content, job_metadata = task_result
                else:
                    response_content = task_result
                    job_metadata = None

                # Merge metadata: defaults ← per-job overrides
                merged_meta = dict(metadata) if metadata else {}
                if job_metadata:
                    merged_meta.update(job_metadata)

                # Submit
                submission = await job_ops.submit_result(
                    job_id=job_id,
                    response_content=response_content,
                    metadata=merged_meta or None,
                )

                if submission.get("success"):
                    logger.info(
                        f"[JobRunner] Job #{job_id} submitted! TX: {submission['txHash']}"
                    )
                    if submission.get("dataUrl"):
                        logger.info(f"[JobRunner]   Storage: {submission['dataUrl']}")
                else:
                    logger.error(
                        f"[JobRunner] Job #{job_id} submission failed: "
                        f"{submission.get('error')}"
                    )

        except Exception as e:
            logger.error(f"[JobRunner] Polling error: {e}")

        await asyncio.sleep(poll_interval)
