"""Core config primitives shared across SDK modules.

Two pieces:

- :func:`get_env` — tiny env-var reader. All modules should use this instead
  of calling ``os.getenv`` directly so the env surface stays auditable and
  prefix conventions stay consistent.
- :class:`AgentConfig` — dataclass base that captures the configuration
  concepts common to **every** agent the SDK ships (network + wallet).
  Module-specific configs (``APEXConfig``, ``BNBAgentConfig``, ...) inherit
  from this and add only their own fields.

Env var convention
------------------
- **Global** (no prefix): keys that describe the SDK process as a whole
  (``NETWORK``, ``RPC_URL``, ``PRIVATE_KEY``, ``WALLET_PASSWORD``,
  ``WALLET_ADDRESS``, ``DEBUG``).
- **Module-scoped**: ``<MODULE>_<KEY>`` (e.g. ``APEX_COMMERCE_ADDRESS``,
  ``ERC8004_REGISTRY_ADDRESS``, ``STORAGE_PROVIDER``).

See the project-root ``.env.example`` for the full surface.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import NetworkConfig
    from ..wallets.wallet_provider import WalletProvider

logger = logging.getLogger(__name__)


def get_env(key: str, default: str | None = None, prefix: str = "") -> str | None:
    """Read ``<prefix><key>`` from the environment.

    Returns ``default`` when the variable is unset or empty. Empty strings
    are normalised to ``None`` (or ``default``) so callers don't have to
    distinguish ``VAR=`` from ``VAR unset``.
    """
    full_key = f"{prefix}{key}"
    value = os.getenv(full_key)
    if value is None or value == "":
        return default
    return value


@dataclass
class AgentConfig:
    """Base for SDK module configs that need a wallet + network.

    Primary API
    -----------
    wallet_provider
        ``WalletProvider`` instance that owns all signing. Preferred when
        the caller already has a provider; raw keys never need to be
        handled directly.

    Convenience API
    ---------------
    private_key + wallet_password
        Auto-wrapped into ``EVMWalletProvider`` in ``__post_init__``.
        ``private_key`` is cleared after wrapping so it never survives
        in memory on the config object.

    Network
    -------
    network
        Accepts either a preset name (``"bsc-testnet"``) or a concrete
        ``NetworkConfig`` instance. Subclasses can overlay module-specific
        overrides by customising :meth:`effective_network`.
    """

    network: str | NetworkConfig = "bsc-testnet"
    wallet_provider: WalletProvider | None = field(default=None, repr=False)

    # Convenience: auto-wrapped into EVMWalletProvider
    private_key: str = field(default="", repr=False)
    wallet_password: str = field(default="", repr=False)
    wallet_address: str = ""  # select specific wallet from ~/.bnbagent/wallets/

    def __post_init__(self):
        if self.private_key and not self.private_key.startswith("0x"):
            self.private_key = f"0x{self.private_key}"

        if self.private_key and not self.wallet_provider:
            if not self.wallet_password:
                raise ValueError(
                    "wallet_password is required when using private_key. "
                    "Pass wallet_provider= directly or set wallet_password."
                )
            from ..wallets import EVMWalletProvider

            self.wallet_provider = EVMWalletProvider(
                password=self.wallet_password,
                private_key=self.private_key,
            )
            self.private_key = ""

        # Password-only path: load an existing keystore if one is on disk,
        # otherwise let EVMWalletProvider auto-generate a fresh wallet.
        elif not self.private_key and not self.wallet_provider and self.wallet_password:
            from ..wallets import EVMWalletProvider

            self.wallet_provider = EVMWalletProvider(
                password=self.wallet_password,
                address=self.wallet_address or None,
            )

    def _wallet_info_repr(self) -> str:
        if not self.wallet_provider:
            return "wallet=None"
        try:
            return f"wallet='{self.wallet_provider.address[:10]}...'"
        except Exception:
            return "wallet='<configured>'"

    @property
    def effective_network(self) -> NetworkConfig:
        """Resolve ``network`` to a concrete ``NetworkConfig``.

        Subclasses should override this to overlay their own env-derived
        contract-address overrides on top of the base resolution.
        """
        from ..config import resolve_network

        return resolve_network(self.network)

    @classmethod
    def _wallet_kwargs_from_env(cls) -> dict[str, str]:
        """Read the wallet-related env vars (global, no prefix).

        Returns a kwargs dict suitable for ``cls(**kwargs, ...)``. Called
        by each subclass's ``from_env`` so the wallet logic is uniform.
        """
        private_key = get_env("PRIVATE_KEY") or ""
        wallet_password = get_env("WALLET_PASSWORD") or ""
        wallet_address = get_env("WALLET_ADDRESS") or ""
        return {
            "private_key": private_key,
            "wallet_password": wallet_password,
            "wallet_address": wallet_address,
        }

    @classmethod
    def _with_network_overlay(
        cls, base: NetworkConfig, **overrides: str | int | None
    ) -> NetworkConfig:
        """Helper for subclasses: overlay only non-empty override values."""
        filtered = {k: v for k, v in overrides.items() if v}
        return replace(base, **filtered) if filtered else base
