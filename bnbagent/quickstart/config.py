"""
ACPConfig - unified configuration for ACP agents.

Supports:
- Explicit configuration via constructor
- Environment variable loading via from_env()
- Validation of required fields

Environment variables:
    BSC_RPC_URL or RPC_URL   - Blockchain RPC endpoint
    ACP_ADDRESS              - AgenticCommerceUpgradeable contract address
    OOV3_EVALUATOR_ADDRESS   - OOv3Evaluator contract address (default: BSC Testnet)
    PRIVATE_KEY              - Agent wallet private key
    CHAIN_ID                 - Chain ID (default: 97 for BSC Testnet)
    STORAGE_PROVIDER         - "local" or "ipfs" (default: "local")
    PINATA_JWT               - Pinata JWT token (required if STORAGE_PROVIDER=ipfs)
    PINATA_GATEWAY           - IPFS gateway URL (optional)
    LOCAL_STORAGE_PATH       - Path for local storage (default: ./.storage)
    AGENT_PRICE              - Default negotiation price (default: 1e18)
    PAYMENT_TOKEN_ADDRESS    - ERC20 token for payments
"""

import os
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ACPConfig:
    """
    Unified configuration for ACP agent operations.
    
    Required fields:
        rpc_url: Blockchain RPC endpoint
        acp_address: AgenticCommerceUpgradeable contract address
        private_key: Agent wallet private key
    
    Optional fields:
        oov3_evaluator_address: OOv3Evaluator contract address
        chain_id: Chain ID (default: 97)
        storage_provider: "local" or "ipfs" (default: "local")
        pinata_jwt: Pinata JWT for IPFS
        pinata_gateway: IPFS gateway URL
        local_storage_path: Local storage directory
        agent_price: Default negotiation price
        payment_token_address: ERC20 payment token
    """
    
    # Required
    rpc_url: str
    acp_address: str
    private_key: str
    
    # Optional (defaults for BSC Testnet)
    oov3_evaluator_address: str = "0x283d858244932664bd69eb7FE3b1587b84B14be8"
    chain_id: int = 97
    storage_provider: str = "local"
    pinata_jwt: Optional[str] = None
    pinata_gateway: Optional[str] = None
    local_storage_path: str = "./.storage"
    agent_price: str = "1000000000000000000"  # 1 token (18 decimals)
    payment_token_address: Optional[str] = None
    
    def __post_init__(self):
        """Validate configuration after initialization."""
        self.validate()
    
    def __repr__(self) -> str:
        """Safe repr that hides private key."""
        return (
            f"ACPConfig("
            f"rpc_url='{self.rpc_url}', "
            f"acp_address='{self.acp_address}', "
            f"private_key='***', "
            f"chain_id={self.chain_id}, "
            f"storage_provider='{self.storage_provider}')"
        )
    
    def validate(self) -> None:
        """Validate required configuration fields."""
        errors = []
        
        if not self.rpc_url:
            errors.append("rpc_url is required")
        if not self.acp_address:
            errors.append("acp_address is required")
        if not self.private_key:
            errors.append("private_key is required")
        
        if self.storage_provider == "ipfs" and not self.pinata_jwt:
            errors.append("pinata_jwt is required when storage_provider is 'ipfs'")
        
        if errors:
            raise ValueError(f"ACPConfig validation failed: {', '.join(errors)}")
        
        # Normalize private key
        if not self.private_key.startswith("0x"):
            self.private_key = f"0x{self.private_key}"
    
    @classmethod
    def from_env(cls, prefix: str = "") -> "ACPConfig":
        """
        Create configuration from environment variables.
        
        Args:
            prefix: Optional prefix for env vars (e.g., "AGENT_" -> "AGENT_RPC_URL")
        
        Returns:
            ACPConfig instance
        
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
        acp_address = get_env("ACP_ADDRESS") or get_env("ACP_CONTRACT_ADDRESS") or ""
        private_key = get_env("PRIVATE_KEY") or ""
        
        return cls(
            rpc_url=rpc_url,
            acp_address=acp_address,
            private_key=private_key,
            oov3_evaluator_address=get_env("OOV3_EVALUATOR_ADDRESS", "0x283d858244932664bd69eb7FE3b1587b84B14be8"),
            chain_id=int(get_env("CHAIN_ID", "97")),
            storage_provider=get_env("STORAGE_PROVIDER", "local"),
            pinata_jwt=get_env("PINATA_JWT"),
            pinata_gateway=get_env("PINATA_GATEWAY"),
            local_storage_path=get_env("LOCAL_STORAGE_PATH", "./.storage"),
            agent_price=get_env("AGENT_PRICE", "1000000000000000000"),
            payment_token_address=get_env("PAYMENT_TOKEN_ADDRESS"),
        )
    
    @classmethod
    def from_env_optional(cls, prefix: str = "") -> Optional["ACPConfig"]:
        """
        Try to create configuration from environment variables.
        
        Returns None if required variables are missing, instead of raising.
        Useful for conditional ACP enablement.
        
        Args:
            prefix: Optional prefix for env vars
        
        Returns:
            ACPConfig instance or None if required vars missing
        """
        try:
            return cls.from_env(prefix)
        except ValueError as e:
            logger.info(f"[ACPConfig] ACP not configured: {e}")
            return None
