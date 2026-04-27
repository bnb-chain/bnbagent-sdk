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
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

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

    def get_deliverable_url(self, job_id: int, *, hint_block: int | None = None) -> str | None:
        """Return the ``deliverable_url`` for a submitted job.

        Reads the ``JobInitialised`` event emitted by ``onSubmitted`` and
        parses ``optParams`` (JSON bytes) to extract ``deliverable_url``.
        Returns ``None`` if the event is not found or the field is absent.

        Args:
            hint_block: if the caller knows roughly when the job was submitted
                (e.g. the block number of the ``Disputed`` event), passing it
                allows the query to use a tight window and avoid RPC block-range
                limits.  When omitted a 5 000-block lookback is used.
        """
        _LOOKBACK = 5_000

        try:
            current_block = self.w3.eth.block_number
        except Exception:
            current_block = None

        if hint_block is not None:
            from_block = max(0, hint_block - _LOOKBACK)
            to_block = hint_block + 10
        elif current_block is not None:
            from_block = max(0, current_block - _LOOKBACK)
            to_block = "latest"
        else:
            from_block = 0
            to_block = "latest"

        try:
            logs = self.contract.events.JobInitialised().get_logs(
                from_block=from_block,
                to_block=to_block,
                argument_filters={"jobId": job_id},
            )
        except Exception as exc:
            logger.warning("[PolicyClient] get_deliverable_url(%s) event query failed: %s", job_id, exc)
            return None

        if not logs:
            return None

        raw: bytes = logs[0]["args"].get("optParams", b"")
        if not raw:
            return None
        try:
            params = json.loads(raw.decode("utf-8"))
            return params.get("deliverable_url") or None
        except Exception as exc:
            logger.warning("[PolicyClient] get_deliverable_url(%s) parse failed: %s", job_id, exc)
            return None

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
