"""
APEXConfig - unified configuration for APEX agents.

Supports:
- Explicit configuration via constructor
- Environment variable loading via from_env()
- Validation of required fields

Environment variables:
    BSC_RPC_URL or RPC_URL      - Blockchain RPC endpoint
    ERC8183_ADDRESS             - AgenticCommerceUpgradeable contract address (ERC-8183)
    APEX_EVALUATOR_ADDRESS      - APEX Evaluator contract address (default: BSC Testnet)
    PRIVATE_KEY                 - Agent wallet private key
    CHAIN_ID                    - Chain ID (default: 97 for BSC Testnet)
    STORAGE_PROVIDER            - "local" or "ipfs" (default: "local")
    PINATA_JWT                  - Pinata JWT token (required if STORAGE_PROVIDER=ipfs)
    PINATA_GATEWAY              - IPFS gateway URL (optional)
    LOCAL_STORAGE_PATH          - Path for local storage (default: ./.agent-data)
    AGENT_PRICE                 - Default negotiation price (default: 1e18)
    PAYMENT_TOKEN_ADDRESS       - BEP20 token for payments
"""

import os
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class APEXConfig:
    """
    Unified configuration for APEX agent operations.

    Required fields:
        rpc_url: Blockchain RPC endpoint
        erc8183_address: AgenticCommerceUpgradeable contract address (ERC-8183)
        private_key: Agent wallet private key

    Optional fields:
        apex_evaluator_address: APEX Evaluator contract address
        chain_id: Chain ID (default: 97)
        storage_provider: "local" or "ipfs" (default: "local")
        pinata_jwt: Pinata JWT for IPFS
        pinata_gateway: IPFS gateway URL
        local_storage_path: Local storage directory
        agent_price: Default negotiation price
        payment_token_address: BEP20 payment token
    """

    # Required
    rpc_url: str
    erc8183_address: str
    private_key: str

    # Optional (defaults for BSC Testnet)
    apex_evaluator_address: str = "0x5f4976ACBCD2968D08273bA9f4a67FA43C4A3af3"
    chain_id: int = 97
    storage_provider: str = "local"
    pinata_jwt: Optional[str] = None
    pinata_gateway: Optional[str] = None
    local_storage_path: str = "./.agent-data"
    agent_price: str = "1000000000000000000"  # 1 token (18 decimals)
    payment_token_address: Optional[str] = None

    def __post_init__(self):
        """Validate configuration after initialization."""
        self.validate()

    def __repr__(self) -> str:
        """Safe repr that hides private key."""
        return (
            f"APEXConfig("
            f"rpc_url='{self.rpc_url}', "
            f"erc8183_address='{self.erc8183_address}', "
            f"private_key='***', "
            f"chain_id={self.chain_id}, "
            f"storage_provider='{self.storage_provider}')"
        )

    def validate(self) -> None:
        """Validate required configuration fields."""
        errors = []

        if not self.rpc_url:
            errors.append("rpc_url is required")
        if not self.erc8183_address:
            errors.append("erc8183_address is required")
        if not self.private_key:
            errors.append("private_key is required")

        if self.storage_provider == "ipfs" and not self.pinata_jwt:
            errors.append("pinata_jwt is required when storage_provider is 'ipfs'")

        if errors:
            raise ValueError(f"APEXConfig validation failed: {', '.join(errors)}")

        # Normalize private key
        if not self.private_key.startswith("0x"):
            self.private_key = f"0x{self.private_key}"

    @classmethod
    def from_env(cls, prefix: str = "") -> "APEXConfig":
        """
        Create configuration from environment variables.

        Args:
            prefix: Optional prefix for env vars (e.g., "AGENT_" -> "AGENT_RPC_URL")

        Returns:
            APEXConfig instance

        Raises:
            ValueError: If required environment variables are missing
        """
        def get_env(key: str, default: Optional[str] = None) -> Optional[str]:
            # Try prefixed key first, then standard key
            prefixed_key = f"{prefix}{key}" if prefix else key
            value = os.getenv(prefixed_key)
            if value is None and prefix:
                value = os.getenv(key)
            return value if value is not None else default

        # Read values (both names are valid, BSC_RPC_URL takes priority for BSC networks)
        rpc_url = get_env("BSC_RPC_URL") or get_env("RPC_URL") or ""
        erc8183_address = get_env("ERC8183_ADDRESS") or ""
        private_key = get_env("PRIVATE_KEY") or ""

        return cls(
            rpc_url=rpc_url,
            erc8183_address=erc8183_address,
            private_key=private_key,
            apex_evaluator_address=get_env("APEX_EVALUATOR_ADDRESS") or "0x5f4976ACBCD2968D08273bA9f4a67FA43C4A3af3",
            chain_id=int(get_env("CHAIN_ID", "97")),
            storage_provider=get_env("STORAGE_PROVIDER", "local"),
            pinata_jwt=get_env("PINATA_JWT"),
            pinata_gateway=get_env("PINATA_GATEWAY"),
            local_storage_path=get_env("LOCAL_STORAGE_PATH", "./.agent-data"),
            agent_price=get_env("AGENT_PRICE", "1000000000000000000"),
            payment_token_address=get_env("PAYMENT_TOKEN_ADDRESS"),
        )

    @classmethod
    def from_env_optional(cls, prefix: str = "") -> Optional["APEXConfig"]:
        """
        Try to create configuration from environment variables.

        Returns None if required variables are missing, instead of raising.
        Useful for conditional APEX enablement.

        Args:
            prefix: Optional prefix for env vars

        Returns:
            APEXConfig instance or None if required vars missing
        """
        try:
            return cls.from_env(prefix)
        except ValueError as e:
            logger.info(f"[APEXConfig] APEX not configured: {e}")
            return None
