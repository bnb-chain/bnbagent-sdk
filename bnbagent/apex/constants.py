"""APEX protocol specific configuration."""
import os
from ..core.constants import _SHARED_TESTNET

APEX_CONFIG = {
    **_SHARED_TESTNET,
    "erc8183_contract": os.environ.get("ERC8183_ADDRESS", "0x3464e64dD53bC093c53050cE5114062765e9F1b6"),
    "apex_evaluator": os.environ.get("APEX_EVALUATOR_ADDRESS", "0x5f4976ACBCD2968D08273bA9f4a67FA43C4A3af3"),
    "payment_token": os.environ.get("PAYMENT_TOKEN_ADDRESS", "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565"),
}
