"""
Unified configuration that aggregates module-specific configs.

Each module contributes its own defaults. User config overrides everything.
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class NetworkConfig:
    """Per-network configuration."""

    name: str
    chain_id: int
    rpc_url: str
    paymaster_url: Optional[str] = None
    use_paymaster: bool = False


NETWORKS: Dict[str, NetworkConfig] = {
    "bsc-testnet": NetworkConfig(
        name="bsc-testnet",
        chain_id=97,
        rpc_url="https://data-seed-prebsc-2-s2.binance.org:8545",
        paymaster_url="https://bsc-megafuel-testnet.nodereal.io",
        use_paymaster=True,
    ),
}


@dataclass
class BNBAgentConfig:
    """
    Unified SDK configuration.

    Aggregates:
    - Network selection
    - Per-module settings (namespaced: "apex.evaluator_address")
    - General settings

    Usage:
        config = BNBAgentConfig.from_env()
        config = BNBAgentConfig(network="bsc-testnet", private_key="0x...")
    """

    network: str = "bsc-testnet"
    private_key: str = ""
    settings: Dict[str, Any] = field(default_factory=dict)
    modules: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value. Supports dotted keys: 'apex.evaluator_address'."""
        if "." in key:
            module_name, sub_key = key.split(".", 1)
            return self.modules.get(module_name, {}).get(sub_key, default)
        return self.settings.get(key, default)

    def to_flat_dict(self) -> Dict[str, Any]:
        """Flatten to a single dict for module.initialize(config)."""
        flat = dict(self.settings)
        flat["network"] = self.network
        flat["private_key"] = self.private_key
        for mod_name, mod_settings in self.modules.items():
            for k, v in mod_settings.items():
                flat[f"{mod_name}.{k}"] = v
        return flat

    @property
    def network_config(self) -> NetworkConfig:
        """Resolve the current network configuration."""
        rpc_override = os.environ.get("BSC_RPC_URL", "")
        nc = NETWORKS.get(self.network)
        if nc is None:
            raise ValueError(f"Unknown network: {self.network}")
        if rpc_override:
            nc = NetworkConfig(
                name=nc.name,
                chain_id=nc.chain_id,
                rpc_url=rpc_override,
                paymaster_url=nc.paymaster_url,
                use_paymaster=not rpc_override.startswith("http://localhost"),
            )
        return nc

    @classmethod
    def from_env(cls) -> "BNBAgentConfig":
        """Create config from environment variables."""
        return cls(
            network=os.getenv("NETWORK", "bsc-testnet"),
            private_key=os.getenv("PRIVATE_KEY", ""),
            settings={
                "storage_provider": os.getenv("STORAGE_PROVIDER", "local"),
                "local_storage_path": os.getenv("LOCAL_STORAGE_PATH", "./.agent-data"),
                "pinata_jwt": os.getenv("PINATA_JWT", ""),
                "debug": os.getenv("DEBUG", "false").lower() == "true",
            },
            modules={
                "apex": {
                    "erc8183_address": os.getenv(
                        "ERC8183_ADDRESS",
                        "0x3464e64dD53bC093c53050cE5114062765e9F1b6",
                    ),
                    "evaluator_address": os.getenv(
                        "APEX_EVALUATOR_ADDRESS",
                        "0x5f4976ACBCD2968D08273bA9f4a67FA43C4A3af3",
                    ),
                    "payment_token": os.getenv(
                        "PAYMENT_TOKEN_ADDRESS",
                        "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565",
                    ),
                    "agent_price": os.getenv("AGENT_PRICE", "1000000000000000000"),
                },
            },
        )
