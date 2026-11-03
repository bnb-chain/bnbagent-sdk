"""Error hierarchy for bnbagent-eval.

Standalone on purpose (like the SDK's x402 errors): the core must not import ``bnbagent``.
Metric-level failures are *data* (``MetricResult.error``), never exceptions; these classes are
raised only for invalid requests, suites, configuration and integration failures.
"""

from __future__ import annotations

__all__ = [
    "EvalError",
    "SchemaError",
    "SuiteError",
    "RequestError",
    "MissingExtraError",
    "PolicyError",
    "JudgeError",
    "JudgeThrottled",
    "JudgeParseError",
    "JudgeNonRetryable",
    "TargetError",
    "AdapterError",
    "BudgetExceeded",
    "PaymentRequired",
    "PaymentError",
    "AttestationError",
    "ChainIntegrationError",
]


class EvalError(Exception):
    """Base class for every bnbagent-eval error."""


class SchemaError(EvalError):
    """A serialized contract is malformed, has unknown fields, or an unsupported schema version."""


class SuiteError(EvalError):
    """A suite is invalid (unknown metric, bad params, lock mismatch, judge conflict, ...)."""


class RequestError(EvalError):
    """An evaluation request is invalid."""


class MissingExtraError(EvalError, ImportError):
    """An optional dependency is not installed."""

    def __init__(self, module: str, extra: str):
        self.module = module
        self.extra = extra
        super().__init__(
            f"'{module}' is required for this feature. Install it with: "
            f"pip install 'bnbagent-eval[{extra}]'"
        )


class PolicyError(EvalError):
    """A request or suite was denied by the untrusted-suite policy."""


class JudgeError(EvalError):
    """Base class for judge-model failures."""


class JudgeThrottled(JudgeError):
    """The judge provider throttled the request (retryable)."""


class JudgeParseError(JudgeError):
    """The judge response could not be parsed into the expected schema."""


class JudgeNonRetryable(JudgeError):
    """The judge request failed in a way that retrying cannot fix (e.g. context overflow)."""


class TargetError(EvalError):
    """The agent under test could not be invoked."""


class AdapterError(EvalError):
    """A trace could not be mapped into the normalized trajectory model."""


class BudgetExceeded(EvalError):
    """A request's estimated cost exceeds its budget."""


class PaymentRequired(EvalError):
    """The eval service requires payment (HTTP 402)."""

    def __init__(self, quote: object | None = None, message: str = "payment required"):
        self.quote = quote
        super().__init__(message)


class PaymentError(EvalError):
    """A payment could not be verified or settled."""


class AttestationError(EvalError):
    """An attestation or response binding failed verification."""


class ChainIntegrationError(EvalError):
    """A port adapter failed to talk to the chain or the SDK."""

    def __init__(self, message: str, *, code: str = "chain_error", retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(message)
