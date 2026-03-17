"""ERC-8004 Identity Registry specific configuration."""
import os
from ..core.constants import _SHARED_TESTNET

ERC8004_CONFIG = {
    **_SHARED_TESTNET,
    "registry_contract": os.environ.get(
        "IDENTITY_REGISTRY_ADDRESS", "0x8004A818BFB912233c491871b3d84c89A494BD9e"
    ),
}
