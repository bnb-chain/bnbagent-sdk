"""
APEXConfig — unified configuration for APEX agents.

Supports:
- Explicit configuration via constructor
- Environment variable loading via from_env()
- WalletProvider and StorageProvider injection
- Network-based defaults via resolve_network()

Environment variables:
    BSC_RPC_URL or RPC_URL      - Blockchain RPC endpoint (overrides network default)
    ERC8183_ADDRESS             - ERC-8183 contract address (overrides network default)
    APEX_EVALUATOR_ADDRESS      - APEX Evaluator address (overrides network default)
    PRIVATE_KEY                 - Agent wallet private key
    WALLET_PASSWORD             - Password for wallet encryption (required with PRIVATE_KEY)
    CHAIN_ID                    - Chain ID (overrides network default)
    AGENT_PRICE                 - Default negotiation price (default: 1e18)
    PAYMENT_TOKEN_ADDRESS       - BEP20 token for payments (overrides network default)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..config import resolve_network
from ..storage.config import StorageConfig

if TYPE_CHECKING:
    from ..storage.interface import StorageProvider
    from ..wallets.wallet_provider import WalletProvider

logger = logging.getLogger(__name__)


@dataclass
class APEXConfig:
    """Unified configuration for APEX agent operations.

    Primary API:
        wallet_provider: WalletProvider for signing (preferred)
        storage: StorageProvider for off-chain storage

    Convenience API:
        private_key + wallet_password: Auto-wrapped into EVMWalletProvider
    """

    # Primary API
    network: str = "bsc-testnet"
    wallet_provider: WalletProvider | None = field(default=None, repr=False)
    storage: StorageProvider | None = field(default=None, repr=False)
    agent_price: str = "1000000000000000000"  # 1 token (18 decimals)

    # Convenience: auto-wrapped into EVMWalletProvider
    private_key: str = field(default="", repr=False)
    wallet_password: str = field(default="", repr=False)
    wallet_address: str = ""  # select specific wallet from ~/.bnbagent/wallets/

    # Override fields
    rpc_url: str = ""  # override network default
    chain_id: int = 0  # override network default
    erc8183_address: str = ""  # override network default
    apex_evaluator_address: str = ""  # override network default
    payment_token_address: str = ""  # override network default

    def __post_init__(self):
        """Validate and auto-wrap private_key into WalletProvider."""
        # Normalize private key
        if self.private_key and not self.private_key.startswith("0x"):
            self.private_key = f"0x{self.private_key}"

        # Auto-wrap: private_key + wallet_password → EVMWalletProvider
        if self.private_key and not self.wallet_provider:
            if not self.wallet_password:
                raise ValueError(
                    "wallet_password is required when using private_key. "
                    "Use APEXConfig(private_key='0x...', wallet_password='...') "
                    "or pass wallet_provider= directly."
                )
            from ..wallets import EVMWalletProvider

            self.wallet_provider = EVMWalletProvider(
                password=self.wallet_password,
                private_key=self.private_key,
            )
            self.private_key = ""  # Clear plaintext

        # Load from existing keystore when no private_key but password is given
        elif not self.private_key and not self.wallet_provider and self.wallet_password:
            from ..wallets import EVMWalletProvider

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
            f"APEXConfig("
            f"network='{self.network}', "
            f"{wallet_info}, "
            f"chain_id={self.effective_chain_id}, "
            f"erc8183='{self.effective_erc8183_address[:10]}...')"
        )

    @property
    def effective_rpc_url(self) -> str:
        return self.rpc_url or resolve_network(self.network).rpc_url

    @property
    def effective_chain_id(self) -> int:
        return self.chain_id or resolve_network(self.network).chain_id

    @property
    def effective_erc8183_address(self) -> str:
        return self.erc8183_address or resolve_network(self.network).erc8183_contract

    @property
    def effective_evaluator_address(self) -> str:
        return self.apex_evaluator_address or resolve_network(self.network).apex_evaluator

    @property
    def effective_payment_token(self) -> str:
        return self.payment_token_address or resolve_network(self.network).payment_token

    @classmethod
    def from_env(cls, prefix: str = "") -> APEXConfig:
        """Create configuration from environment variables.

        Args:
            prefix: Optional prefix for env vars (e.g., "AGENT_" -> "AGENT_RPC_URL")

        Returns:
            APEXConfig instance

        Raises:
            ValueError: If required environment variables are missing
        """

        def get_env(key: str, default: str | None = None) -> str | None:
            prefixed_key = f"{prefix}{key}" if prefix else key
            value = os.getenv(prefixed_key)
            if value is None and prefix:
                value = os.getenv(key)
            return value if value is not None else default

        wallet_password = get_env("WALLET_PASSWORD") or ""
        if not wallet_password:
            raise ValueError(
                "APEXConfig validation failed: WALLET_PASSWORD is required. "
                "Set WALLET_PASSWORD env var to encrypt/decrypt the wallet keystore."
            )

        private_key = get_env("PRIVATE_KEY") or ""
        wallet_address = get_env("WALLET_ADDRESS") or ""

        # PRIVATE_KEY is required on first run only; after that the encrypted
        # keystore in ~/.bnbagent/wallets/ is used and PRIVATE_KEY can be removed.
        if not private_key:
            from ..wallets import EVMWalletProvider

            if not EVMWalletProvider.keystore_exists(address=wallet_address or None):
                raise ValueError(
                    "APEXConfig validation failed: PRIVATE_KEY is required on first run. "
                    "After first run, the key is encrypted in ~/.bnbagent/wallets/ "
                    "and only WALLET_PASSWORD is needed."
                )
            logger.info("[APEXConfig] Loading wallet from existing keystore (PRIVATE_KEY not set)")

        # Build storage from StorageConfig
        storage_config = StorageConfig.from_env()
        from ..storage.factory import create_storage_provider

        storage = create_storage_provider(storage_config)

        return cls(
            network=get_env("NETWORK", "bsc-testnet"),
            private_key=private_key,
            wallet_password=wallet_password,
            wallet_address=wallet_address,
            storage=storage,
            agent_price=get_env("AGENT_PRICE", "1000000000000000000"),
            rpc_url=get_env("BSC_RPC_URL") or get_env("RPC_URL") or "",
            chain_id=int(get_env("CHAIN_ID", "0")),
            erc8183_address=get_env("ERC8183_ADDRESS") or "",
            apex_evaluator_address=get_env("APEX_EVALUATOR_ADDRESS") or "",
            payment_token_address=get_env("PAYMENT_TOKEN_ADDRESS") or "",
        )

    @classmethod
    def from_env_optional(cls, prefix: str = "") -> APEXConfig | None:
        """Try to create configuration from environment variables.

        Returns None if required variables are missing.
        """
        try:
            return cls.from_env(prefix)
        except ValueError as e:
            logger.info("[APEXConfig] APEX not configured: %s", e)
            return None
