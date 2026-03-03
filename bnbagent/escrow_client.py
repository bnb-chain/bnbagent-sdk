"""
EscrowUpgradeable (Bazaar) contract interaction client.

Provides typed Python methods wrapping the on-chain EscrowUpgradeable contract:
  createJobAndLock, acceptJob, rejectJob, submitResult,
  settleAssertion, cancelExpired, getJob.
"""

import json
import logging
from enum import IntEnum
from pathlib import Path
from typing import Optional, Dict, Any

from web3 import Web3
from web3.contract import Contract

logger = logging.getLogger(__name__)


class JobPhase(IntEnum):
    NONE = 0
    PAYMENT_LOCKED = 1
    IN_PROGRESS = 2
    ASSERTING = 3
    DISPUTED = 4
    COMPLETED = 5
    REFUNDED = 6
    CANCELLED = 7


class SettlementType(IntEnum):
    NONE = 0
    UNCHALLENGED = 1
    DISPUTE_AGENT_WON = 2
    DISPUTE_CLIENT_WON = 3
    AGENT_TIMED_OUT = 4
    AGENT_REJECTED = 5
    CLIENT_APPROVED = 6


def _load_escrow_abi() -> list:
    abi_path = Path(__file__).parent / "abis" / "Escrow.json"
    with open(abi_path) as f:
        return json.load(f)


class EscrowClient:
    """
    Python client for the EscrowUpgradeable contract.

    Wraps all public functions for both client-side and agent-side operations.
    """

    def __init__(
        self,
        web3: Web3,
        contract_address: str,
        private_key: Optional[str] = None,
        abi: Optional[list] = None,
    ):
        self.w3 = web3
        self.address = Web3.to_checksum_address(contract_address)

        if abi is None:
            abi = _load_escrow_abi()

        self.contract: Contract = self.w3.eth.contract(
            address=self.address, abi=abi
        )
        self._private_key = private_key
        self._account = (
            self.w3.eth.account.from_key(private_key).address
            if private_key
            else None
        )

    def _send_tx(self, fn, value: int = 0) -> Dict[str, Any]:
        """Build, sign, and send a transaction."""
        if not self._private_key:
            raise RuntimeError("private_key required for write operations")

        tx = fn.build_transaction({
            "from": self._account,
            "nonce": self.w3.eth.get_transaction_count(self._account),
            "gas": 500_000,
            "value": value,
        })
        signed = self.w3.eth.account.sign_transaction(tx, self._private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        return {
            "transactionHash": receipt["transactionHash"].hex(),
            "status": receipt["status"],
            "receipt": receipt,
        }

    # ── Client functions ──

    def create_job_and_lock(
        self,
        agent_id: int,
        request_hash: bytes,
        amount: int,
    ) -> Dict[str, Any]:
        """Create a job and lock ERC20 payment. Caller must approve() first."""
        fn = self.contract.functions.createJobAndLock(
            agent_id, request_hash, amount
        )
        result = self._send_tx(fn)
        logs = self.contract.events.JobCreated().process_receipt(result["receipt"])
        if logs:
            result["jobId"] = logs[0]["args"]["jobId"]
        return result

    def cancel_expired(self, job_id: int) -> Dict[str, Any]:
        fn = self.contract.functions.cancelExpired(job_id)
        return self._send_tx(fn)

    # ── Agent functions ──

    def accept_job(self, job_id: int) -> Dict[str, Any]:
        fn = self.contract.functions.acceptJob(job_id)
        return self._send_tx(fn)

    def reject_job(
        self, job_id: int, reason_code: bytes, reason_message: str = ""
    ) -> Dict[str, Any]:
        fn = self.contract.functions.rejectJob(job_id, reason_code, reason_message)
        return self._send_tx(fn)

    def submit_result(
        self,
        job_id: int,
        result_hash: bytes,
        response_hash: bytes,
        data_url: str,
    ) -> Dict[str, Any]:
        fn = self.contract.functions.submitResult(
            job_id, result_hash, response_hash, data_url
        )
        return self._send_tx(fn)

    # ── Permissionless ──

    def settle_assertion(self, job_id: int) -> Dict[str, Any]:
        fn = self.contract.functions.settleAssertion(job_id)
        return self._send_tx(fn)

    # ── View functions ──

    def get_job(self, job_id: int) -> Dict[str, Any]:
        raw = self.contract.functions.getJob(job_id).call()
        return {
            "jobId": raw[0],
            "client": raw[1],
            "agentId": raw[2],
            "agentOwner": raw[3],
            "agreedPrice": raw[4],
            "assertionBond": raw[5],
            "phase": JobPhase(raw[6]),
            "settlement": SettlementType(raw[7]),
            "requestHash": raw[8],
            "responseHash": raw[9],
            "resultHash": raw[10],
            "assertionId": raw[11],
            "createdAt": raw[12],
            "updatedAt": raw[13],
            "acceptDeadline": raw[14],
            "submitDeadline": raw[15],
        }

    def get_job_phase(self, job_id: int) -> JobPhase:
        return JobPhase(self.contract.functions.getJobPhase(job_id).call())

    def get_assertion_id_for_job(self, job_id: int) -> bytes:
        return self.contract.functions.getAssertionIdForJob(job_id).call()

    def min_service_fee(self) -> int:
        return self.contract.functions.minServiceFee().call()

    def next_job_id(self) -> int:
        return self.contract.functions.nextJobId().call()
