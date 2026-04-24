"""Shared types and constants for the APEX SDK.

Mirrors the enums and reason codes defined by the APEX v1 contract stack:

- ``JobStatus`` — order-dependent with ``IACP.JobStatus`` in the Solidity kernel.
- ``Verdict`` — order-dependent with ``VERDICT_*`` constants in
  ``EvaluatorRouterUpgradeable`` and ``OptimisticPolicy``.
- ``REASON_APPROVED`` / ``REASON_REJECTED`` — ``keccak256`` reason codes emitted
  by ``OptimisticPolicy``; also re-exported as hex strings for logging.

Any change to the on-chain enum / constant layout MUST be reflected here,
otherwise ``APEXClient.get_job(...).status`` and verdict comparisons will
silently drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from web3 import Web3


class JobStatus(IntEnum):
    """ERC-8183 job lifecycle, matches ``IACP.JobStatus``."""

    OPEN = 0
    FUNDED = 1
    SUBMITTED = 2
    COMPLETED = 3
    REJECTED = 4
    EXPIRED = 5


class Verdict(IntEnum):
    """Policy verdict, matches ``VERDICT_*`` in Router + Policy."""

    PENDING = 0
    APPROVE = 1
    REJECT = 2


# ---------------------------------------------------------------------------
# Reason codes (bytes32 keccak256 of ASCII label)
# ---------------------------------------------------------------------------

REASON_APPROVED: bytes = Web3.keccak(text="OPTIMISTIC_APPROVED")
REASON_REJECTED: bytes = Web3.keccak(text="OPTIMISTIC_REJECTED")

ZERO_REASON: bytes = b"\x00" * 32
ZERO_ADDRESS: str = "0x" + "00" * 20


@dataclass(frozen=True)
class Job:
    """Typed view of ``IACP.Job`` returned by ``commerce.getJob``."""

    id: int
    client: str
    provider: str
    evaluator: str
    description: str
    budget: int
    expired_at: int
    status: JobStatus
    hook: str
