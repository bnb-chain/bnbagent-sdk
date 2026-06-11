"""Wallet-layer exceptions.

``UnsupportedWalletOperation`` is the descriptive error raised when a wallet
backend cannot service an operation (e.g. a fixed-command-menu wallet asked
for arbitrary signing). It subclasses :class:`NotImplementedError` so existing
``except NotImplementedError`` callers keep working.
"""

from __future__ import annotations


class UnsupportedWalletOperation(NotImplementedError):
    """A wallet backend cannot perform the requested capability/operation.

    The message is assembled from the capability (or operation) name, the
    reason it is unsupported, an optional alternative path, and an optional
    upstream-tracking reference (a ``REQ-n`` / ``S-n`` ID from
    ``docs/twak-cli-gaps-v0.18.0.md``). Raising with just the first positional
    argument uses it verbatim as the message.
    """

    def __init__(
        self,
        capability_or_operation: str,
        *,
        reason: str | None = None,
        alternative: str | None = None,
        ref: str | None = None,
    ) -> None:
        message = capability_or_operation
        if reason:
            message = f"{capability_or_operation}: {reason}."
        if alternative:
            message += f" Alternative: {alternative}."
        if ref:
            message += f" (tracked as {ref} in docs/twak-cli-gaps-v0.18.0.md)"
        super().__init__(message)
