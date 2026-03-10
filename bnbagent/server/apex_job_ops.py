"""
ApexJobOps — simplified job lifecycle operations for agents.

Wraps ApexClient with automatic ServiceRecord building and storage handling.

Example:
    ops = ApexJobOps(
        rpc_url="https://bsc-testnet.bnbchain.org",
        apex_address="0x...",
        private_key="0x...",
        storage_provider=ipfs_provider,  # optional
    )

    await ops.accept_job(job_id=123)
    await ops.submit_result(
        job_id=123,
        response_content="Agent response...",
        negotiation_request=req_dict,
        negotiation_response=resp_dict,
    )
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional

from web3 import Web3

from ..apex_client import ApexClient, JobPhase
from ..service_record import (
    ServiceRecord,
    NegotiationData,
    ServiceData,
    TimestampData,
    OnChainReferences,
)
from ..storage.interface import IStorageProvider

logger = logging.getLogger(__name__)


class ApexJobOps:
    """
    Simplified job lifecycle operations for agents.

    Combines ApexClient, ServiceRecord building, and storage handling
    into a single easy-to-use interface.
    """

    def __init__(
        self,
        rpc_url: str,
        apex_address: str,
        private_key: str,
        storage_provider: Optional[IStorageProvider] = None,
        chain_id: int = 97,
    ):
        """
        Initialize job operations.

        Args:
            rpc_url: RPC endpoint URL
            apex_address: ApexUpgradeable contract address
            private_key: Agent owner wallet private key
            storage_provider: Optional storage provider for ServiceRecord upload
            chain_id: Chain ID (default: 97 for BSC Testnet)
        """
        self._rpc_url = rpc_url
        self._apex_address = apex_address
        self._private_key = private_key if private_key.startswith("0x") else f"0x{private_key}"
        self._storage = storage_provider
        self._chain_id = chain_id

        self._client: Optional[ApexClient] = None
        self._accept_tx_hashes: Dict[int, str] = {}

    def _get_client(self) -> ApexClient:
        """Get or create ApexClient instance."""
        if self._client is None:
            w3 = Web3(Web3.HTTPProvider(self._rpc_url))
            try:
                from web3.middleware import ExtraDataToPOAMiddleware
                w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            except ImportError:
                pass
            self._client = ApexClient(
                web3=w3,
                contract_address=self._apex_address,
                private_key=self._private_key,
            )
        return self._client

    @staticmethod
    def _ensure_0x(h: str) -> str:
        """Ensure hash has 0x prefix."""
        if not h:
            return ""
        return h if h.startswith("0x") else "0x" + h

    async def accept_job(self, job_id: int) -> Dict[str, Any]:
        """
        Accept a job on-chain (PaymentLocked -> InProgress).

        Args:
            job_id: The job ID to accept

        Returns:
            Dict with success status and transaction hash
        """
        try:
            client = self._get_client()
            result = await asyncio.to_thread(client.accept_job, job_id)
            tx_hash = result["transactionHash"]
            self._accept_tx_hashes[job_id] = tx_hash
            logger.info(f"[ApexJobOps] acceptJob({job_id}) success: {tx_hash}")
            return {"success": True, "txHash": tx_hash}
        except Exception as e:
            logger.error(f"[ApexJobOps] acceptJob({job_id}) failed: {e}")
            return {"success": False, "error": str(e)}

    async def submit_result(
        self,
        job_id: int,
        response_content: str,
        negotiation_request: Optional[Dict] = None,
        negotiation_response: Optional[Dict] = None,
        agent_id: int = 0,
        timestamps: Optional[Dict] = None,
        on_chain_refs: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Submit job result on-chain with automatic ServiceRecord building.

        Args:
            job_id: The job ID
            response_content: Agent's response content
            negotiation_request: Original negotiation request dict
            negotiation_response: Negotiation response dict with price
            agent_id: Agent ID (optional, will be read from job if 0)
            timestamps: Optional timestamp data dict
            on_chain_refs: Optional on-chain reference hashes

        Returns:
            Dict with success status, transaction hash, and data URL
        """
        try:
            client = self._get_client()
            now = int(time.time())
            refs = on_chain_refs or {}

            negotiation_request_hash = ""
            negotiation_response_hash = ""

            if negotiation_request:
                negotiation_request_hash = self._ensure_0x(Web3.keccak(
                    text=json.dumps(negotiation_request, sort_keys=True, separators=(",", ":"))
                ).hex())
            if negotiation_response:
                negotiation_response_hash = self._ensure_0x(Web3.keccak(
                    text=json.dumps(negotiation_response, sort_keys=True, separators=(",", ":"))
                ).hex())

            record = ServiceRecord(
                job_id=job_id,
                agent_id=agent_id,
                chain_id=self._chain_id,
                contract_address=self._apex_address,
                negotiation=NegotiationData(
                    request=negotiation_request,
                    request_hash=negotiation_request_hash,
                    response=negotiation_response,
                    response_hash=negotiation_response_hash,
                ) if negotiation_request or negotiation_response else None,
                service=ServiceData(
                    response_content=response_content,
                ),
                timestamps=TimestampData(
                    negotiated_at=(timestamps or {}).get("negotiated_at", 0),
                    requested_at=(timestamps or {}).get("requested_at", 0),
                    responded_at=(timestamps or {}).get("responded_at", now),
                    submitted_at=now,
                ),
                on_chain=OnChainReferences(
                    approve_tx_hash=self._ensure_0x(refs.get("approve_tx_hash", "") or ""),
                    create_job_tx_hash=self._ensure_0x(refs.get("create_job_tx_hash", "") or ""),
                    accept_job_tx_hash=self._ensure_0x(
                        refs.get("accept_job_tx_hash", "") or
                        self._accept_tx_hashes.get(job_id, "")
                    ),
                ),
            )

            result = await asyncio.to_thread(
                client.submit_result_with_record, record, self._storage
            )

            logger.info(f"[ApexJobOps] submitResult({job_id}) success: {result['transactionHash']}")
            return {
                "success": True,
                "txHash": result["transactionHash"],
                "dataUrl": result.get("dataUrl", ""),
            }

        except Exception as e:
            logger.error(f"[ApexJobOps] submitResult({job_id}) failed: {e}")
            return {"success": False, "error": str(e)}

    async def reject_job(
        self,
        job_id: int,
        reason: str = "Agent execution failed",
        reason_code: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        """
        Reject a job on-chain (refunds client).

        Only works for jobs in PaymentLocked phase.

        Args:
            job_id: The job ID to reject
            reason: Human-readable rejection reason
            reason_code: Optional reason code bytes (default: keccak256("EXECUTION_FAILED"))

        Returns:
            Dict with success status and transaction hash
        """
        try:
            client = self._get_client()
            if reason_code is None:
                reason_code = Web3.keccak(text="EXECUTION_FAILED")

            result = await asyncio.to_thread(
                client.reject_job, job_id, reason_code, reason
            )
            logger.info(f"[ApexJobOps] rejectJob({job_id}) success: {result['transactionHash']}")
            return {"success": True, "txHash": result["transactionHash"]}

        except Exception as e:
            logger.error(f"[ApexJobOps] rejectJob({job_id}) failed: {e}")
            return {"success": False, "error": str(e)}

    async def get_job(self, job_id: int) -> Dict[str, Any]:
        """
        Get job details from chain.

        Args:
            job_id: The job ID to query

        Returns:
            Job details dict
        """
        client = self._get_client()
        return await asyncio.to_thread(client.get_job, job_id)

    async def get_job_phase(self, job_id: int) -> JobPhase:
        """
        Get job phase from chain.

        Args:
            job_id: The job ID to query

        Returns:
            JobPhase enum value
        """
        client = self._get_client()
        return await asyncio.to_thread(client.get_job_phase, job_id)

    def get_accept_tx_hash(self, job_id: int) -> str:
        """Get cached accept transaction hash for a job."""
        return self._accept_tx_hashes.get(job_id, "")

    def set_accept_tx_hash(self, job_id: int, tx_hash: str) -> None:
        """Cache accept transaction hash for a job."""
        self._accept_tx_hashes[job_id] = tx_hash

    @property
    def apex_client(self) -> ApexClient:
        """Get the underlying ApexClient instance."""
        return self._get_client()
