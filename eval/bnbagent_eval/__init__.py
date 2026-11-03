"""bnbagent-eval: agent evaluation for BNB Chain agents.

Importing this package pulls in no optional extras, no bnbagent/web3, and makes no network call.
"""

from __future__ import annotations

from ._version import __version__
from .client import EvalClient, evaluate
from .metrics.custom.python import register_python_metric
from .metrics.registry import register as register_metric
from .types import (
    AgentSubject,
    Artifact,
    Budget,
    EvalCase,
    EvalRequest,
    EvalResponse,
    JobSubject,
    JudgeConfig,
    Message,
    MetricConfig,
    MetricResult,
    PassRule,
    Privacy,
    RunOptions,
    Suite,
    TargetRef,
    Verdict,
)

__all__ = [
    "AgentSubject",
    "Artifact",
    "Budget",
    "EvalCase",
    "EvalClient",
    "EvalRequest",
    "EvalResponse",
    "JobSubject",
    "JudgeConfig",
    "Message",
    "MetricConfig",
    "MetricResult",
    "PassRule",
    "Privacy",
    "RunOptions",
    "Suite",
    "TargetRef",
    "Verdict",
    "__version__",
    "evaluate",
    "register_metric",
    "register_python_metric",
]
