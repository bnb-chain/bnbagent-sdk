"""
ACPJobOps — simplified job lifecycle operations for agents (EIP-8183).

Wraps ACPClient with async operations for agent-side job handling.

Example:
    ops = ACPJobOps(
        rpc_url="https://bsc-testnet.bnbchain.org",
        acp_address="0x...",
        private_key="0x...",
        storage_provider=ipfs_provider,  # optional
    )

    await ops.submit_result(
        job_id=123,
        response_content="Agent response...",
    )
"""

import asyncio
import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional

from web3 import Web3

from ..acp_client import ACPClient, ACPStatus
from ..storage.interface import IStorageProvider

logger = logging.getLogger(__name__)


class ACPJobOps:
    """
    Simplified job lifecycle operations for agents using EIP-8183.

    Combines ACPClient and optional storage handling into a single interface.

    Async/Sync Boundary
    --------------------
    ACPClient is **synchronous** — web3.py's HTTPProvider performs blocking I/O
    and there is no mature async web3 transport.  Converting ACPClient to async
    would be a large, risky change.

    ACPJobOps is **async** so it can be used from async frameworks (FastAPI, etc.)
    without blocking the event loop.  Every call to a synchronous ACPClient method
    is wrapped in ``asyncio.to_thread()`` to offload the blocking I/O to a thread.

    Storage providers (IStorageProvider) are **async** — their ``upload()`` method
    is awaited directly.
    """

    def __init__(
        self,
        rpc_url: str,
        acp_address: str,
        private_key: str,
        storage_provider: Optional[IStorageProvider] = None,
        chain_id: int = 97,
    ):
        """
        Initialize job operations.

        Args:
            rpc_url: RPC endpoint URL
            acp_address: AgenticCommerceUpgradeable contract address
            private_key: Agent wallet private key
            storage_provider: Optional storage provider for response upload
            chain_id: Chain ID (default: 97 for BSC Testnet)
        """
        self._rpc_url = rpc_url
        self._acp_address = acp_address
        self._private_key = private_key if private_key.startswith("0x") else f"0x{private_key}"
        self._storage = storage_provider
        self._chain_id = chain_id
        self._client: Optional[ACPClient] = None

    def _get_client(self) -> ACPClient:
        """Get or create ACPClient instance (sync — no I/O on first call beyond ABI load)."""
        if self._client is None:
            w3 = Web3(Web3.HTTPProvider(self._rpc_url))
            try:
                from web3.middleware import ExtraDataToPOAMiddleware
                w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            except ImportError:
                pass
            self._client = ACPClient(
                web3=w3,
                contract_address=self._acp_address,
                private_key=self._private_key,
            )
        return self._client

    @property
    def agent_address(self) -> str:
        """Get the agent's wallet address."""
        client = self._get_client()
        return client._account or ""

    async def submit_result(
        self,
        job_id: int,
        response_content: str,
        metadata: Optional[Dict[str, Any]] = None,
        include_job_context: bool = True,
        include_negotiation_history: bool = True,
    ) -> Dict[str, Any]:
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
            deliverable_data: Dict[str, Any] = {
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
                    
                    # Include ALL EIP-8183 Job fields
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
                        logger.warning(f"[ACPJobOps] Failed to get negotiation history: {e}")
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
                logger.info(f"[ACPJobOps] Response uploaded: {data_url}")
            
            if data_url:
                deliverable_hash = Web3.keccak(text=data_url)
                # Pass data_url as opt_params for evaluator hooks (e.g., OOv3Evaluator)
                opt_params = data_url.encode("utf-8")
            else:
                deliverable_hash = Web3.keccak(
                    text=json.dumps(deliverable_data, sort_keys=True, separators=(",", ":"))
                )
                opt_params = b""

            result = await asyncio.to_thread(
                client.submit, job_id, deliverable_hash, opt_params
            )

            logger.info(f"[ACPJobOps] submit({job_id}) success: {result['transactionHash']}")
            return {
                "success": True,
                "txHash": result["transactionHash"],
                "dataUrl": data_url,
                "deliverableHash": "0x" + deliverable_hash.hex(),
            }

        except Exception as e:
            logger.error(f"[ACPJobOps] submit({job_id}) failed: {e}")
            return {"success": False, "error": str(e)}

    async def get_job(self, job_id: int) -> Dict[str, Any]:
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
            logger.error(f"[ACPJobOps] get_job({job_id}) failed: {e}")
            return {"success": False, "error": str(e)}

    async def get_job_status(self, job_id: int) -> Dict[str, Any]:
        """
        Get job status from chain.

        Args:
            job_id: The job ID to query

        Returns:
            Dict with success status and ACPStatus value, or error dict on failure
        """
        try:
            client = self._get_client()
            status = await asyncio.to_thread(client.get_job_status, job_id)
            return {"success": True, "status": status}
        except Exception as e:
            logger.error(f"[ACPJobOps] get_job_status({job_id}) failed: {e}")
            return {"success": False, "error": str(e)}

    async def get_pending_jobs(
        self,
        from_block: int | None = None,
        to_block: str = "latest",
        max_block_range: int = 45000,
    ) -> Dict[str, Any]:
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
                latest_block = await asyncio.to_thread(
                    lambda: client.w3.eth.block_number
                )
                from_block = max(0, latest_block - max_block_range)

            logger.info(f"[ACPJobOps] Querying JobFunded events from block {from_block}")
            events = await asyncio.to_thread(
                client.get_job_funded_events, from_block, to_block
            )
            logger.info(f"[ACPJobOps] Found {len(events)} JobFunded events")
            
            pending_jobs = []
            for event in events:
                job_id = event["jobId"]
                job_result = await self.get_job(job_id)
                if not job_result.get("success"):
                    logger.warning(f"[ACPJobOps] Failed to get job #{job_id}")
                    continue
                
                provider = job_result.get("provider", "").lower()
                status = job_result.get("status")
                logger.info(f"[ACPJobOps] Job #{job_id}: provider={provider}, status={status}, my_address={my_address}")
                
                if provider == my_address and status == ACPStatus.FUNDED:
                    pending_jobs.append(job_result)
                    logger.info(f"[ACPJobOps] Job #{job_id} matched! Adding to pending jobs")
            
            return {"success": True, "jobs": pending_jobs}
        except Exception as e:
            logger.error(f"[ACPJobOps] get_pending_jobs failed: {e}")
            return {"success": False, "error": str(e), "jobs": []}

    async def verify_job(self, job_id: int) -> Dict[str, Any]:
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
                is_network_error = any(kw in error_msg.lower() for kw in ["timeout", "connection", "network", "rpc"])
                return {
                    "valid": False,
                    "error": f"Failed to fetch job: {error_msg}",
                    "error_code": 503 if is_network_error else 500,
                }
            
            my_address = self.agent_address.lower()
            
            if job_result.get("status") != ACPStatus.FUNDED:
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
                warnings.append({
                    "code": "CLIENT_AS_EVALUATOR",
                    "message": "Evaluator is same as client - client can reject and get refund after you submit",
                })
            
            return {
                "valid": True,
                "job": job_result,
                "warnings": warnings if warnings else None,
            }
            
        except Exception as e:
            error_msg = str(e)
            is_network_error = any(kw in error_msg.lower() for kw in ["timeout", "connection", "network", "rpc"])
            return {
                "valid": False,
                "error": f"Failed to verify job: {error_msg}",
                "error_code": 503 if is_network_error else 500,
            }

    @property
    def acp_client(self) -> ACPClient:
        """Get the underlying ACPClient instance."""
        return self._get_client()
