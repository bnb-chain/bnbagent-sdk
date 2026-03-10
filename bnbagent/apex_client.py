"""
APEX Protocol contract interaction client.

Provides typed Python methods wrapping the on-chain ApexUpgradeable contract:
  createJobAndLock, acceptJob, rejectJob, submitResult,
  settleAssertion, cancelExpired, getJob.
"""

import json
import logging
import time
from enum import IntEnum
from pathlib import Path
from typing import Optional, Dict, Any

from web3 import Web3
from web3.contract import Contract

logger = logging.getLogger(__name__)

MAX_RETRIES = 5
RETRY_BASE_DELAY = 1.0  # seconds


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


def _load_apex_abi() -> list:
    abi_path = Path(__file__).parent / "abis" / "Apex.json"
    with open(abi_path) as f:
        return json.load(f)


class ApexClient:
    """
    Python client for the APEX Protocol (ApexUpgradeable) contract.

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
            abi = _load_apex_abi()

        self.contract: Contract = self.w3.eth.contract(
            address=self.address, abi=abi
        )
        self._private_key = private_key
        self._account = (
            self.w3.eth.account.from_key(private_key).address
            if private_key
            else None
        )

    def _send_tx(self, fn, value: int = 0, gas: int = 500_000) -> Dict[str, Any]:
        """Build, sign, and send a transaction with retry on rate limit."""
        if not self._private_key:
            raise RuntimeError("private_key required for write operations")

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                tx = fn.build_transaction({
                    "from": self._account,
                    "nonce": self.w3.eth.get_transaction_count(self._account),
                    "gas": gas,
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
            except Exception as e:
                last_error = e
                error_str = str(e).lower()
                is_rate_limit = "429" in error_str or "too many requests" in error_str
                if is_rate_limit and attempt < MAX_RETRIES - 1:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        f"[ApexClient] Rate limited, retry {attempt + 1}/{MAX_RETRIES} "
                        f"in {delay:.1f}s"
                    )
                    time.sleep(delay)
                else:
                    raise

        raise last_error  # type: ignore

    def _call_with_retry(self, fn):
        """Call a read function with retry on rate limit."""
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                return fn.call()
            except Exception as e:
                last_error = e
                error_str = str(e).lower()
                is_rate_limit = "429" in error_str or "too many requests" in error_str
                if is_rate_limit and attempt < MAX_RETRIES - 1:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        f"[ApexClient] Rate limited (read), retry {attempt + 1}/{MAX_RETRIES} "
                        f"in {delay:.1f}s"
                    )
                    time.sleep(delay)
                else:
                    raise
        raise last_error  # type: ignore

    # ── Client functions ──

    def create_job_and_lock(
        self,
        agent_id: int,
        negotiation_request_hash: bytes,
        negotiation_response_hash: bytes,
        amount: int,
    ) -> Dict[str, Any]:
        """Create a job and lock ERC20 payment. Caller must approve() first."""
        fn = self.contract.functions.createJobAndLock(
            agent_id, negotiation_request_hash, negotiation_response_hash, amount
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
        service_record_hash: bytes,
        service_response_hash: bytes,
        data_url: str,
    ) -> Dict[str, Any]:
        fn = self.contract.functions.submitResult(
            job_id, service_record_hash, service_response_hash, data_url
        )
        return self._send_tx(fn, gas=800_000)

    def submit_result_with_record(
        self,
        record: "ServiceRecord",
        storage: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        High-level: compute hashes, upload to storage, call submitResult on-chain.

        IMPORTANT: Upload to storage FIRST to get dataUrl, then pass it to
        submitResult so it's included in the OOv3 assertion claim.
        """
        from web3 import Web3

        hashes = record.compute_hashes()
        service_record_hash = Web3.keccak(text=record.canonical_json())
        # Use service.response_content for serviceResponseHash
        service_response_hash = (
            Web3.keccak(text=record.service.response_content)
            if record.service and record.service.response_content
            else b"\x00" * 32
        )

        # Step 1: Upload to storage FIRST to get dataUrl for the assertion claim
        record._hashes = hashes
        data_url = ""
        if storage:
            d = record.to_dict()
            d["hashes"] = hashes
            if hasattr(storage, "save_sync"):
                data_url = storage.save_sync(d)
            elif hasattr(storage, "save"):
                data_url = storage.save(record)
            logger.info(f"[ApexClient] ServiceRecord uploaded: {data_url}")

        # Step 2: Call submitResult on-chain WITH the dataUrl
        # The dataUrl is embedded in the OOv3 assertion claim as Evidence
        tx_result = self.submit_result(
            record.job_id, service_record_hash, service_response_hash, data_url
        )

        # Step 3: Update record with on-chain references
        if record.on_chain is None:
            from .service_record import OnChainReferences
            record.on_chain = OnChainReferences()
        record.on_chain.submit_result_tx_hash = tx_result["transactionHash"]

        try:
            job = self.get_job(record.job_id)
            assertion_bytes = job.get("assertionId", b"")
            if assertion_bytes:
                record.on_chain.assertion_id = "0x" + assertion_bytes.hex() if isinstance(assertion_bytes, bytes) else str(assertion_bytes)
        except Exception:
            pass

        return {
            "transactionHash": tx_result["transactionHash"],
            "status": tx_result["status"],
            "hashes": hashes,
            "dataUrl": data_url,
        }

    # ── Permissionless ──

    def settle_assertion(self, job_id: int) -> Dict[str, Any]:
        fn = self.contract.functions.settleAssertion(job_id)
        return self._send_tx(fn)

    # ── View functions ──

    def get_job(self, job_id: int) -> Dict[str, Any]:
        raw = self._call_with_retry(self.contract.functions.getJob(job_id))
        return {
            "jobId": raw[0],
            "client": raw[1],
            "agentId": raw[2],
            "agentOwner": raw[3],
            "agreedPrice": raw[4],
            "assertionBond": raw[5],
            "phase": JobPhase(raw[6]),
            "settlement": SettlementType(raw[7]),
            "negotiationRequestHash": raw[8],
            "negotiationResponseHash": raw[9],
            "serviceRecordHash": raw[10],
            "serviceResponseHash": raw[11],
            "assertionId": raw[12],
            "createdAt": raw[13],
            "updatedAt": raw[14],
            "acceptDeadline": raw[15],
            "submitDeadline": raw[16],
        }

    def get_job_phase(self, job_id: int) -> JobPhase:
        return JobPhase(self._call_with_retry(self.contract.functions.getJobPhase(job_id)))

    def get_assertion_id_for_job(self, job_id: int) -> bytes:
        return self._call_with_retry(self.contract.functions.getAssertionIdForJob(job_id))

    def min_service_fee(self) -> int:
        """
        Get minimum service fee from contract.

        This is calculated as: minBond * 10000 / bondRate
        With default 10% bond rate (1000 basis points): minServiceFee = minBond * 10

        The minBond comes from UMA OOv3 contract (currently 1 TUSD on testnet).
        So minServiceFee = 10 TUSD minimum.
        """
        return self._call_with_retry(self.contract.functions.minServiceFee())

    def payment_token(self) -> str:
        """Get the payment token address configured in the Apex contract."""
        return self._call_with_retry(self.contract.functions.paymentToken())

    def next_job_id(self) -> int:
        return self._call_with_retry(self.contract.functions.nextJobId())
