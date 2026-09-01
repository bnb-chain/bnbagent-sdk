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
    PAYMENT_TOKEN_EIP712_NAME,
    PAYMENT_TOKEN_EIP712_VERSION,
    DeployedAddresses,
    get_address,
    known_payment_tokens,
)
from .assets import (
    ASSET_CATALOG,
    AssetAlias,
    AssetCatalog,
    AssetId,
    B402TransferMethod,
    EIP3009Domain,
    PaymentAsset,
    get_asset,
    get_asset_by_address,
    list_assets,
    parse_asset_id,
    resolve_asset_alias,
    to_asset_atomic,
)

__all__ = [
    "BNB_CHAIN_ADDRESSES",
    "ASSET_CATALOG",
    "AssetAlias",
    "AssetCatalog",
    "AssetId",
    "B402TransferMethod",
    "BSC_MAINNET_CHAIN_ID",
    "BSC_TESTNET_CHAIN_ID",
    "DeployedAddresses",
    "EIP3009Domain",
    "PAYMENT_TOKEN_EIP712_NAME",
    "PAYMENT_TOKEN_EIP712_VERSION",
    "PaymentAsset",
    "get_asset",
    "get_asset_by_address",
    "get_address",
    "known_payment_tokens",
    "list_assets",
    "parse_asset_id",
    "resolve_asset_alias",
    "to_asset_atomic",
]
