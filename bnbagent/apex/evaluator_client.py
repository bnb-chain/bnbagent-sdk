"""
APEX Evaluator contract interaction client.

The evaluator is a pluggable component in the APEX protocol — any contract
implementing the evaluator interface can be used. The current implementation
wraps a UMA OOv3-based evaluator:
  - Query assertion info, liveness, settleable status
  - Settle jobs after liveness period
  - Deposit/withdraw bond
"""

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any

from web3 import Web3
from web3.contract import Contract

from ..core.nonce_manager import NonceManager

logger = logging.getLogger(__name__)

MAX_RETRIES = 5
RETRY_BASE_DELAY = 1.0


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
        raise RuntimeError(f"ABI file not found: {abi_path}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON in ABI file {abi_path}: {e}")


class APEXEvaluatorClient:
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
        private_key: Optional[str] = None,
        abi: Optional[list] = None,
    ):
        self.w3 = web3
        self.address = Web3.to_checksum_address(contract_address)

        if abi is None:
            abi = _load_apex_evaluator_abi()

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
        """Build, sign, and send a transaction with nonce management and retry."""
        if not self._private_key:
            raise RuntimeError("private_key required for write operations")

        nonce_mgr = NonceManager.for_account(self.w3, self._account)
        last_error = None

        for attempt in range(MAX_RETRIES):
            nonce = nonce_mgr.get_nonce()
            try:
                tx = fn.build_transaction({
                    "from": self._account,
                    "nonce": nonce,
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

                # Nonce error -> re-sync and retry
                if nonce_mgr.handle_error(e, nonce) and attempt < MAX_RETRIES - 1:
                    logger.warning(
                        f"[APEXEvaluatorClient] Nonce error, retry {attempt + 1}/{MAX_RETRIES}"
                    )
                    continue

                # Rate limit -> backoff and retry
                is_rate_limit = "429" in error_str or "too many requests" in error_str
                if is_rate_limit and attempt < MAX_RETRIES - 1:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        f"[APEXEvaluatorClient] Rate limited, retry {attempt + 1}/{MAX_RETRIES} "
                        f"in {delay:.1f}s"
                    )
                    time.sleep(delay)
                    continue

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
                        f"[APEXEvaluatorClient] Rate limited (read), retry {attempt + 1}/{MAX_RETRIES} "
                        f"in {delay:.1f}s"
                    )
                    time.sleep(delay)
                else:
                    raise
        raise last_error  # type: ignore

    # ── Query Functions ──

    def get_assertion_info(self, job_id: int) -> AssertionInfo:
        """
        Get assertion info for a job.
        
        Returns:
            AssertionInfo with assertion_id, initiated, disputed, liveness_end, settleable
        """
        result = self._call_with_retry(
            self.contract.functions.getAssertionInfo(job_id)
        )
        return AssertionInfo(
            assertion_id=result[0],
            initiated=result[1],
            disputed=result[2],
            liveness_end=result[3],
            settleable=result[4],
        )

    def get_liveness_end(self, job_id: int) -> int:
        """Get timestamp when liveness period ends for a job."""
        return self._call_with_retry(
            self.contract.functions.getLivenessEnd(job_id)
        )

    def is_settleable(self, job_id: int) -> bool:
        """Check if a job's assertion can be settled now."""
        return self._call_with_retry(
            self.contract.functions.isSettleable(job_id)
        )

    def get_minimum_bond(self) -> int:
        """Get the minimum bond required for assertions."""
        return self._call_with_retry(
            self.contract.functions.getMinimumBond()
        )

    def get_bond_balance(self) -> int:
        """Get current bond balance in the contract."""
        return self._call_with_retry(
            self.contract.functions.bondBalance()
        )

    def get_liveness(self) -> int:
        """Get current liveness period in seconds."""
        return self._call_with_retry(
            self.contract.functions.liveness()
        )

    def job_assertion_initiated(self, job_id: int) -> bool:
        """Check if assertion has been initiated for a job."""
        return self._call_with_retry(
            self.contract.functions.jobAssertionInitiated(job_id)
        )

    def job_disputed(self, job_id: int) -> bool:
        """Check if a job's assertion has been disputed."""
        return self._call_with_retry(
            self.contract.functions.jobDisputed(job_id)
        )

    def job_to_assertion(self, job_id: int) -> bytes:
        """Get assertion ID for a job."""
        return self._call_with_retry(
            self.contract.functions.jobToAssertion(job_id)
        )

    def assertion_to_job(self, assertion_id: bytes) -> int:
        """Get job ID for an assertion."""
        return self._call_with_retry(
            self.contract.functions.assertionToJob(assertion_id)
        )

    # ── Write Functions ──

    def settle_job(self, job_id: int) -> Dict[str, Any]:
        """
        Settle an assertion after liveness period.
        
        Anyone can call this. Will trigger OOv3 callback which completes/rejects the job.
        """
        fn = self.contract.functions.settleJob(job_id)
        return self._send_tx(fn)

    def initiate_assertion(self, job_id: int) -> Dict[str, Any]:
        """
        Manually initiate an assertion for a submitted job.
        
        Note: Normally this is auto-triggered by afterAction hook.
        Only needed if hook wasn't set when job was created.
        """
        fn = self.contract.functions.initiateAssertion(job_id)
        return self._send_tx(fn)

    def deposit_bond(self, amount: int) -> Dict[str, Any]:
        """
        Deposit bond tokens into the contract.
        
        Anyone can call this to fund assertions.
        """
        fn = self.contract.functions.depositBond(amount)
        return self._send_tx(fn)

    def withdraw_bond(self, amount: int) -> Dict[str, Any]:
        """
        Withdraw bond tokens from the contract.
        
        Only owner can call this.
        """
        fn = self.contract.functions.withdrawBond(amount)
        return self._send_tx(fn)

    def set_bond_token(self, new_bond_token: str) -> Dict[str, Any]:
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
