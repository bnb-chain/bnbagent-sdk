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
    "rpc_url": os.environ.get("BSC_RPC_URL", "https://data-seed-prebsc-2-s2.binance.org:8545"),
    "registry_contract": os.environ.get("IDENTITY_REGISTRY_ADDRESS", "0x8004A818BFB912233c491871b3d84c89A494BD9e"),
    "erc8183_contract": os.environ.get("ERC8183_ADDRESS", "0x3464e64dD53bC093c53050cE5114062765e9F1b6"),
    "apex_evaluator": os.environ.get("APEX_EVALUATOR_ADDRESS", "0x5f4976ACBCD2968D08273bA9f4a67FA43C4A3af3"),
    "payment_token": os.environ.get("PAYMENT_TOKEN_ADDRESS", "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565"),
    "paymaster_url": "https://bsc-megafuel-testnet.nodereal.io",
    "paymaster": not os.environ.get("BSC_RPC_URL", "").startswith("http://localhost"),
}
