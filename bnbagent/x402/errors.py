"""Errors raised by X402Signer."""

from __future__ import annotations


class X402SignerError(Exception):
    """Base class for X402Signer-layer refusals."""


class X402RecipientMismatchError(X402SignerError):
    """``message['to']`` did not byte-equal the caller-supplied ``expected_to``.

    Forces the caller to commit to a destination address before invoking
    the signer; defends against an upstream LLM tool quietly altering the
    payee in a 402 challenge.
    """


class X402AmountExceededError(X402SignerError):
    """``message['value']`` exceeded the per-call ``max_value`` for this token."""


class X402BudgetExhaustedError(X402SignerError):
    """The session budget for this token would be exceeded by this call."""


class X402PolicyError(X402SignerError):
    """A SigningPolicy violation surfaced from the underlying wallet."""
