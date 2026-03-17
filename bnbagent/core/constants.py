"""
Shared network configuration constants.

Protocol-specific addresses are defined in each module's constants.py.
Environment variable overrides (for local fork / custom RPC):
  BSC_RPC_URL — override rpc_url
"""

import os

# 8004scan API base URL
SCAN_API_URL = "https://www.8004scan.io/api/v1"

# Shared BSC Testnet configuration (protocol-agnostic)
_SHARED_TESTNET = {
    "name": "bsc-testnet",
    "chain_id": 97,
    "rpc_url": os.environ.get("BSC_RPC_URL", "https://data-seed-prebsc-2-s2.binance.org:8545"),
    "paymaster_url": "https://bsc-megafuel-testnet.nodereal.io",
    "paymaster": not os.environ.get("BSC_RPC_URL", "").startswith("http://localhost"),
}


def get_network_config(network: str = "bsc-testnet") -> dict:
    """Get base network config (without protocol-specific addresses)."""
    if network == "bsc-testnet":
        return _SHARED_TESTNET.copy()
    raise ValueError(f"Unknown network: {network}")
