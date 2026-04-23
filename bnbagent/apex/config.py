"""APEXConfig — APEX-agent configuration (v1).

Inherits ``wallet_provider`` + ``network`` plumbing from :class:`AgentConfig`
and adds the three APEX-specific concerns:

- ``storage``      — off-chain deliverable store.
- ``service_price`` — minimum budget (in raw token units) this provider
  will accept; used by ``APEXJobOps.verify_job`` (HTTP 402) and by the
  ``NegotiationHandler`` to advertise a floor in ``/negotiate`` responses.

Contract-address overrides are NOT fields on this class. Use either:

- Env vars ``APEX_COMMERCE_ADDRESS`` / ``APEX_ROUTER_ADDRESS`` /
  ``APEX_POLICY_ADDRESS`` (applied in :meth:`effective_network`).
- A pre-built ``NetworkConfig`` passed as ``network=NetworkConfig(...)``
  (fully explicit, env overrides are ignored in this mode).

Env var surface (module-scoped, ``APEX_`` prefix)
-------------------------------------------------
    APEX_COMMERCE_ADDRESS — override commerce_contract
    APEX_ROUTER_ADDRESS   — override router_contract
    APEX_POLICY_ADDRESS   — override policy_contract
    APEX_SERVICE_PRICE    — minimum budget floor (raw token units)

Global env vars consumed via :class:`AgentConfig`:
    NETWORK / RPC_URL / PRIVATE_KEY / WALLET_PASSWORD / WALLET_ADDRESS

Payment token address is NOT configurable — it is immutable on the Commerce
kernel and fetched at runtime via ``APEXClient.payment_token``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..config import NetworkConfig
from ..core.config import AgentConfig, get_env

if TYPE_CHECKING:
    from ..storage.interface import StorageProvider

logger = logging.getLogger(__name__)


APEX_ENV_PREFIX = "APEX_"


@dataclass
class APEXConfig(AgentConfig):
    """Configuration for an APEX agent (typically a provider).

    Primary API (see :class:`AgentConfig` for wallet + network fields):
        storage
            Off-chain storage for deliverables.
        service_price
            Minimum budget floor, raw token units (stringified int).
    """

    storage: StorageProvider | None = field(default=None, repr=False)
    service_price: str = "1000000000000000000"  # 1 token (18 decimals default)

    def __repr__(self) -> str:
        nc = self.effective_network
        return (
            f"APEXConfig("
            f"network='{nc.name}', "
            f"{self._wallet_info_repr()}, "
            f"commerce='{nc.commerce_contract[:10]}...', "
            f"service_price={self.service_price})"
        )

    # ----------------------------------------------------------- effectives

    @property
    def effective_network(self) -> NetworkConfig:
        """Resolve ``network`` and overlay APEX-scoped env overrides.

        Overlay precedence (highest → lowest):
            1. ``APEX_COMMERCE_ADDRESS`` / ``APEX_ROUTER_ADDRESS`` /
               ``APEX_POLICY_ADDRESS`` env vars.
            2. ``RPC_URL`` env var (applied during preset resolution).
            3. Preset defaults from ``NETWORKS``.

        When ``self.network`` is already a ``NetworkConfig`` object, the
        caller takes full control — env overrides are not applied.
        """
        base = super().effective_network
        if isinstance(self.network, NetworkConfig):
            return base
        return self._with_network_overlay(
            base,
            commerce_contract=get_env("COMMERCE_ADDRESS", prefix=APEX_ENV_PREFIX),
            router_contract=get_env("ROUTER_ADDRESS", prefix=APEX_ENV_PREFIX),
            policy_contract=get_env("POLICY_ADDRESS", prefix=APEX_ENV_PREFIX),
        )

    # ------------------------------------- convenience shorthand properties

    @property
    def effective_rpc_url(self) -> str:
        return self.effective_network.rpc_url

    @property
    def effective_chain_id(self) -> int:
        return self.effective_network.chain_id

    @property
    def effective_commerce_address(self) -> str:
        return self.effective_network.commerce_contract

    @property
    def effective_router_address(self) -> str:
        return self.effective_network.router_contract

    @property
    def effective_policy_address(self) -> str:
        return self.effective_network.policy_contract

    # ---------------------------------------------------------------- loaders

    @classmethod
    def from_env(cls) -> APEXConfig:
        """Load APEX configuration from the environment.

        Global env vars (``NETWORK``, wallet keys) are read via
        :class:`AgentConfig`. APEX-specific fields use the ``APEX_`` prefix
        and are resolved lazily by :meth:`effective_network` so the env is
        always the single source of truth.
        """
        wallet_password = get_env("WALLET_PASSWORD") or ""
        if not wallet_password:
            raise ValueError(
                "APEXConfig validation failed: WALLET_PASSWORD is required. "
                "Set WALLET_PASSWORD to encrypt/decrypt the wallet keystore."
            )

        wallet_kwargs = cls._wallet_kwargs_from_env()
        private_key = wallet_kwargs["private_key"]
        wallet_address = wallet_kwargs["wallet_address"]

        if not private_key:
            from ..wallets import EVMWalletProvider

            if EVMWalletProvider.keystore_exists(address=wallet_address or None):
                logger.info(
                    "[APEXConfig] Loading wallet from existing keystore "
                    "(PRIVATE_KEY not set)"
                )
            else:
                logger.info(
                    "[APEXConfig] No PRIVATE_KEY and no keystore found — "
                    "a new wallet will be auto-generated"
                )

        from ..storage.config import StorageConfig
        from ..storage.factory import create_storage_provider

        storage = create_storage_provider(StorageConfig.from_env())

        return cls(
            network=get_env("NETWORK", "bsc-testnet"),
            storage=storage,
            service_price=get_env(
                "SERVICE_PRICE", "1000000000000000000", prefix=APEX_ENV_PREFIX
            ),
            **wallet_kwargs,
        )

    @classmethod
    def from_env_optional(cls) -> APEXConfig | None:
        try:
            return cls.from_env()
        except ValueError as exc:
            logger.info("[APEXConfig] APEX not configured: %s", exc)
            return None
