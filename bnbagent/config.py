"""Top-level SDK configuration.

Exports:

- :class:`NetworkConfig` — per-network defaults (RPC, paymaster, contract
  addresses for every module that uses on-chain state).
- :func:`resolve_network` — looks up a preset by name, with an optional
  ``RPC_URL`` env override. **Module-specific contract overrides live in
  their own module configs** (e.g. ``ERC8183Config``, ``get_erc8004_config``),
  not here.
- :class:`BNBAgentConfig` — top-level SDK facade; composes modules via
  :class:`ModuleRegistry`. Inherits wallet + network plumbing from
  :class:`AgentConfig`.

Env var surface
---------------
``resolve_network`` is intentionally narrow: it only reads ``RPC_URL``.
Module-scoped env vars (``ERC8183_COMMERCE_ADDRESS``, ``ERC8004_REGISTRY_ADDRESS``,
``STORAGE_*``, ...) are owned by the corresponding module config. The
project-root ``.env.example`` is the authoritative reference.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .core.config import AgentConfig, get_env

logger = logging.getLogger(__name__)


@dataclass
class NetworkConfig:
    """Per-network configuration with ALL protocol addresses.

    ERC-8183 is a three-contract stack: AgenticCommerce kernel (escrow),
    EvaluatorRouter (routing + hook), and OptimisticPolicy (silence-approves,
    vote-rejects). Payment token is NOT configured here — it is immutable
    on the Commerce kernel and read at runtime via ``ERC8183Client.payment_token``.
    """

    name: str
    chain_id: int
    rpc_url: str
    paymaster_url: str | None = None
    use_paymaster: bool = False
    # ERC-8004 Identity Registry
    registry_contract: str = ""
    # ERC-8183 stack
    commerce_contract: str = ""
    router_contract: str = ""
    policy_contract: str = ""


NETWORKS: dict[str, NetworkConfig] = {
    "bsc-testnet": NetworkConfig(
        name="bsc-testnet",
        chain_id=97,
        rpc_url="https://data-seed-prebsc-2-s2.binance.org:8545",
        paymaster_url="https://bsc-megafuel-testnet.nodereal.io",
        use_paymaster=True,
        registry_contract="0x8004A818BFB912233c491871b3d84c89A494BD9e",
        commerce_contract="0xa206c0517b6371c6638cd9e4a42cc9f02a33b0de",
        router_contract="0xd7d36d66d2f1b608a0f943f722d27e3744f66f25",
        policy_contract="0x4f4678d4439fec812ac7674bb3efb4c8f5fb78a6",
    ),
    "bsc-mainnet": NetworkConfig(
        name="bsc-mainnet",
        chain_id=56,
        rpc_url="https://bsc-dataseed.binance.org",
        paymaster_url="https://bsc-megafuel.nodereal.io/",
        use_paymaster=True,
        registry_contract="0x8004A169FB4a3325136EB29fA0ceB6D2e539a432",
        commerce_contract="0xea4daa3100a767e86fded867729ae7446476eba6",
        router_contract="0x51895229e12f9876011789b04f8698af06ccd6da",
        policy_contract="0x9c01845705b3078aa2e8cff7520a6376fd766de5",
    ),
}


def resolve_network(network: str | NetworkConfig = "bsc-testnet") -> NetworkConfig:
    """Resolve a network preset to a concrete ``NetworkConfig``.

    Accepts either a preset name (``"bsc-testnet"`` / ``"bsc-mainnet"``) or a
    concrete ``NetworkConfig`` instance:

    - **String** → look up the preset; apply ``RPC_URL`` env override if set.
      Module-scoped contract-address envs (``ERC8183_*``, ``ERC8004_*``) are
      NOT read here — they belong to each module's own config loader.
    - **NetworkConfig** → returned as-is; env vars are never applied (fully
      explicit control is the point of passing an object).
    """
    if isinstance(network, NetworkConfig):
        return network

    nc = NETWORKS.get(network)
    if nc is None:
        raise ValueError(f"Unknown network: {network}")

    rpc_override = get_env("RPC_URL")
    if rpc_override:
        use_paymaster = not rpc_override.startswith("http://localhost")
        return NetworkConfig(
            name=nc.name,
            chain_id=nc.chain_id,
            rpc_url=rpc_override,
            paymaster_url=nc.paymaster_url,
            use_paymaster=use_paymaster,
            registry_contract=nc.registry_contract,
            commerce_contract=nc.commerce_contract,
            router_contract=nc.router_contract,
            policy_contract=nc.policy_contract,
        )
    return nc


@dataclass
class BNBAgentConfig(AgentConfig):
    """Top-level SDK config — aggregates wallet + network + module settings.

    Usage:
        from bnbagent.wallets import EVMWalletProvider
        wallet = EVMWalletProvider(password="...", private_key="0x...")
        config = BNBAgentConfig(wallet_provider=wallet)

        # Convenience (auto-wraps into EVMWalletProvider):
        config = BNBAgentConfig(private_key="0x...", wallet_password="...")

        # From environment:
        config = BNBAgentConfig.from_env()
    """

    settings: dict[str, Any] = field(default_factory=dict)
    modules: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __repr__(self) -> str:
        net_name = (
            self.network if isinstance(self.network, str) else self.network.name
        )
        return (
            f"BNBAgentConfig("
            f"network='{net_name}', "
            f"{self._wallet_info_repr()}, "
            f"settings={list(self.settings.keys())}, "
            f"modules={list(self.modules.keys())})"
        )

    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value. Supports dotted keys: ``'erc8183.service_price'``."""
        if "." in key:
            module_name, sub_key = key.split(".", 1)
            return self.modules.get(module_name, {}).get(sub_key, default)
        return self.settings.get(key, default)

    def to_flat_dict(self) -> dict[str, Any]:
        """Flatten to a single dict for ``module.initialize(config)``."""
        flat = dict(self.settings)
        flat["network"] = self.network
        flat["wallet_provider"] = self.wallet_provider
        for mod_name, mod_settings in self.modules.items():
            for k, v in mod_settings.items():
                flat[f"{mod_name}.{k}"] = v
        return flat

    @property
    def network_config(self) -> NetworkConfig:
        """Resolve the current network to a concrete ``NetworkConfig``."""
        return resolve_network(self.network) if isinstance(self.network, str) else self.network

    @classmethod
    def from_env(cls) -> BNBAgentConfig:
        """Create config from environment variables.

        Reads the **global** env surface only (network + wallet + debug).
        Module-specific settings are loaded inside each module's own
        ``*Config.from_env`` — keep those concerns separate.
        """
        wallet_kwargs = cls._wallet_kwargs_from_env()
        return cls(
            network=get_env("NETWORK", "bsc-testnet"),
            settings={"debug": (get_env("DEBUG", "false") or "false").lower() == "true"},
            **wallet_kwargs,
        )
