"""Strict B402 expected-asset resolution and wallet-route capabilities."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import cast

from web3 import Web3

from ..networks import (
    AssetId,
    B402TransferMethod,
    EIP3009Domain,
    get_asset,
    get_asset_by_address,
    known_payment_tokens,
)
from .errors import UnsupportedWalletRouteError

_CAIP2_NETWORK = re.compile(r"^eip155:(56|97)$")
_LOCAL_WALLETS = frozenset(("evm-local", "turnkey"))
_DELEGATED_WALLETS = frozenset(("twak", "altana"))


@dataclass(frozen=True)
class ExpectedB402Asset:
    """Catalog metadata committed by a caller for one B402 route."""

    network: str
    chain_id: int
    asset_id: AssetId
    symbol: str
    address: str
    decimals: int
    b402_methods: tuple[B402TransferMethod, ...]
    eip3009_domain: EIP3009Domain | None
    is_default: bool


@dataclass(frozen=True)
class B402WalletRoute:
    """A validated route for the exact expected asset (never a fallback)."""

    wallet_kind: str
    expected_asset: ExpectedB402Asset
    transfer_method: B402TransferMethod
    delegated: bool


def _parse_network(network: str | int) -> int:
    if isinstance(network, bool):
        raise TypeError("B402 network must be a chain id or CAIP-2 eip155 network")
    if isinstance(network, int):
        # get_asset performs the known-chain check below.
        return network
    if not isinstance(network, str):
        raise TypeError("B402 network must be a chain id or CAIP-2 eip155 network")
    matched = _CAIP2_NETWORK.fullmatch(network)
    if matched is None:
        raise ValueError(f"unsupported or malformed B402 network: {network!r}")
    return int(matched.group(1))


def resolve_b402_asset(network: str | int, asset: AssetId | str) -> ExpectedB402Asset:
    """Resolve a canonical AssetId or checksum address on one known network.

    UI aliases such as ``USDC``/``USDT`` are deliberately not accepted.
    Address inputs must already be checksummed; accepting arbitrary casing at
    this trust boundary would hide malformed or cross-network quote metadata.
    """

    chain_id = _parse_network(network)
    if isinstance(asset, str) and asset.startswith("0x"):
        if not Web3.is_checksum_address(asset):
            raise ValueError(f"B402 asset address must be checksummed: {asset!r}")
        catalog = get_asset_by_address(chain_id, asset)
    else:
        catalog = get_asset(chain_id, asset)

    return ExpectedB402Asset(
        network=f"eip155:{chain_id}",
        chain_id=chain_id,
        asset_id=catalog.asset_id,
        symbol=catalog.symbol,
        address=catalog.address,
        decimals=catalog.decimals,
        b402_methods=catalog.b402_methods,
        eip3009_domain=catalog.eip3009_domain,
        is_default=catalog.is_default,
    )


def require_b402_wallet_route(
    wallet_kind: str,
    expected_asset: ExpectedB402Asset,
    transfer_method: str,
) -> B402WalletRoute:
    """Validate a wallet route for exactly ``expected_asset`` or raise typed unsupported."""

    # Re-resolve the canonical identity so manually constructed metadata cannot
    # smuggle an unverified EIP-3009 domain or method into the capability gate.
    catalog_expected = resolve_b402_asset(expected_asset.network, expected_asset.asset_id)
    catalog_matches = catalog_expected == expected_asset
    method_supported = transfer_method in expected_asset.b402_methods

    delegated = wallet_kind in _DELEGATED_WALLETS
    supported = False
    if catalog_matches and method_supported and delegated:
        supported = True
    elif (
        catalog_matches
        and wallet_kind in _LOCAL_WALLETS
        and transfer_method == "eip3009"
        and expected_asset.eip3009_domain is not None
        and (expected_asset.chain_id, expected_asset.address) in known_payment_tokens()
    ):
        supported = True

    if not supported:
        raise UnsupportedWalletRouteError(
            wallet_kind=wallet_kind,
            network=expected_asset.network,
            chain_id=expected_asset.chain_id,
            asset_id=expected_asset.asset_id,
            transfer_method=transfer_method,
        )

    return B402WalletRoute(
        wallet_kind=wallet_kind,
        expected_asset=expected_asset,
        transfer_method=cast(B402TransferMethod, transfer_method),
        delegated=delegated,
    )
