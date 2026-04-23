"""Thin Python wrapper around ``AgenticCommerceUpgradeable`` (the APEX v1 kernel).

This client is **low-level**: each method maps 1:1 to a Solidity function.
Approval management, batching, and auto-settle are intentionally left to
``APEXClient`` (the facade) — ``CommerceClient`` only speaks raw kernel.

Synchronous by design; async callers should use ``asyncio.to_thread(...)``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from web3 import Web3
from web3.contract import Contract

from ..core.contract_mixin import ContractClientMixin
from ..wallets.wallet_provider import WalletProvider
from .types import ZERO_ADDRESS, ZERO_REASON, Job, JobStatus

logger = logging.getLogger(__name__)


def _load_abi() -> list:
    abi_path = Path(__file__).parent / "abis" / "AgenticCommerce.json"
    return json.loads(abi_path.read_text())


def _decode_job(raw: Any) -> Job:
    """Convert the tuple returned by ``getJob`` into a ``Job`` dataclass."""
    return Job(
        id=raw[0],
        client=Web3.to_checksum_address(raw[1]),
        provider=Web3.to_checksum_address(raw[2]),
        evaluator=Web3.to_checksum_address(raw[3]),
        description=raw[4],
        budget=raw[5],
        expired_at=raw[6],
        status=JobStatus(raw[7]),
        hook=Web3.to_checksum_address(raw[8]),
    )


class CommerceClient(ContractClientMixin):
    """Low-level client for the ``AgenticCommerceUpgradeable`` kernel."""

    def __init__(
        self,
        web3: Web3,
        contract_address: str,
        wallet_provider: WalletProvider | None = None,
        *,
        abi: list | None = None,
    ) -> None:
        self.w3 = web3
        self.address = Web3.to_checksum_address(contract_address)
        self.contract: Contract = self.w3.eth.contract(
            address=self.address, abi=abi or _load_abi()
        )
        self._wallet_provider = wallet_provider
        self._account = wallet_provider.address if wallet_provider is not None else None

    # ----------------------------------------------------------------- writes

    def create_job(
        self,
        *,
        provider: str,
        evaluator: str,
        expired_at: int,
        description: str,
        hook: str = ZERO_ADDRESS,
    ) -> dict[str, Any]:
        """Create a new job (``Open`` state) and return a dict with ``jobId``."""
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

    def set_provider(
        self,
        job_id: int,
        provider: str,
        opt_params: bytes = b"",
    ) -> dict[str, Any]:
        fn = self.contract.functions.setProvider(
            job_id, Web3.to_checksum_address(provider), opt_params
        )
        return self._send_tx(fn)

    def set_budget(
        self,
        job_id: int,
        amount: int,
        opt_params: bytes = b"",
    ) -> dict[str, Any]:
        fn = self.contract.functions.setBudget(job_id, amount, opt_params)
        return self._send_tx(fn)

    def fund(
        self,
        job_id: int,
        expected_budget: int,
        opt_params: bytes = b"",
    ) -> dict[str, Any]:
        """Deposit escrow. Caller MUST have approved ``expected_budget`` first."""
        fn = self.contract.functions.fund(job_id, expected_budget, opt_params)
        return self._send_tx(fn)

    def submit(
        self,
        job_id: int,
        deliverable: bytes,
        opt_params: bytes = b"",
    ) -> dict[str, Any]:
        if len(deliverable) != 32:
            raise ValueError("deliverable must be exactly 32 bytes")
        fn = self.contract.functions.submit(job_id, deliverable, opt_params)
        return self._send_tx(fn)

    def complete(
        self,
        job_id: int,
        reason: bytes = ZERO_REASON,
        opt_params: bytes = b"",
    ) -> dict[str, Any]:
        """Evaluator-only. Routed jobs are completed via ``RouterClient.settle``."""
        if len(reason) != 32:
            raise ValueError("reason must be exactly 32 bytes")
        fn = self.contract.functions.complete(job_id, reason, opt_params)
        return self._send_tx(fn)

    def reject(
        self,
        job_id: int,
        reason: bytes = ZERO_REASON,
        opt_params: bytes = b"",
    ) -> dict[str, Any]:
        """Client (while Open) or evaluator (while Funded/Submitted)."""
        if len(reason) != 32:
            raise ValueError("reason must be exactly 32 bytes")
        fn = self.contract.functions.reject(job_id, reason, opt_params)
        return self._send_tx(fn)

    def claim_refund(self, job_id: int) -> dict[str, Any]:
        """Permissionless refund path after ``expiredAt``. Not pausable, no hook."""
        fn = self.contract.functions.claimRefund(job_id)
        return self._send_tx(fn)

    # ------------------------------------------------------------------ views

    def get_job(self, job_id: int) -> Job:
        raw = self._call_with_retry(self.contract.functions.getJob(job_id))
        return _decode_job(raw)

    def job_counter(self) -> int:
        return self._call_with_retry(self.contract.functions.jobCounter())

    def payment_token(self) -> str:
        return self._call_with_retry(self.contract.functions.paymentToken())

    def platform_fee_bp(self) -> int:
        return self._call_with_retry(self.contract.functions.platformFeeBP())

    def platform_treasury(self) -> str:
        return self._call_with_retry(self.contract.functions.platformTreasury())

    def job_has_budget(self, job_id: int) -> bool:
        return self._call_with_retry(self.contract.functions.jobHasBudget(job_id))

    # Batch read via Multicall3 — optional convenience for indexers.
    def get_jobs_batch(self, job_ids: list[int]) -> list[Job | None]:
        if not job_ids:
            return []
        from ..core.multicall import multicall_read

        raw_results = multicall_read(
            w3=self.w3,
            contract=self.contract,
            function_name="getJob",
            call_args_list=[(jid,) for jid in job_ids],
        )
        jobs: list[Job | None] = []
        for success, decoded in raw_results:
            if not success or not decoded:
                jobs.append(None)
                continue
            try:
                jobs.append(_decode_job(decoded))
            except Exception:
                jobs.append(None)
        return jobs

    # --------------------------------------------------------- event helpers

    def get_job_funded_events(
        self,
        from_block: int,
        to_block: str = "latest",
        provider: str | None = None,
    ) -> list[dict[str, Any]]:
        event_filter = {}
        if provider:
            event_filter["provider"] = Web3.to_checksum_address(provider)
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

    def get_submit_data_url(self, job_id: int) -> str | None:
        """Recover the data URL from a ``submit`` transaction's ``optParams``.

        The URL is not stored on-chain (only its keccak256 hash is). We
        locate the submit tx via ``JobSubmitted`` (indexed by ``jobId``)
        and decode ``optParams`` from the calldata.
        """
        try:
            current_block = self.w3.eth.block_number
            from_block = max(0, current_block - 50_000)
        except Exception:
            from_block = 0

        logs = self.contract.events.JobSubmitted().get_logs(
            from_block=from_block,
            to_block="latest",
            argument_filters={"jobId": job_id},
        )
        if not logs:
            return None

        tx = self.w3.eth.get_transaction(logs[0]["transactionHash"])
        try:
            _, params = self.contract.decode_function_input(tx.input)
        except (ValueError, KeyError) as exc:
            logger.warning(f"[CommerceClient] decode_function_input failed for job {job_id}: {exc}")
            return None

        opt_params: bytes = params.get("optParams", b"")
        if not opt_params:
            return None
        try:
            return opt_params.decode("utf-8")
        except UnicodeDecodeError:
            return None
