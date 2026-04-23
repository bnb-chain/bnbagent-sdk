"""Thin wrapper around ``OptimisticPolicy`` (APEX v1 reference policy).

Surface:

- ``dispute(jobId)``      — client-only, within dispute window.
- ``vote_reject(jobId)``  — whitelisted voter, post-dispute.
- Read helpers for window state, quorum, voter status, etc.

Note: the contract's "silence approves" design means voters can ONLY reject.
There is no ``voteApprove`` on-chain; jobs without dispute auto-approve when
``submittedAt + disputeWindow`` elapses.
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
    abi_path = Path(__file__).parent / "abis" / "OptimisticPolicy.json"
    return json.loads(abi_path.read_text())


class PolicyClient(ContractClientMixin):
    """Low-level client for ``OptimisticPolicy``."""

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

    def dispute(self, job_id: int) -> dict[str, Any]:
        """Client raises a dispute. MUST be within dispute window."""
        fn = self.contract.functions.dispute(job_id)
        return self._send_tx(fn)

    def vote_reject(self, job_id: int) -> dict[str, Any]:
        """Whitelisted voter casts a reject vote (one per voter per job)."""
        fn = self.contract.functions.voteReject(job_id)
        return self._send_tx(fn)

    # ------------------------------------------------------------------ views

    def check(self, job_id: int, evidence: bytes = b"") -> tuple[Verdict, bytes]:
        """Simulate the verdict the Router would see right now."""
        verdict_int, reason = self._call_with_retry(
            self.contract.functions.check(job_id, evidence)
        )
        return Verdict(verdict_int), reason

    def submitted_at(self, job_id: int) -> int:
        return self._call_with_retry(self.contract.functions.submittedAt(job_id))

    def disputed(self, job_id: int) -> bool:
        return self._call_with_retry(self.contract.functions.disputed(job_id))

    def reject_votes(self, job_id: int) -> int:
        return self._call_with_retry(self.contract.functions.rejectVotes(job_id))

    def has_voted(self, job_id: int, voter: str) -> bool:
        return self._call_with_retry(
            self.contract.functions.hasVoted(job_id, Web3.to_checksum_address(voter))
        )

    def is_voter(self, voter: str) -> bool:
        return self._call_with_retry(
            self.contract.functions.isVoter(Web3.to_checksum_address(voter))
        )

    def dispute_window(self) -> int:
        return self._call_with_retry(self.contract.functions.disputeWindow())

    def vote_quorum(self) -> int:
        return self._call_with_retry(self.contract.functions.voteQuorum())

    def active_voter_count(self) -> int:
        return self._call_with_retry(self.contract.functions.activeVoterCount())

    def admin(self) -> str:
        return self._call_with_retry(self.contract.functions.admin())

    def commerce(self) -> str:
        return self._call_with_retry(self.contract.functions.commerce())

    def router(self) -> str:
        return self._call_with_retry(self.contract.functions.router())

    # --------------------------------------------------- admin writes (owner)

    def add_voter(self, voter: str) -> dict[str, Any]:
        fn = self.contract.functions.addVoter(Web3.to_checksum_address(voter))
        return self._send_tx(fn)

    def remove_voter(self, voter: str) -> dict[str, Any]:
        fn = self.contract.functions.removeVoter(Web3.to_checksum_address(voter))
        return self._send_tx(fn)

    def set_quorum(self, new_quorum: int) -> dict[str, Any]:
        fn = self.contract.functions.setQuorum(new_quorum)
        return self._send_tx(fn)
