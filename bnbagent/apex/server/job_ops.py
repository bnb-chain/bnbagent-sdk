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
import json
import logging
import time
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
        service_price: int = 0,
        payment_token_decimals: int = 18,
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
            service_price: Agent's service price in token smallest unit.
                           Jobs with budget below this are rejected by verify_job().
                           0 means no budget check (default).
            payment_token_decimals: Decimal places of the payment token (default: 18).
                                   Included in verify_job() 402 responses so clients
                                   can interpret the amounts.
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
        self._service_price = service_price
        self._payment_token_decimals = payment_token_decimals
        self._client: APEXClient | None = None
        self._deliverable_urls: dict[int, str] = {}  # job_id → data_url
        self._last_scanned_block: int | None = None
        self._startup_scan_done: bool = False
        self._last_known_next_id: int = 0
        self._pending_open_ids: set[int] = set()  # OPEN jobs assigned to this agent

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
                self._deliverable_urls[job_id] = data_url

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

    async def get_response(self, job_id: int) -> dict[str, Any]:
        """
        Get stored deliverable response for a job.

        Resolution order:
        1. In-memory cache (populated by submit_result in the same process)
        2. Convention filename for local storage (``job-{id}.json``)
        3. On-chain fallback: decode ``optParams`` from the submit tx calldata
           to recover the data URL, then download from storage.

        The on-chain fallback (step 3) costs 2 RPC calls (``eth_getLogs`` on
        the indexed ``JobSubmitted`` event + ``eth_getTransactionByHash``) and
        only triggers when steps 1 and 2 miss — typically after a process
        restart with IPFS storage.

        Args:
            job_id: The job ID

        Returns:
            Dict with success status and deliverable data (response, job, metadata, etc.)
        """
        if not self._storage:
            return {"success": False, "error": "No storage configured"}

        # 1. In-memory cache (populated by submit_result)
        url = self._deliverable_urls.get(job_id)
        if url:
            try:
                data = await self._storage.download(url)
                return {"success": True, **data}
            except Exception as e:
                logger.warning(f"[APEXJobOps] get_response({job_id}) download failed: {e}")

        # 2. Convention filename for local storage
        if hasattr(self._storage, "_base"):
            try:
                filepath = self._storage._base / f"job-{job_id}.json"
                if filepath.exists():
                    content = filepath.read_text(encoding="utf-8")
                    data = json.loads(content)
                    return {"success": True, **data}
            except Exception as e:
                logger.warning(f"[APEXJobOps] get_response({job_id}) file read failed: {e}")

        # 3. On-chain fallback: recover data URL from submit tx calldata
        try:
            client = self._get_client()
            data_url = await asyncio.to_thread(client.get_submit_data_url, job_id)
            if data_url:
                logger.info(
                    f"[APEXJobOps] get_response({job_id}) recovered URL from chain: {data_url}"
                )
                # Cache for subsequent requests
                self._deliverable_urls[job_id] = data_url
                data = await self._storage.download(data_url)
                return {"success": True, **data}
        except Exception as e:
            logger.warning(f"[APEXJobOps] get_response({job_id}) on-chain fallback failed: {e}")

        return {"success": False, "error": f"Response not found for job {job_id}"}

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

    async def _multicall_scan(self, job_ids: list[int]) -> dict[str, Any]:
        """Scan a list of job IDs via Multicall3 and return funded jobs for this agent.

        Uses a single ``eth_call`` (via ``get_jobs_batch``) instead of
        ``eth_getLogs``, avoiding BSC public node rate limits on log queries.

        Also tracks OPEN jobs assigned to this agent in ``_pending_open_ids``
        so they can be re-checked on subsequent polls (a job may transition
        from OPEN → FUNDED between polls without changing ``next_job_id``).

        Args:
            job_ids: Job IDs to check.

        Returns:
            ``{"success": True, "jobs": [...]}`` with funded, non-expired jobs
            assigned to this agent.
        """
        if not job_ids:
            return {"success": True, "jobs": []}

        client = self._get_client()
        my_address = self.agent_address.lower()

        all_jobs = await asyncio.to_thread(client.get_jobs_batch, list(job_ids))

        now = int(time.time())
        pending_jobs = []
        for job in all_jobs:
            if job is None:
                continue
            provider = job.get("provider", "").lower()
            status = job.get("status")
            expired_at = job.get("expiredAt", 0)
            job_id = job.get("jobId")

            if provider != my_address:
                # Not our job — stop tracking if we were
                self._pending_open_ids.discard(job_id)
                continue

            if status == APEXStatus.FUNDED and expired_at > now:
                pending_jobs.append({"success": True, **job})
                self._pending_open_ids.discard(job_id)
            elif status == APEXStatus.OPEN:
                # Track OPEN jobs so we re-check them next poll
                self._pending_open_ids.add(job_id)
            else:
                # Terminal state (COMPLETED, REJECTED, EXPIRED, etc.)
                self._pending_open_ids.discard(job_id)

        return {"success": True, "jobs": pending_jobs}

    async def _startup_scan(self) -> dict[str, Any]:
        """One-time batch scan of all jobs via Multicall3.

        Called on the first invocation of ``get_pending_jobs()`` to bootstrap
        the pending-job list without relying on ``eth_getLogs`` over a large
        block range (which triggers rate limits on public BSC nodes).

        After completing, sets ``_startup_scan_done = True`` and records the
        block number at the time of the snapshot so that subsequent calls can
        do progressive Multicall3 scanning from that point forward.

        If the Multicall3 batch read fails, falls back to the original
        event-based scan for this one call.
        """
        client = self._get_client()

        # Record block BEFORE scanning so progressive scanning picks up
        # any events emitted during or after the batch read.
        snapshot_block = await asyncio.to_thread(lambda: client.w3.eth.block_number)

        try:
            next_id = await asyncio.to_thread(client.next_job_id)
            if next_id == 0:
                self._last_scanned_block = snapshot_block
                self._startup_scan_done = True
                return {"success": True, "jobs": []}

            result = await self._multicall_scan(list(range(next_id)))

            self._last_scanned_block = snapshot_block
            self._last_known_next_id = next_id
            self._startup_scan_done = True
            logger.info(
                f"[APEXJobOps] Startup scan complete: {len(result['jobs'])} pending"
                f" out of {next_id} total jobs (snapshot block {snapshot_block})"
            )
            return result

        except Exception as e:
            logger.warning(
                f"[APEXJobOps] Multicall startup scan failed ({e}),"
                " falling back to event scan"
            )
            # Fall back to original event-based scan
            my_address = self.agent_address.lower()
            try:
                latest_block = snapshot_block
                from_block = max(0, latest_block - 45000)
                result = await self._event_scan(from_block, "latest", my_address)
                return result
            except Exception as fallback_err:
                logger.warning(
                    f"[APEXJobOps] Event scan fallback also failed ({fallback_err}),"
                    " will retry next poll"
                )
                return {"success": False, "error": str(fallback_err), "jobs": []}
            finally:
                self._last_scanned_block = snapshot_block
                self._startup_scan_done = True

    async def _event_scan(
        self,
        from_block: int,
        to_block: str,
        my_address: str,
    ) -> dict[str, Any]:
        """Scan JobFunded events and filter for pending jobs assigned to this agent."""
        client = self._get_client()

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

    async def get_pending_jobs(
        self,
        from_block: int | None = None,
        to_block: str = "latest",
        max_block_range: int = 45000,
    ) -> dict[str, Any]:
        """
        Get funded jobs assigned to this agent.

        Uses Multicall3 ``eth_call`` exclusively to avoid ``eth_getLogs`` rate
        limits on BSC public nodes:

        1. **Startup** (first call): Multicall3 batch scan of all existing jobs.
        2. **Runtime** (subsequent calls): Check ``next_job_id()`` — if unchanged,
           no new jobs exist (0 extra RPCs).  If new jobs exist, scan only the
           new ID range via Multicall3 (1 ``eth_call``).

        If the caller explicitly passes ``from_block``, the original event-based
        scan is used instead (for backwards compatibility).

        Args:
            from_block: Starting block number. When None, uses Multicall3
                scanning (startup scan on first call, then incremental).
            to_block: Ending block number or "latest"
            max_block_range: Maximum block range for fallback queries
                (default: 45000, under BSC 50k limit)

        Returns:
            Dict with success status and list of pending job dicts, or error on failure
        """
        try:
            # Explicit from_block: honor it directly, no state update
            if from_block is not None:
                my_address = self.agent_address.lower()
                return await self._event_scan(from_block, to_block, my_address)

            # First call: one-time startup scan via Multicall3
            if not self._startup_scan_done:
                return await self._startup_scan()

            # Subsequent calls: progressive Multicall3 scanning
            client = self._get_client()
            next_id = await asyncio.to_thread(client.next_job_id)

            # Collect IDs to scan: new job IDs + previously-seen OPEN jobs
            scan_set: set[int] = set()
            if next_id > self._last_known_next_id:
                scan_set.update(range(self._last_known_next_id, next_id))
            scan_set.update(self._pending_open_ids)

            if not scan_set:
                logger.debug(
                    f"[APEXJobOps] Progressive scan: no changes"
                    f" (next_id={next_id}, open={len(self._pending_open_ids)})"
                )
                return {"success": True, "jobs": []}

            scan_ids = sorted(scan_set)
            logger.info(
                f"[APEXJobOps] Progressive scan: checking {len(scan_ids)} job(s)"
                f" (new={next_id - self._last_known_next_id},"
                f" open={len(self._pending_open_ids)})"
            )
            result = await self._multicall_scan(scan_ids)
            self._last_known_next_id = next_id
            found = len(result.get("jobs", []))
            if found:
                logger.info(
                    f"[APEXJobOps] Progressive scan found {found} pending job(s)"
                )
            return result

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

            # Budget check
            if self._service_price > 0:
                job_budget = job_result.get("budget", 0)
                if job_budget < self._service_price:
                    return {
                        "valid": False,
                        "error": (
                            f"Job budget ({job_budget}) is below agent's "
                            f"service price ({self._service_price})"
                        ),
                        "error_code": 402,
                        "service_price": str(self._service_price),
                        "decimals": self._payment_token_decimals,
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
