"""Wallet provider factory — a single creation entry point.

Each provider has its own creation lifecycle (the EVM provider loads or
auto-generates an encrypted keystore in its constructor; the twak provider
delegates custody to the ``twak`` CLI), so there is no shared key store. This
factory does not try to unify *storage* — it unifies *selection*: given a
``kind`` string (typically from configuration) plus provider-specific keyword
arguments, it constructs the matching :class:`WalletProvider`.

Example::

    wallet = create_wallet_provider("evm", password="pw")
    wallet = create_wallet_provider("twak", chain="bsc")
"""

from __future__ import annotations

from typing import Any

from .wallet_provider import WalletProvider

#: Wallet kinds the factory can construct. Mirrors each provider's ``kind``.
SUPPORTED_WALLET_KINDS: tuple[str, ...] = ("evm", "twak", "mpc")


def create_wallet_provider(kind: str, **kwargs: Any) -> WalletProvider:
    """Construct a :class:`WalletProvider` for ``kind``.

    Args:
        kind: Provider identifier (case-insensitive): ``"evm"``, ``"twak"``
            or ``"mpc"``.
        **kwargs: Forwarded verbatim to the provider constructor. Required
            arguments differ per kind — e.g. ``EVMWalletProvider`` needs
            ``password=...``; ``TWAKProvider`` accepts ``chain=...``.

    Returns:
        The constructed provider.

    Raises:
        ValueError: If ``kind`` is not one of :data:`SUPPORTED_WALLET_KINDS`.
        NotImplementedError: If ``kind`` is ``"mpc"`` (stub by design — supply
            your own subclass instead).
    """
    normalized = (kind or "").strip().lower()

    if normalized == "evm":
        from .evm_wallet_provider import EVMWalletProvider

        return EVMWalletProvider(**kwargs)
    if normalized == "twak":
        from .twak_provider import TWAKProvider

        return TWAKProvider(**kwargs)
    if normalized == "mpc":
        from .mpc_wallet_provider import MPCWalletProvider

        return MPCWalletProvider(**kwargs)

    raise ValueError(
        f"Unknown wallet kind {kind!r}. "
        f"Supported kinds: {', '.join(SUPPORTED_WALLET_KINDS)}."
    )
