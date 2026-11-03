"""What is being evaluated; bound into hashes, evidence and attestations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ._serde import Serializable


@dataclass(frozen=True)
class JobSubject(Serializable):
    chain_id: int
    commerce: str
    job_id: int
    provider: str
    output_hash: str  # keccak(manifest.response.content)
    type: Literal["job"] = "job"


@dataclass(frozen=True)
class AgentSubject(Serializable):
    chain_id: int
    registry: str
    agent_id: int
    agent_version: str | None = None  # target fingerprint
    type: Literal["agent"] = "agent"


Subject = JobSubject | AgentSubject
