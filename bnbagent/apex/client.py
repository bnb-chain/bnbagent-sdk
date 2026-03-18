"""
ERC-8183 (Agentic Commerce) contract interaction client.

Provides typed Python methods wrapping the on-chain AgenticCommerceUpgradeable contract:
  createJob, setBudget, fund, setProvider, submit, complete, reject, claimRefund, getJob.

This client is intentionally **synchronous**.  web3.py's HTTPProvider performs
blocking HTTP calls and there is no production-ready async transport.  Async
callers (e.g. APEXJobOps) bridge via ``asyncio.to_thread()`` to avoid blocking
the event loop.
"""

from __future__ import annotations

import json
import logging
import time
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from web3 import Web3
from web3.contract import Contract

from ..core.contract_mixin import ContractClientMixin

if TYPE_CHECKING:
    from ..wallets.wallet_provider import WalletProvider

logger = logging.getLogger(__name__)

# Job expiry calculation: liveness_period + 72 hours (DVM buffer)
#
# Components:
#   - OOv3 liveness period: 30 min default (configurable)
#   - DVM dispute resolution: 48-96 hours (use 72h as safe upper bound)
#
# Jobs can be refunded after expiry if not completed/rejected,
# so expiry must be long enough to cover worst-case dispute scenario.
DEFAULT_LIVENESS_SECONDS = 30 * 60  # 30 minutes (OOv3 default)
DVM_BUFFER_SECONDS = 72 * 60 * 60  # 72 hours for DVM disputes
DEFAULT_JOB_EXPIRY_SECONDS = DEFAULT_LIVENESS_SECONDS + DVM_BUFFER_SECONDS  # 72.5 hours


def get_default_expiry(liveness_seconds: int = DEFAULT_LIVENESS_SECONDS) -> int:
    """
    Get default job expiry timestamp.

    Formula: current_time + liveness_period + 72_hours

    Args:
        liveness_seconds: OOv3 liveness period in seconds (default: 1800 = 30 min)

    Returns:
        int: Unix timestamp for job expiry
    """
    return int(time.time()) + liveness_seconds + DVM_BUFFER_SECONDS


class APEXStatus(IntEnum):
    """ERC-8183 Job status enum."""

    NONE = 0
    OPEN = 1
    FUNDED = 2
    SUBMITTED = 3
    COMPLETED = 4
    REJECTED = 5
    EXPIRED = 6


def _load_erc8183_abi() -> list:
    abi_path = Path(__file__).parent / "abis" / "ERC8183.json"
    try:
        with open(abi_path) as f:
            return json.load(f)
    except FileNotFoundError:
        raise RuntimeError(f"ABI file not found: {abi_path}") from None
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON in ABI file {abi_path}: {e}") from e


class APEXClient(ContractClientMixin):
    """
    Python client for the ERC-8183 (Agentic Commerce) contract.

    Wraps all public functions for both client-side and provider-side (agent) operations.
    """

    def __init__(
        self,
        web3: Web3,
        contract_address: str,
        private_key: str | None = None,
        abi: list | None = None,
        wallet_provider: WalletProvider | None = None,
    ):
        self.w3 = web3
        self.address = Web3.to_checksum_address(contract_address)

        if abi is None:
            abi = _load_erc8183_abi()

        self.contract: Contract = self.w3.eth.contract(address=self.address, abi=abi)
        self._private_key = private_key
        self._wallet_provider = wallet_provider
        if wallet_provider is not None:
            self._account = wallet_provider.address
        else:
            self._account = (
                self.w3.eth.account.from_key(private_key).address if private_key else None
            )

    # ── Client functions ──

    def create_job(
        self,
        provider: str,
        evaluator: str,
        expired_at: int,
        description: str,
        hook: str = "0x0000000000000000000000000000000000000000",
    ) -> dict[str, Any]:
        """
        Create a new job. Returns jobId from event.

        Args:
            provider: Provider (agent) address. Can be zero address for open jobs.
            evaluator: Evaluator address. Can be zero address if client is evaluator.
            expired_at: Unix timestamp when job expires.
            description: Job description string.
            hook: Optional hook contract address.
        """
        fn = self.contract.functions.createJob(
            Web3.to_checksum_address(provider),
            Web3.to_checksum_address(evaluator),
            expired_at,
            description,
            Web3.to_checksum_address(hook),
        )
        result = self._send_tx(fn)
        logs = self.contract.events.JobCreated().process_receipt(result["receipt"])
        if logs:
            result["jobId"] = logs[0]["args"]["jobId"]
        return result

    def set_budget(
        self,
        job_id: int,
        amount: int,
        opt_params: bytes = b"",
    ) -> dict[str, Any]:
        """Set the budget for a job. Must be called before fund()."""
        fn = self.contract.functions.setBudget(job_id, amount, opt_params)
        return self._send_tx(fn)

    def fund(
        self,
        job_id: int,
        expected_budget: int,
        opt_params: bytes = b"",
    ) -> dict[str, Any]:
        """
        Fund a job. Caller must approve() the payment token first.
        Transfers funds from client to contract escrow.
        """
        fn = self.contract.functions.fund(job_id, expected_budget, opt_params)
        return self._send_tx(fn)

    def set_provider(
        self,
        job_id: int,
        provider: str,
        opt_params: bytes = b"",
    ) -> dict[str, Any]:
        """Set the provider for an open job (provider was zero address)."""
        fn = self.contract.functions.setProvider(
            job_id,
            Web3.to_checksum_address(provider),
            opt_params,
        )
        return self._send_tx(fn)

    def complete(
        self,
        job_id: int,
        reason: bytes = b"\x00" * 32,
        opt_params: bytes = b"",
    ) -> dict[str, Any]:
        """
        Complete/approve a job (evaluator only).
        Releases payment to provider.
        """
        fn = self.contract.functions.complete(job_id, reason, opt_params)
        return self._send_tx(fn)

    def reject(
        self,
        job_id: int,
        reason: bytes = b"\x00" * 32,
        opt_params: bytes = b"",
    ) -> dict[str, Any]:
        """
        Reject a job (client or evaluator).
        Refunds payment to client.
        """
        fn = self.contract.functions.reject(job_id, reason, opt_params)
        return self._send_tx(fn)

    def claim_refund(self, job_id: int) -> dict[str, Any]:
        """Claim refund for an expired job."""
        fn = self.contract.functions.claimRefund(job_id)
        return self._send_tx(fn)

    def claim_pending(self) -> dict[str, Any]:
        """Claim any pending withdrawals for the caller."""
        fn = self.contract.functions.claimPending()
        return self._send_tx(fn)

    # ── Provider (Agent) functions ──

    def submit(
        self,
        job_id: int,
        deliverable: bytes,
        opt_params: bytes = b"",
    ) -> dict[str, Any]:
        """
        Submit work for a funded job. Provider only.

        Args:
            job_id: The job ID.
            deliverable: 32-byte hash of the deliverable (e.g., IPFS CID hash).
            opt_params: Optional parameters for hooks.
        """
        if len(deliverable) != 32:
            raise ValueError("deliverable must be exactly 32 bytes")
        fn = self.contract.functions.submit(job_id, deliverable, opt_params)
        return self._send_tx(fn)

    # ── View functions ──

    def get_job(self, job_id: int) -> dict[str, Any]:
        """Get job details by ID."""
        raw = self._call_with_retry(self.contract.functions.getJob(job_id))
        return {
            "jobId": job_id,
            "client": raw[0],
            "provider": raw[1],
            "evaluator": raw[2],
            "hook": raw[3],
            "budget": raw[4],
            "expiredAt": raw[5],
            "status": APEXStatus(raw[6]),
            "deliverable": raw[7],
            "description": raw[8],
        }

    def get_job_status(self, job_id: int) -> APEXStatus:
        """Get the status of a job."""
        return APEXStatus(self._call_with_retry(self.contract.functions.getJobStatus(job_id)))

    def payment_token(self) -> str:
        """Get the payment token address configured in the ERC-8183 contract."""
        return self._call_with_retry(self.contract.functions.paymentToken())

    def min_budget(self) -> int:
        """Get the minimum budget required for jobs."""
        return self._call_with_retry(self.contract.functions.minBudget())

    def next_job_id(self) -> int:
        """Get the next job ID that will be assigned."""
        return self._call_with_retry(self.contract.functions.nextJobId())

    def pending_withdrawals(self, account: str) -> int:
        """Get pending withdrawal amount for an account."""
        return self._call_with_retry(
            self.contract.functions.pendingWithdrawals(Web3.to_checksum_address(account))
        )

    # ── Event helpers ──

    def get_job_funded_events(
        self,
        from_block: int,
        to_block: str = "latest",
        provider: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get JobFunded events, optionally filtered by provider.

        This is useful for agents to discover jobs assigned to them.

        Note: from_block is required - caller should calculate appropriate range
        to avoid exceeding RPC block range limits (e.g., BSC: 50000 blocks).
        """
        event_filter = {}
        if provider:
            event_filter["client"] = Web3.to_checksum_address(provider)

        logs = self.contract.events.JobFunded().get_logs(
            from_block=from_block,
            to_block=to_block,
            argument_filters=event_filter if event_filter else None,
        )

        return [
            {
                "jobId": log["args"]["jobId"],
                "client": log["args"]["client"],
                "amount": log["args"]["amount"],
                "blockNumber": log["blockNumber"],
                "transactionHash": log["transactionHash"].hex(),
            }
            for log in logs
        ]

    def get_job_created_events(
        self,
        from_block: int,
        to_block: str = "latest",
    ) -> list[dict[str, Any]]:
        """
        Get JobCreated events.

        Note: from_block is required - caller should calculate appropriate range
        to avoid exceeding RPC block range limits (e.g., BSC: 50000 blocks).
        """
        logs = self.contract.events.JobCreated().get_logs(
            from_block=from_block,
            to_block=to_block,
        )

        return [
            {
                "jobId": log["args"]["jobId"],
                "client": log["args"]["client"],
                "provider": log["args"]["provider"],
                "evaluator": log["args"]["evaluator"],
                "expiredAt": log["args"]["expiredAt"],
                "blockNumber": log["blockNumber"],
                "transactionHash": log["transactionHash"].hex(),
            }
            for log in logs
        ]

    def get_budget_set_events(
        self,
        job_id: int | None = None,
        from_block: int = 0,
        to_block: str = "latest",
    ) -> list[dict[str, Any]]:
        """
        Get BudgetSet events for tracking negotiation history.

        This is useful for tracking budget changes during negotiation phase.
        Both client and provider can call setBudget() while job is in Open status.

        Args:
            job_id: Optional job ID to filter events (indexed, so RPC can handle broader range)
            from_block: Starting block number (when job_id is provided, 0 is usually OK)
            to_block: Ending block number or "latest"

        Returns:
            List of BudgetSet events with jobId, amount, blockNumber, transactionHash

        Note: When job_id is None, caller should limit from_block to avoid exceeding
        RPC block range limits (e.g., BSC: 50000 blocks).
        """
        event_filter = {}
        if job_id is not None:
            event_filter["jobId"] = job_id

        logs = self.contract.events.BudgetSet().get_logs(
            from_block=from_block,
            to_block=to_block,
            argument_filters=event_filter if event_filter else None,
        )

        return [
            {
                "jobId": log["args"]["jobId"],
                "amount": log["args"]["amount"],
                "blockNumber": log["blockNumber"],
                "transactionHash": log["transactionHash"].hex(),
            }
            for log in logs
        ]
