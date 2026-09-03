"""Strict B402 expected-asset resolution and wallet-route capabilities."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast, runtime_checkable

from web3 import Web3

from ..networks import (
    AssetId,
    B402Kind,
    B402TransferMethod,
    EIP3009Domain,
    get_asset,
    get_asset_by_address,
    known_eip3009_payment_tokens,
    parse_asset_id,
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
    b402_kinds: tuple[B402Kind, ...] = ()


@dataclass(frozen=True)
class B402WalletRoute:
    """A validated route for the exact expected asset (never a fallback)."""

    wallet_kind: str
    expected_asset: ExpectedB402Asset
    transfer_method: B402TransferMethod
    delegated: bool


@runtime_checkable
class DelegatedX402ExactPayerCapability(Protocol):
    """Minimal concrete-payer contract for a delegated exact route."""

    exact_transfer_methods: tuple[B402TransferMethod, ...]

    def request_exact(self, *args: Any, **kwargs: Any) -> Any:
        """Atomically bind a caller-selected route before payment."""
        ...


@dataclass(frozen=True)
class ExpectedEIP3009Route:
    """Caller-selected catalog route bound by :class:`X402Signer`.

    The challenge supplies typed-data, never its trust root. This immutable
    value contains the exact active token and EIP-712 domain that local
    EIP-3009 signing may use.
    """

    network: str
    chain_id: int
    asset_id: AssetId
    address: str
    transfer_method: Literal["eip3009"]
    name: str
    version: str


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
        b402_kinds=catalog.b402_kinds,
    )


def resolve_expected_eip3009_route(
    network: str | int, asset: AssetId | str
) -> ExpectedEIP3009Route:
    """Resolve one active catalog asset into its exact EIP-3009 signing route."""

    expected_asset = resolve_b402_asset(network, asset)
    kind = next(
        (candidate for candidate in expected_asset.b402_kinds if candidate.method == "eip3009"),
        None,
    )
    domain = expected_asset.eip3009_domain
    if (
        "eip3009" not in expected_asset.b402_methods
        or kind is None
        or domain is None
        or (kind.name, kind.version) != (domain.name, domain.version)
    ):
        raise ValueError(
            f"asset {expected_asset.asset_id.value} has no catalog EIP-3009 signing route"
        )
    return ExpectedEIP3009Route(
        network=expected_asset.network,
        chain_id=expected_asset.chain_id,
        asset_id=expected_asset.asset_id,
        address=expected_asset.address,
        transfer_method="eip3009",
        name=domain.name,
        version=domain.version,
    )


def require_expected_eip3009_route(route: ExpectedEIP3009Route) -> ExpectedEIP3009Route:
    """Re-resolve a public route and require an exact catalog-canonical match.

    An exact structural clone of resolver output is accepted; a stale,
    placeholder, or field-drifted route is not.
    """

    if not isinstance(route, ExpectedEIP3009Route):
        raise TypeError("expected EIP-3009 route must be catalog-derived")
    canonical = resolve_expected_eip3009_route(route.network, route.address)
    if route != canonical:
        raise ValueError("expected EIP-3009 route does not match the asset catalog")
    return canonical


def require_b402_wallet_route(
    wallet_kind: str,
    expected_asset: ExpectedB402Asset,
    transfer_method: str,
    delegated_payer: DelegatedX402ExactPayerCapability | object | None = None,
) -> B402WalletRoute:
    """Validate a wallet route for exactly ``expected_asset`` or raise typed unsupported."""

    # Address + network are the route's on-wire identity. Re-resolve them so a
    # caller-owned object can never smuggle mutable metadata or a str-enum
    # lookalike into the validated route. If the address itself is malformed,
    # resolve the claimed canonical identity only to produce a stable typed
    # refusal; it still cannot make ``catalog_matches`` true.
    address_resolved = True
    try:
        catalog_expected = resolve_b402_asset(expected_asset.network, expected_asset.address)
    except (KeyError, TypeError, ValueError):
        address_resolved = False
        catalog_expected = resolve_b402_asset(expected_asset.chain_id, expected_asset.asset_id)

    try:
        provided_asset_id = parse_asset_id(expected_asset.asset_id)
    except (TypeError, ValueError):
        provided_asset_id = None

    catalog_matches = (
        address_resolved
        and expected_asset.network == catalog_expected.network
        and expected_asset.chain_id == catalog_expected.chain_id
        and provided_asset_id is catalog_expected.asset_id
        and expected_asset.symbol == catalog_expected.symbol
        and expected_asset.address == catalog_expected.address
        and expected_asset.decimals == catalog_expected.decimals
        and expected_asset.b402_methods == catalog_expected.b402_methods
        and expected_asset.eip3009_domain == catalog_expected.eip3009_domain
        and expected_asset.b402_kinds == catalog_expected.b402_kinds
        and expected_asset.is_default == catalog_expected.is_default
    )
    method_supported = transfer_method in catalog_expected.b402_methods

    exact_transfer_methods = getattr(delegated_payer, "exact_transfer_methods", ())
    delegated = (
        wallet_kind in _DELEGATED_WALLETS
        and callable(getattr(delegated_payer, "request_exact", None))
        and isinstance(exact_transfer_methods, (tuple, list, frozenset))
        and transfer_method in exact_transfer_methods
    )
    supported = False
    if catalog_matches and method_supported and delegated:
        supported = True
    elif (
        catalog_matches
        and wallet_kind in _LOCAL_WALLETS
        and transfer_method == "eip3009"
        and catalog_expected.eip3009_domain is not None
        and (catalog_expected.chain_id, catalog_expected.address)
        in known_eip3009_payment_tokens()
    ):
        supported = True

    if not supported:
        raise UnsupportedWalletRouteError(
            wallet_kind=wallet_kind,
            network=catalog_expected.network,
            chain_id=catalog_expected.chain_id,
            asset_id=catalog_expected.asset_id,
            transfer_method=transfer_method,
        )

    return B402WalletRoute(
        wallet_kind=wallet_kind,
        expected_asset=catalog_expected,
        transfer_method=cast(B402TransferMethod, transfer_method),
        delegated=delegated,
    )
