"""BNB Chain deployment registry — addresses + EIP-712 metadata.

Public API::

    from bnbagent.networks import (
        BNB_CHAIN_ADDRESSES,
        DeployedAddresses,
        get_address,
        known_payment_tokens,
        BSC_MAINNET_CHAIN_ID,
        BSC_TESTNET_CHAIN_ID,
        PAYMENT_TOKEN_EIP712_NAME,
        PAYMENT_TOKEN_EIP712_VERSION,
    )
"""

from __future__ import annotations

from .addresses import (
    BNB_CHAIN_ADDRESSES,
    BSC_MAINNET_CHAIN_ID,
    BSC_TESTNET_CHAIN_ID,
    DeployedAddresses,
    PAYMENT_TOKEN_EIP712_NAME,
    PAYMENT_TOKEN_EIP712_VERSION,
    get_address,
    known_payment_tokens,
)

__all__ = [
    "BNB_CHAIN_ADDRESSES",
    "BSC_MAINNET_CHAIN_ID",
    "BSC_TESTNET_CHAIN_ID",
    "DeployedAddresses",
    "PAYMENT_TOKEN_EIP712_NAME",
    "PAYMENT_TOKEN_EIP712_VERSION",
    "get_address",
    "known_payment_tokens",
]
