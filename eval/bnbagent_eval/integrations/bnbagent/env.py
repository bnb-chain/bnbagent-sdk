"""Build ports from the SDK's standard environment variables (for the CLI and scripts).

Wallet: ``WALLET_KIND`` / ``PRIVATE_KEY`` / ``WALLET_PASSWORD`` / ``WALLET_ADDRESS``, exactly as
the SDK's ``AgentConfig.from_env`` reads them (the raw key is wrapped and cleared by the SDK).
Storage: ``STORAGE_PROVIDER=ipfs`` → ``IPFSStorageProvider.from_env()``, ``local`` →
``LocalStorageProvider.from_env()``. Network: ``NETWORK`` (default ``bsc-testnet``).
Secrets stay in the environment (e.g. a git-ignored ``.env``); nothing is written to disk here.
"""

from __future__ import annotations

import os
from typing import Any

from ...exceptions import ChainIntegrationError
from ...ports import ChainPorts

DEFAULT_NETWORK = "bsc-testnet"


def wallet_from_env(network: str = DEFAULT_NETWORK) -> Any | None:
    """The SDK ``WalletProvider`` configured by env vars, or ``None`` when none is configured."""
    from bnbagent.core.config import AgentConfig

    from .compat import wallet_env_kwargs

    kwargs = wallet_env_kwargs(AgentConfig)
    if not any(kwargs.values()):
        return None
    return AgentConfig(network=network, **kwargs).wallet_provider


def storage_from_env() -> Any | None:
    kind = (os.environ.get("STORAGE_PROVIDER") or "").strip().lower()
    if not kind:
        return None
    if kind == "ipfs":
        from bnbagent.storage.ipfs_storage_provider import IPFSStorageProvider

        return IPFSStorageProvider.from_env()
    if kind == "local":
        from bnbagent.storage.local_storage_provider import LocalStorageProvider

        return LocalStorageProvider.from_env()
    raise ChainIntegrationError(
        f"unsupported STORAGE_PROVIDER {kind!r} (use 'ipfs' or 'local')", code="invalid_config"
    )


def network_from_env(network: str | None = None) -> str:
    """``network``, else ``NETWORK``, else ``bsc-testnet``."""
    return network or (os.environ.get("NETWORK") or "").strip() or DEFAULT_NETWORK


def ports_from_env(network: str | None = None, **kw: Any) -> ChainPorts:
    from ._factory import ports

    net = network_from_env(network)
    return ports(net, wallet=wallet_from_env(net), storage=storage_from_env(), **kw)
