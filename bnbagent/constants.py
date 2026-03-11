"""
Network configuration constants.

Environment variable overrides (for local fork / custom RPC):
  BSC_RPC_URL              — override rpc_url
  IDENTITY_REGISTRY_ADDRESS — override registry_contract
"""

import os

# 8004scan API base URL
SCAN_API_URL = "https://www.8004scan.io/api/v1"

# BSC Testnet configuration
TESTNET_CONFIG = {
    "name": "bsc-testnet",
    "chain_id": 97,
    "rpc_url": os.environ.get("BSC_RPC_URL", "https://data-seed-prebsc-1-s1.binance.org:8545"),
    "registry_contract": os.environ.get("IDENTITY_REGISTRY_ADDRESS", "0x8004A818BFB912233c491871b3d84c89A494BD9e"),
    "acp_contract": os.environ.get("ACP_ADDRESS", "0x8b121FEf5e1688B976D814003f05d9366F3Fa8A3"),
    "oov3_evaluator": os.environ.get("OOV3_EVALUATOR_ADDRESS", "0x283d858244932664bd69eb7FE3b1587b84B14be8"),
    "payment_token": os.environ.get("PAYMENT_TOKEN_ADDRESS", "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565"),
    "paymaster_url": "https://bsc-megafuel-testnet.nodereal.io",
    "paymaster": not os.environ.get("BSC_RPC_URL", "").startswith("http://localhost"),
}
