"""
Unified configuration that aggregates module-specific configs.

Each module contributes its own defaults. User config overrides everything.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, replace
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class NetworkConfig:
    """Per-network configuration with ALL protocol addresses."""

    name: str
    chain_id: int
    rpc_url: str
    paymaster_url: str | None = None
    use_paymaster: bool = False
    # ERC-8004
    registry_contract: str = ""
    # APEX Protocol (uses ERC-8183 contract)
    erc8183_contract: str = ""
    apex_evaluator: str = ""
    payment_token: str = ""


NETWORKS: dict[str, NetworkConfig] = {
    "bsc-testnet": NetworkConfig(
        name="bsc-testnet",
        chain_id=97,
        rpc_url="https://data-seed-prebsc-2-s2.binance.org:8545",
        paymaster_url="https://bsc-megafuel-testnet.nodereal.io",
        use_paymaster=True,
        registry_contract="0x8004A818BFB912233c491871b3d84c89A494BD9e",
        erc8183_contract="0x3464e64dD53bC093c53050cE5114062765e9F1b6",
        apex_evaluator="0x5f4976ACBCD2968D08273bA9f4a67FA43C4A3af3",
        payment_token="0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565",
    ),
    "bsc-mainnet": NetworkConfig(
        name="bsc-mainnet",
        chain_id=56,
        rpc_url="https://bsc-dataseed.binance.org",
        registry_contract="",  # TBD
        erc8183_contract="",  # TBD
        apex_evaluator="",  # TBD
        payment_token="",  # TBD
    ),
}


def resolve_network(network: str = "bsc-testnet") -> NetworkConfig:
    """Resolve network with env var overrides.

    Env overrides:
        RPC_URL — override rpc_url
        IDENTITY_REGISTRY_ADDRESS — override registry_contract
        ERC8183_ADDRESS — override erc8183_contract
        APEX_EVALUATOR_ADDRESS — override apex_evaluator
        PAYMENT_TOKEN_ADDRESS — override payment_token
    """
    nc = NETWORKS.get(network)
    if nc is None:
        raise ValueError(f"Unknown network: {network}")

    overrides: dict[str, Any] = {}
    rpc_override = os.environ.get("RPC_URL", "")
    if rpc_override:
        overrides["rpc_url"] = rpc_override
        overrides["use_paymaster"] = not rpc_override.startswith("http://localhost")

    env_map = {
        "IDENTITY_REGISTRY_ADDRESS": "registry_contract",
        "ERC8183_ADDRESS": "erc8183_contract",
        "APEX_EVALUATOR_ADDRESS": "apex_evaluator",
        "PAYMENT_TOKEN_ADDRESS": "payment_token",
    }
    for env_key, field_name in env_map.items():
        val = os.environ.get(env_key, "")
        if val:
            overrides[field_name] = val

    if overrides:
        return replace(nc, **overrides)
    return nc


@dataclass
class BNBAgentConfig:
    """
    Unified SDK configuration.

    Aggregates:
    - Network selection
    - Wallet provider (preferred) or private_key + wallet_password (auto-wrapped)
    - Per-module settings (namespaced: "apex.evaluator_address")
    - General settings

    Usage:
        from bnbagent.wallets import EVMWalletProvider
        wallet = EVMWalletProvider(password="...", private_key="0x...")
        config = BNBAgentConfig(wallet_provider=wallet)

        # Or convenience (auto-wraps into EVMWalletProvider):
        config = BNBAgentConfig(private_key="0x...", wallet_password="...")

        # From environment:
        config = BNBAgentConfig.from_env()
    """

    network: str = "bsc-testnet"
    wallet_provider: Any = field(default=None, repr=False)  # WalletProvider
    settings: dict[str, Any] = field(default_factory=dict)
    modules: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Convenience: auto-wrapped into EVMWalletProvider
    private_key: str = field(default="", repr=False)
    wallet_password: str = field(default="", repr=False)
    wallet_address: str = ""  # select specific wallet from ~/.bnbagent/wallets/

    def __post_init__(self):
        """Auto-wrap private_key into WalletProvider."""
        if self.private_key and not self.private_key.startswith("0x"):
            self.private_key = f"0x{self.private_key}"

        if self.private_key and not self.wallet_provider:
            if not self.wallet_password:
                raise ValueError(
                    "wallet_password is required when using private_key. "
                    "Use BNBAgentConfig(private_key='0x...', wallet_password='...') "
                    "or pass wallet_provider= directly."
                )
            from .wallets import EVMWalletProvider

            self.wallet_provider = EVMWalletProvider(
                password=self.wallet_password,
                private_key=self.private_key,
            )
            self.private_key = ""  # Clear plaintext

        # Load from existing keystore when no private_key but password is given
        elif not self.private_key and not self.wallet_provider and self.wallet_password:
            from .wallets import EVMWalletProvider

            if EVMWalletProvider.keystore_exists(address=self.wallet_address or None):
                self.wallet_provider = EVMWalletProvider(
                    password=self.wallet_password,
                    address=self.wallet_address or None,
                )

    def __repr__(self) -> str:
        """Safe repr that hides sensitive data."""
        if self.wallet_provider:
            try:
                wallet_info = f"wallet='{self.wallet_provider.address[:10]}...'"
            except Exception:
                wallet_info = "wallet='<configured>'"
        else:
            wallet_info = "wallet=None"
        return (
            f"BNBAgentConfig("
            f"network='{self.network}', "
            f"{wallet_info}, "
            f"settings={list(self.settings.keys())}, "
            f"modules={list(self.modules.keys())})"
        )

    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value. Supports dotted keys: 'apex.evaluator_address'."""
        if "." in key:
            module_name, sub_key = key.split(".", 1)
            return self.modules.get(module_name, {}).get(sub_key, default)
        return self.settings.get(key, default)

    def to_flat_dict(self) -> dict[str, Any]:
        """Flatten to a single dict for module.initialize(config)."""
        flat = dict(self.settings)
        flat["network"] = self.network
        flat["wallet_provider"] = self.wallet_provider
        for mod_name, mod_settings in self.modules.items():
            for k, v in mod_settings.items():
                flat[f"{mod_name}.{k}"] = v
        return flat

    @property
    def network_config(self) -> NetworkConfig:
        """Resolve the current network configuration."""
        return resolve_network(self.network)

    @classmethod
    def from_env(cls) -> BNBAgentConfig:
        """Create config from environment variables.

        Reads PRIVATE_KEY + WALLET_PASSWORD and auto-wraps into EVMWalletProvider.
        If neither is set, wallet_provider will be None (read-only config).
        """
        private_key = os.getenv("PRIVATE_KEY", "")
        wallet_password = os.getenv("WALLET_PASSWORD", "")

        return cls(
            network=os.getenv("NETWORK", "bsc-testnet"),
            private_key=private_key,
            wallet_password=wallet_password,
            settings={
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
                    "service_price": os.getenv("SERVICE_PRICE", "1000000000000000000"),
                },
            },
        )
