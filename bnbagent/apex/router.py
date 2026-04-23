"""Thin wrapper around ``EvaluatorRouterUpgradeable``.

The Router acts as ``job.evaluator`` and ``job.hook`` for every job that is
registered with it. Its two primary public-surface methods are:

- ``register_job(jobId, policy)`` — client binds a whitelisted policy after
  ``createJob`` and before ``fund``.
- ``settle(jobId)`` — permissionless; pulls the verdict from the policy and
  applies it to the kernel (``complete`` or ``reject``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from web3 import Web3
from web3.contract import Contract

from ..core.contract_mixin import ContractClientMixin
from ..wallets.wallet_provider import WalletProvider
from .types import Verdict


def _load_abi() -> list:
    abi_path = Path(__file__).parent / "abis" / "EvaluatorRouter.json"
    return json.loads(abi_path.read_text())


class RouterClient(ContractClientMixin):
    """Low-level client for ``EvaluatorRouterUpgradeable``."""

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

    def register_job(self, job_id: int, policy: str) -> dict[str, Any]:
        """Bind ``policy`` to ``job_id``. Client-only, Open-only, single-shot."""
        fn = self.contract.functions.registerJob(
            job_id, Web3.to_checksum_address(policy)
        )
        return self._send_tx(fn)

    def settle(self, job_id: int, evidence: bytes = b"") -> dict[str, Any]:
        """Permissionless: pull the policy verdict and apply it to the kernel."""
        fn = self.contract.functions.settle(job_id, evidence)
        return self._send_tx(fn)

    # ------------------------------------------------------------------ views

    def commerce(self) -> str:
        return self._call_with_retry(self.contract.functions.commerce())

    def job_policy(self, job_id: int) -> str:
        return self._call_with_retry(self.contract.functions.jobPolicy(job_id))

    def policy_whitelist(self, policy: str) -> bool:
        return self._call_with_retry(
            self.contract.functions.policyWhitelist(Web3.to_checksum_address(policy))
        )

    def paused(self) -> bool:
        return self._call_with_retry(self.contract.functions.paused())

    # ------------------------------------------------------------ event helpers

    def get_job_registered_events(
        self,
        from_block: int,
        to_block: str = "latest",
        client: str | None = None,
    ) -> list[dict[str, Any]]:
        filt: dict[str, Any] = {}
        if client:
            filt["client"] = Web3.to_checksum_address(client)
        logs = self.contract.events.JobRegistered().get_logs(
            from_block=from_block,
            to_block=to_block,
            argument_filters=filt if filt else None,
        )
        return [
            {
                "jobId": log["args"]["jobId"],
                "policy": log["args"]["policy"],
                "client": log["args"]["client"],
                "blockNumber": log["blockNumber"],
                "transactionHash": log["transactionHash"].hex(),
            }
            for log in logs
        ]

    def get_job_settled_events(
        self,
        from_block: int,
        to_block: str = "latest",
        verdict: Verdict | int | None = None,
    ) -> list[dict[str, Any]]:
        filt: dict[str, Any] = {}
        if verdict is not None:
            filt["verdict"] = int(verdict)
        logs = self.contract.events.JobSettled().get_logs(
            from_block=from_block,
            to_block=to_block,
            argument_filters=filt if filt else None,
        )
        return [
            {
                "jobId": log["args"]["jobId"],
                "verdict": Verdict(log["args"]["verdict"]),
                "reason": log["args"]["reason"],
                "blockNumber": log["blockNumber"],
                "transactionHash": log["transactionHash"].hex(),
            }
            for log in logs
        ]
