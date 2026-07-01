"""Core config primitives shared across SDK modules.

Two pieces:

- :func:`get_env` — tiny env-var reader. All modules should use this instead
  of calling ``os.getenv`` directly so the env surface stays auditable and
  prefix conventions stay consistent.
- :class:`AgentConfig` — dataclass base that captures the configuration
  concepts common to **every** agent the SDK ships (network + wallet).
  Module-specific configs (``ERC8183Config``, ...) inherit from this and
  add only their own fields.

Env var convention
------------------
- **Global** (no prefix): keys that describe the SDK process as a whole
  (``NETWORK``, ``RPC_URL``, ``PRIVATE_KEY``, ``WALLET_PASSWORD``,
  ``WALLET_ADDRESS``, ``WALLET_KIND``, ``TWAK_BIN``, ``DEBUG``).
  ``WALLET_KIND=twak`` switches every ``*Config.from_env()`` entry point to
  the twak wallet (chain pinned to the config's network); ``TWAK_BIN``
  optionally points at a version-pinned twak binary.
- **Module-scoped**: ``<MODULE>_<KEY>`` (e.g. ``ERC8183_COMMERCE_ADDRESS``,
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
        Both ``private_key`` and ``wallet_password`` are cleared after
        wrapping so neither survives in memory on the config object (the
        provider keeps its own copy of the password).

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
    # Select a non-EVM provider via the wallet factory (e.g. "twak", "mpc").
    # "evm" / "" keep the private_key + wallet_password convenience path below.
    wallet_kind: str = ""

    def __post_init__(self):
        if self.private_key and not self.private_key.startswith("0x"):
            self.private_key = f"0x{self.private_key}"

        # Explicit non-EVM wallet kind: build it through the factory and skip
        # the private_key / password EVM convenience path entirely.
        if (
            self.wallet_kind
            and self.wallet_kind.strip().lower() != "evm"
            and not self.wallet_provider
        ):
            from ..wallets import create_wallet_provider

            kwargs: dict[str, str] = {}
            if self.wallet_kind.strip().lower() == "twak":
                # Pin twak to the config's network (chain identity must not
                # drift from the chain the clients will talk to), and honor
                # the TWAK_BIN override (e.g. a version-pinned shim).
                from ..wallets.twak_provider import TWAK_CHAIN_FOR_NETWORK

                net_name = (
                    self.network
                    if isinstance(self.network, str)
                    else getattr(self.network, "name", "")
                )
                chain = TWAK_CHAIN_FOR_NETWORK.get(net_name)
                if chain:
                    kwargs["chain"] = chain
                twak_bin = get_env("TWAK_BIN")
                if twak_bin:
                    kwargs["twak_bin"] = twak_bin
            self.wallet_provider = create_wallet_provider(self.wallet_kind, **kwargs)
            return

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
            self.wallet_password = ""

            if os.getenv("PRIVATE_KEY"):
                logger.warning(
                    "PRIVATE_KEY is still set in the environment after the "
                    "keystore import succeeded. The encrypted keystore at "
                    "~/.bnbagent/wallets/<address>.json is now the source of "
                    "truth — remove PRIVATE_KEY from your .env / shell to "
                    "avoid leaking the plaintext key via commits, backups, "
                    "or container images."
                )

        # Password-only path: load an existing keystore if one is on disk,
        # otherwise let EVMWalletProvider auto-generate a fresh wallet.
        elif not self.private_key and not self.wallet_provider and self.wallet_password:
            from ..wallets import EVMWalletProvider

            self.wallet_provider = EVMWalletProvider(
                password=self.wallet_password,
                address=self.wallet_address or None,
            )
            self.wallet_password = ""

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
        wallet_kind = get_env("WALLET_KIND") or ""
        return {
            "private_key": private_key,
            "wallet_password": wallet_password,
            "wallet_address": wallet_address,
            "wallet_kind": wallet_kind,
        }

    @classmethod
    def _with_network_overlay(
        cls, base: NetworkConfig, **overrides: str | int | None
    ) -> NetworkConfig:
        """Helper for subclasses: overlay only non-empty override values."""
        filtered = {k: v for k, v in overrides.items() if v}
        return replace(base, **filtered) if filtered else base
