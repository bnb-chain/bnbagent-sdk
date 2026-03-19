"""
APEX Evaluator contract interaction client.

The evaluator is a pluggable component in the APEX protocol — any contract
implementing the evaluator interface can be used. The current implementation
wraps a UMA OOv3-based evaluator:
  - Query assertion info, liveness, settleable status
  - Settle jobs after liveness period
  - Deposit/withdraw bond
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from web3 import Web3
from web3.contract import Contract

from ..core.contract_mixin import ContractClientMixin

if TYPE_CHECKING:
    from ..wallets.wallet_provider import WalletProvider

logger = logging.getLogger(__name__)


@dataclass
class AssertionInfo:
    """Assertion information for a job."""

    assertion_id: bytes
    initiated: bool
    disputed: bool
    liveness_end: int
    settleable: bool


def _load_apex_evaluator_abi() -> list:
    abi_path = Path(__file__).parent / "abis" / "APEXEvaluator.json"
    try:
        with open(abi_path) as f:
            return json.load(f)
    except FileNotFoundError:
        raise RuntimeError(f"ABI file not found: {abi_path}") from None
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON in ABI file {abi_path}: {e}") from e


class APEXEvaluatorClient(ContractClientMixin):
    """
    Python client for the APEX Evaluator contract.

    Used by agents and keepers to:
    - Query assertion status
    - Settle jobs after liveness
    - Monitor bond balance
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
            abi = _load_apex_evaluator_abi()

        self.contract: Contract = self.w3.eth.contract(address=self.address, abi=abi)
        self._private_key = private_key
        self._wallet_provider = wallet_provider
        if wallet_provider is not None:
            self._account = wallet_provider.address
        else:
            self._account = (
                self.w3.eth.account.from_key(private_key).address if private_key else None
            )

    def _send_tx(self, fn, value: int = 0, gas: int = 500_000) -> dict[str, Any]:
        """Override default gas limit (500k is sufficient for evaluator ops)."""
        return super()._send_tx(fn, value=value, gas=gas)

    # ── Query Functions ──

    def get_assertion_info(self, job_id: int) -> AssertionInfo:
        """
        Get assertion info for a job.

        Returns:
            AssertionInfo with assertion_id, initiated, disputed, liveness_end, settleable
        """
        result = self._call_with_retry(self.contract.functions.getAssertionInfo(job_id))
        return AssertionInfo(
            assertion_id=result[0],
            initiated=result[1],
            disputed=result[2],
            liveness_end=result[3],
            settleable=result[4],
        )

    def get_liveness_end(self, job_id: int) -> int:
        """Get timestamp when liveness period ends for a job."""
        return self._call_with_retry(self.contract.functions.getLivenessEnd(job_id))

    def is_settleable(self, job_id: int) -> bool:
        """Check if a job's assertion can be settled now."""
        return self._call_with_retry(self.contract.functions.isSettleable(job_id))

    def get_minimum_bond(self) -> int:
        """Get the minimum bond required for assertions."""
        return self._call_with_retry(self.contract.functions.getMinimumBond())

    def get_bond_balance(self) -> int:
        """Get current bond balance in the contract."""
        return self._call_with_retry(self.contract.functions.bondBalance())

    def get_liveness(self) -> int:
        """Get current liveness period in seconds."""
        return self._call_with_retry(self.contract.functions.liveness())

    def job_assertion_initiated(self, job_id: int) -> bool:
        """Check if assertion has been initiated for a job."""
        return self._call_with_retry(self.contract.functions.jobAssertionInitiated(job_id))

    def job_disputed(self, job_id: int) -> bool:
        """Check if a job's assertion has been disputed."""
        return self._call_with_retry(self.contract.functions.jobDisputed(job_id))

    def job_to_assertion(self, job_id: int) -> bytes:
        """Get assertion ID for a job."""
        return self._call_with_retry(self.contract.functions.jobToAssertion(job_id))

    def assertion_to_job(self, assertion_id: bytes) -> int:
        """Get job ID for an assertion."""
        return self._call_with_retry(self.contract.functions.assertionToJob(assertion_id))

    # ── Write Functions ──

    def settle_job(self, job_id: int) -> dict[str, Any]:
        """
        Settle an assertion after liveness period.

        Anyone can call this. Will trigger OOv3 callback which completes/rejects the job.
        """
        fn = self.contract.functions.settleJob(job_id)
        return self._send_tx(fn)

    def initiate_assertion(self, job_id: int) -> dict[str, Any]:
        """
        Manually initiate an assertion for a submitted job.

        Note: Normally this is auto-triggered by afterAction hook.
        Only needed if hook wasn't set when job was created.
        """
        fn = self.contract.functions.initiateAssertion(job_id)
        return self._send_tx(fn)

    def deposit_bond(self, amount: int) -> dict[str, Any]:
        """
        Deposit bond tokens into the contract.

        Anyone can call this to fund assertions.
        """
        fn = self.contract.functions.depositBond(amount)
        return self._send_tx(fn)

    def withdraw_bond(self, amount: int) -> dict[str, Any]:
        """
        Withdraw bond tokens from the contract.

        Only owner can call this.
        """
        fn = self.contract.functions.withdrawBond(amount)
        return self._send_tx(fn)

    def set_bond_token(self, new_bond_token: str) -> dict[str, Any]:
        """
        Update the bond token address.

        Only owner can call this, and bondBalance must be 0.
        The new token must be whitelisted by UMA OOv3.
        """
        fn = self.contract.functions.setBondToken(Web3.to_checksum_address(new_bond_token))
        return self._send_tx(fn)

    # ── Config Queries ──

    def get_erc8183_address(self) -> str:
        """Get the ERC-8183 contract address."""
        return self._call_with_retry(self.contract.functions.erc8183())

    def get_oov3_address(self) -> str:
        """Get the UMA OOv3 contract address."""
        return self._call_with_retry(self.contract.functions.oov3())

    def get_bond_token_address(self) -> str:
        """Get the bond token address."""
        return self._call_with_retry(self.contract.functions.bondToken())
