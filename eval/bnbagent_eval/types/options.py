"""Run options: runs, deadline, budget, privacy, language, judge override."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ._serde import Serializable
from .judge import JudgeConfig


@dataclass(frozen=True)
class Budget(Serializable):
    max_judge_calls: int | None = None
    max_tokens: int | None = None
    max_usd: float | None = None


@dataclass(frozen=True)
class Privacy(Serializable):
    report_contents: Literal["hashes", "redacted", "full"] = "hashes"
    redact: tuple[str, ...] = ()  # regex patterns applied to reasons/details
    # Reserved (hashed, not applied): encrypt via MetricsPublisher / EvalGate encrypt_to=.
    encrypt_to: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunOptions(Serializable):
    runs: int = 1  # target invocations per case
    deadline: float | None = None  # unix seconds
    budget: Budget | None = None
    privacy: Privacy = field(default_factory=Privacy)
    language: str | None = None
    judge_override: JudgeConfig | None = None
    max_concurrency: int | None = None

    def __post_init__(self) -> None:
        if self.runs < 1:
            raise ValueError("runs must be >= 1")
        if self.max_concurrency is not None and self.max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
