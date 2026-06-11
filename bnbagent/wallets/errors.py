"""Wallet-layer exceptions.

``UnsupportedWalletOperation`` is the descriptive error raised when a wallet
backend cannot service an operation (e.g. a fixed-command-menu wallet asked
for arbitrary signing). It subclasses :class:`NotImplementedError` so existing
``except NotImplementedError`` callers keep working.

``WalletIdentityMismatch`` is raised when a provider pinned to an
``expected_address`` discovers the backend wallet resolves to a different
address (INV-4: never proceed with a drifted on-chain identity).
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


class WalletIdentityMismatch(RuntimeError):
    """The wallet resolved to a different address than the caller pinned.

    Raised on the first address lookup when a provider was constructed with
    ``expected_address`` and the backend reports another address. Addresses
    are public, so both are printed in full. The usual cause in deployment is
    a stale (or wrong-environment) secret-bundle version materializing old
    key material — fix the bundle, never the pin (INV-4).
    """

    def __init__(self, *, expected: str, actual: str) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"wallet identity mismatch: expected {expected} but the wallet "
            f"reports {actual}. The key material backing this wallet is not "
            "the pinned identity — most often a stale or wrong "
            "secret-bundle version (e.g. the TWAK_WALLET_JSON entry) was "
            "materialized. Refusing to operate under a drifted on-chain "
            "identity."
        )
