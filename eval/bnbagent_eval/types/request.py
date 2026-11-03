"""Evaluation request and response contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from ._serde import Serializable
from .case import EvalCase
from .options import RunOptions
from .result import Aggregate, CaseResult, Cost, Verdict
from .subject import Subject
from .suite import Suite
from .target import TargetRef

SCHEMA_VERSION = "bnbagent-eval/v1"
Stage = Literal["dev", "registration", "pre_submit", "review", "vote"]


@dataclass(frozen=True)
class EvalRequest(Serializable):
    stage: Stage = "dev"
    suite: Suite | None = None
    suite_ref: str | None = None  # "<uri>#<suite_hash>"
    cases: tuple[EvalCase, ...] = ()
    dataset_ref: str | None = None
    benchmark_ref: str | None = None
    target: TargetRef | None = None
    subject: Subject | None = None
    options: RunOptions = field(default_factory=RunOptions)
    schema_version: str = SCHEMA_VERSION
    extensions: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]):
        """Readers reject newer major versions and migrate older ones."""
        from .migrate import upgrade

        return super().from_dict(upgrade(data) if isinstance(data, Mapping) else data)


@dataclass(frozen=True)
class Provenance(Serializable):
    package_version: str
    suite_hash: str
    request_hash: str
    started_at: float
    finished_at: float
    metric_versions: Mapping[str, str] = field(default_factory=dict)
    judge: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class Attestation(Serializable):
    scheme: Literal["eip712", "eip191"]
    evaluator: str
    digest: str
    signature: str
    issued_at: int
    expires_at: int | None = None
    domain: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvalResponse(Serializable):
    stage: Stage
    suite_hash: str
    results: tuple[CaseResult, ...]
    aggregate: Aggregate
    verdict: Verdict
    provenance: Provenance
    result_hash: str
    response_hash: str
    subject: Subject | None = None
    cost: Cost = field(default_factory=Cost)
    attestation: Attestation | None = None
    schema_version: str = SCHEMA_VERSION
    extensions: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]):
        """Readers reject newer major versions and migrate older ones."""
        from .migrate import upgrade

        return super().from_dict(upgrade(data) if isinstance(data, Mapping) else data)
