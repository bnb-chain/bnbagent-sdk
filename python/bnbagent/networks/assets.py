"""Canonical payment-asset catalog for supported BNB Chain networks."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Literal

from web3 import Web3

from .addresses import (
    BSC_MAINNET_CHAIN_ID,
    BSC_TESTNET_CHAIN_ID,
    PAYMENT_TOKEN_EIP712_NAME,
    PAYMENT_TOKEN_EIP712_VERSION,
)

B402TransferMethod = Literal["eip3009", "permit2-exact"]
AssetAlias = Literal["U", "USDC", "USDT"]


class AssetId(str, Enum):
    """Stable asset identities used by SDK APIs and persisted configuration."""

    U = "U"
    TEST_U = "TEST_U"
    BINANCE_PEG_USDC = "BINANCE_PEG_USDC"
    BINANCE_PEG_USDT = "BINANCE_PEG_USDT"
    TEST_USDC = "TEST_USDC"
    TEST_USDT = "TEST_USDT"


@dataclass(frozen=True)
class EIP3009Domain:
    """Verified EIP-712 domain metadata for an EIP-3009 token."""

    name: str
    version: str


@dataclass(frozen=True)
class PaymentAsset:
    """Immutable metadata for one canonical asset on one network."""

    chain_id: int
    asset_id: AssetId
    symbol: str
    address: str
    decimals: int
    b402_methods: tuple[B402TransferMethod, ...]
    eip3009_domain: EIP3009Domain | None
    is_default: bool


class AssetCatalog:
    """Fail-closed lookup index keyed by network, canonical id, and address."""

    def __init__(self, assets: Sequence[PaymentAsset]) -> None:
        by_key: dict[tuple[int, AssetId], PaymentAsset] = {}
        by_address: dict[tuple[int, str], PaymentAsset] = {}
        by_chain: dict[int, list[PaymentAsset]] = {}

        for asset in assets:
            if not isinstance(asset.asset_id, AssetId):
                raise ValueError(f"asset_id must be a canonical AssetId: {asset.asset_id!r}")
            key = (asset.chain_id, asset.asset_id)
            if key in by_key:
                raise ValueError(
                    f"duplicate catalog key: chain_id={asset.chain_id}, "
                    f"asset_id={asset.asset_id.value}"
                )

            if not Web3.is_checksum_address(asset.address):
                raise ValueError(f"catalog address is not checksummed: {asset.address}")
            address_key = (asset.chain_id, asset.address.lower())
            if address_key in by_address:
                raise ValueError(
                    f"duplicate catalog address: chain_id={asset.chain_id}, "
                    f"address={asset.address}"
                )

            by_key[key] = asset
            by_address[address_key] = asset
            by_chain.setdefault(asset.chain_id, []).append(asset)

        self._by_key: Mapping[tuple[int, AssetId], PaymentAsset] = MappingProxyType(by_key)
        self._by_address: Mapping[tuple[int, str], PaymentAsset] = MappingProxyType(by_address)
        self._by_chain: Mapping[int, tuple[PaymentAsset, ...]] = MappingProxyType(
            {chain_id: tuple(chain_assets) for chain_id, chain_assets in by_chain.items()}
        )

    def _require_chain(self, chain_id: int) -> None:
        if chain_id not in self._by_chain:
            raise KeyError(f"no asset catalog registered for chain_id={chain_id}")

    def get(self, chain_id: int, asset_id: AssetId | str) -> PaymentAsset:
        self._require_chain(chain_id)
        canonical = parse_asset_id(asset_id)
        try:
            return self._by_key[(chain_id, canonical)]
        except KeyError as exc:
            raise KeyError(
                f"AssetId {canonical.value} is not available on chain_id={chain_id}"
            ) from exc

    def by_address(self, chain_id: int, address: str) -> PaymentAsset:
        self._require_chain(chain_id)
        try:
            address_key = Web3.to_checksum_address(address).lower()
        except (TypeError, ValueError) as exc:
            raise KeyError(
                f"asset address {address!r} is not registered on chain_id={chain_id}"
            ) from exc
        try:
            return self._by_address[(chain_id, address_key)]
        except KeyError as exc:
            raise KeyError(
                f"asset address {address!r} is not registered on chain_id={chain_id}"
            ) from exc

    def list(self, chain_id: int) -> tuple[PaymentAsset, ...]:
        self._require_chain(chain_id)
        return self._by_chain[chain_id]


_DOMAIN = EIP3009Domain(
    name=PAYMENT_TOKEN_EIP712_NAME,
    version=PAYMENT_TOKEN_EIP712_VERSION,
)

ASSET_CATALOG = AssetCatalog(
    (
        PaymentAsset(
            chain_id=BSC_MAINNET_CHAIN_ID,
            asset_id=AssetId.U,
            symbol="U",
            address="0xcE24439F2D9C6a2289F741120FE202248B666666",
            decimals=18,
            b402_methods=("eip3009", "permit2-exact"),
            eip3009_domain=_DOMAIN,
            is_default=True,
        ),
        PaymentAsset(
            chain_id=BSC_MAINNET_CHAIN_ID,
            asset_id=AssetId.BINANCE_PEG_USDC,
            symbol="USDC",
            address="0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
            decimals=18,
            b402_methods=("permit2-exact",),
            eip3009_domain=None,
            is_default=False,
        ),
        PaymentAsset(
            chain_id=BSC_MAINNET_CHAIN_ID,
            asset_id=AssetId.BINANCE_PEG_USDT,
            symbol="USDT",
            address="0x55d398326f99059fF775485246999027B3197955",
            decimals=18,
            b402_methods=("permit2-exact",),
            eip3009_domain=None,
            is_default=False,
        ),
        PaymentAsset(
            chain_id=BSC_TESTNET_CHAIN_ID,
            asset_id=AssetId.TEST_U,
            symbol="U",
            address="0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565",
            decimals=18,
            b402_methods=("eip3009",),
            eip3009_domain=_DOMAIN,
            is_default=True,
        ),
        PaymentAsset(
            chain_id=BSC_TESTNET_CHAIN_ID,
            asset_id=AssetId.TEST_USDC,
            symbol="USDC",
            address="0xEC1C60D64a06896Df296438c12edD14E974FDE47",
            decimals=6,
            b402_methods=("permit2-exact",),
            eip3009_domain=None,
            is_default=False,
        ),
        PaymentAsset(
            chain_id=BSC_TESTNET_CHAIN_ID,
            asset_id=AssetId.TEST_USDT,
            symbol="USDT",
            address="0x337610d27c682E347C9cD60BD4b3b107C9d34dDd",
            decimals=18,
            b402_methods=("permit2-exact",),
            eip3009_domain=None,
            is_default=False,
        ),
    )
)

_ALIASES: Mapping[int, Mapping[str, AssetId]] = MappingProxyType(
    {
        BSC_MAINNET_CHAIN_ID: MappingProxyType(
            {
                "U": AssetId.U,
                "USDC": AssetId.BINANCE_PEG_USDC,
                "USDT": AssetId.BINANCE_PEG_USDT,
            }
        ),
        BSC_TESTNET_CHAIN_ID: MappingProxyType(
            {
                "U": AssetId.TEST_U,
                "USDC": AssetId.TEST_USDC,
                "USDT": AssetId.TEST_USDT,
            }
        ),
    }
)

_DECIMAL_AMOUNT = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")


def parse_asset_id(value: AssetId | str) -> AssetId:
    """Parse a canonical ID without applying network-specific symbol aliases."""
    if isinstance(value, AssetId):
        return value
    try:
        return AssetId(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unknown canonical AssetId: {value!r}") from exc


def resolve_asset_alias(chain_id: int, alias: AssetAlias | str) -> AssetId:
    """Resolve a UI alias after the network context is known."""
    if chain_id not in _ALIASES:
        raise KeyError(f"no asset catalog registered for chain_id={chain_id}")
    try:
        return _ALIASES[chain_id][alias]
    except KeyError as exc:
        raise KeyError(f"unknown asset alias {alias!r} for chain_id={chain_id}") from exc


def get_asset(chain_id: int, asset_id: AssetId | str) -> PaymentAsset:
    """Return metadata for a strict ``(chain_id, canonical AssetId)`` key."""
    return ASSET_CATALOG.get(chain_id, asset_id)


def get_asset_by_address(chain_id: int, address: str) -> PaymentAsset:
    """Reverse-resolve a token address; input casing is ignored."""
    return ASSET_CATALOG.by_address(chain_id, address)


def list_assets(chain_id: int) -> tuple[PaymentAsset, ...]:
    """Return the immutable catalog snapshot for one known network."""
    return ASSET_CATALOG.list(chain_id)


def to_asset_atomic(chain_id: int, asset_id: AssetId | str, amount: str) -> int:
    """Convert a plain non-negative decimal string to exact atomic units."""
    if not isinstance(amount, str):
        raise TypeError("asset amount must be a decimal string")
    if _DECIMAL_AMOUNT.fullmatch(amount) is None:
        raise ValueError(f"invalid decimal amount: {amount!r}")

    asset = get_asset(chain_id, asset_id)
    whole, separator, fraction = amount.partition(".")
    if separator and len(fraction) > asset.decimals:
        raise ValueError(f"asset amount exceeds {asset.decimals} decimal places: {amount!r}")
    return int(whole) * 10**asset.decimals + int(fraction.ljust(asset.decimals, "0") or "0")
