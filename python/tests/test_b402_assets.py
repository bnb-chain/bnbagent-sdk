"""Catalog-driven B402 expected-asset and wallet-route helpers."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

import bnbagent.x402 as x402
from bnbagent.networks import AssetId, get_asset
from bnbagent.x402 import X402Payer, X402PaymentOption


@pytest.mark.parametrize(
    ("network", "asset_id", "symbol", "decimals", "methods", "is_default"),
    [
        ("eip155:56", AssetId.U, "U", 18, ("eip3009", "permit2-exact"), True),
        (56, AssetId.BINANCE_PEG_USDC, "USDC", 18, ("permit2-exact",), False),
        (56, AssetId.BINANCE_PEG_USDT, "USDT", 18, ("permit2-exact",), False),
        ("eip155:97", AssetId.TEST_U, "U", 18, ("eip3009",), True),
        (97, AssetId.TEST_USDC, "USDC", 6, ("permit2-exact",), False),
        (97, AssetId.TEST_USDT, "USDT", 18, ("permit2-exact",), False),
    ],
)
def test_resolve_b402_asset_snapshot(
    network: str | int,
    asset_id: AssetId,
    symbol: str,
    decimals: int,
    methods: tuple[str, ...],
    is_default: bool,
) -> None:
    expected = x402.resolve_b402_asset(network, asset_id)
    catalog = get_asset(expected.chain_id, asset_id)

    assert (
        expected.network,
        expected.chain_id,
        expected.asset_id,
        expected.symbol,
        expected.address,
        expected.decimals,
        expected.b402_methods,
        expected.b402_kinds,
        expected.is_default,
    ) == (
        f"eip155:{catalog.chain_id}",
        catalog.chain_id,
        asset_id,
        symbol,
        catalog.address,
        decimals,
        methods,
        catalog.b402_kinds,
        is_default,
    )
    with pytest.raises(FrozenInstanceError):
        expected.symbol = "OTHER"  # type: ignore[misc]


@pytest.mark.parametrize("chain_id", [56, 97])
@pytest.mark.parametrize("asset_id", list(AssetId))
def test_resolve_b402_asset_accepts_only_same_chain_catalog_asset(
    chain_id: int, asset_id: AssetId
) -> None:
    try:
        catalog = get_asset(chain_id, asset_id)
    except KeyError:
        with pytest.raises(KeyError, match="not available"):
            x402.resolve_b402_asset(chain_id, asset_id)
    else:
        assert x402.resolve_b402_asset(chain_id, catalog.address).asset_id is asset_id


@pytest.mark.parametrize("network", ["56", "bsc", "eip155:1", "eip155:056", 1, True])
def test_resolve_b402_asset_rejects_unknown_or_malformed_network(
    network: str | int,
) -> None:
    with pytest.raises((TypeError, ValueError, KeyError)):
        x402.resolve_b402_asset(network, AssetId.U)


@pytest.mark.parametrize("asset", ["USDC", "USDT", "0x1234", "not-an-asset"])
def test_resolve_b402_asset_rejects_bare_symbols_and_malformed_assets(asset: str) -> None:
    with pytest.raises((ValueError, KeyError)):
        x402.resolve_b402_asset(56, asset)


def test_resolve_b402_asset_requires_checksum_address() -> None:
    usdc = get_asset(97, AssetId.TEST_USDC)

    with pytest.raises(ValueError, match="checksum"):
        x402.resolve_b402_asset(97, usdc.address.lower())


def test_resolve_b402_asset_rejects_cross_chain_address() -> None:
    mainnet_usdc = get_asset(56, AssetId.BINANCE_PEG_USDC)

    with pytest.raises(KeyError, match="not registered"):
        x402.resolve_b402_asset(97, mainnet_usdc.address)


@pytest.mark.parametrize("chain_id", [56, 97])
def test_evm_local_and_turnkey_allow_only_verified_known_eip3009_u(
    chain_id: int,
) -> None:
    asset_id = AssetId.U if chain_id == 56 else AssetId.TEST_U
    expected = x402.resolve_b402_asset(chain_id, asset_id)

    for wallet_kind in ("evm-local", "turnkey"):
        route = x402.require_b402_wallet_route(wallet_kind, expected, "eip3009")
        assert route.expected_asset == expected
        assert route.expected_asset is not expected
        assert route.transfer_method == "eip3009"
        assert route.delegated is False


@pytest.mark.parametrize("chain_id", [56, 97])
@pytest.mark.parametrize("symbol", ["USDC", "USDT"])
@pytest.mark.parametrize("wallet_kind", ["evm-local", "turnkey"])
def test_local_wallets_return_typed_unsupported_for_permit2_only_assets(
    chain_id: int, symbol: str, wallet_kind: str
) -> None:
    asset_id = {
        (56, "USDC"): AssetId.BINANCE_PEG_USDC,
        (56, "USDT"): AssetId.BINANCE_PEG_USDT,
        (97, "USDC"): AssetId.TEST_USDC,
        (97, "USDT"): AssetId.TEST_USDT,
    }[(chain_id, symbol)]
    expected = x402.resolve_b402_asset(chain_id, asset_id)

    with pytest.raises(x402.UnsupportedWalletRouteError) as raised:
        x402.require_b402_wallet_route(wallet_kind, expected, "permit2-exact")

    error = raised.value
    assert isinstance(error, x402.X402SignerError)
    assert (
        error.wallet_kind,
        error.network,
        error.chain_id,
        error.asset_id,
        error.transfer_method,
    ) == (wallet_kind, f"eip155:{chain_id}", chain_id, asset_id, "permit2-exact")
    assert wallet_kind in str(error)
    assert f"eip155:{chain_id}" in str(error)
    assert asset_id.value in str(error)
    assert "permit2-exact" in str(error)


@pytest.mark.parametrize("wallet_kind", ["evm-local", "turnkey"])
def test_local_wallets_do_not_open_permit2_even_when_u_catalog_supports_it(
    wallet_kind: str,
) -> None:
    expected = x402.resolve_b402_asset(56, AssetId.U)

    with pytest.raises(x402.UnsupportedWalletRouteError):
        x402.require_b402_wallet_route(wallet_kind, expected, "permit2-exact")


@pytest.mark.parametrize("wallet_kind", ["twak", "altana"])
@pytest.mark.parametrize(
    ("chain_id", "asset_id", "method"),
    [
        (56, AssetId.U, "eip3009"),
        (56, AssetId.U, "permit2-exact"),
        (56, AssetId.BINANCE_PEG_USDC, "permit2-exact"),
        (56, AssetId.BINANCE_PEG_USDT, "permit2-exact"),
        (97, AssetId.TEST_U, "eip3009"),
        (97, AssetId.TEST_USDC, "permit2-exact"),
        (97, AssetId.TEST_USDT, "permit2-exact"),
    ],
)
def test_delegated_wallets_allow_only_catalog_declared_routes(
    wallet_kind: str, chain_id: int, asset_id: AssetId, method: str
) -> None:
    expected = x402.resolve_b402_asset(chain_id, asset_id)

    route = x402.require_b402_wallet_route(wallet_kind, expected, method)

    assert route.expected_asset == expected
    assert route.expected_asset is not expected
    assert route.transfer_method == method
    assert route.delegated is True


@pytest.mark.parametrize("wallet_kind", ["twak", "altana"])
@pytest.mark.parametrize(
    ("chain_id", "asset_id", "method"),
    [
        (56, AssetId.BINANCE_PEG_USDC, "eip3009"),
        (56, AssetId.BINANCE_PEG_USDT, "eip3009"),
        (97, AssetId.TEST_U, "permit2-exact"),
    ],
)
def test_delegated_wallets_reject_methods_missing_from_asset_catalog(
    wallet_kind: str, chain_id: int, asset_id: AssetId, method: str
) -> None:
    expected = x402.resolve_b402_asset(chain_id, asset_id)

    with pytest.raises(x402.UnsupportedWalletRouteError):
        x402.require_b402_wallet_route(wallet_kind, expected, method)


@pytest.mark.parametrize("wallet_kind", ["evm-local", "turnkey", "twak", "altana"])
@pytest.mark.parametrize("method", ["permit2-upto", "permit2", "unknown"])
def test_all_wallets_fail_closed_without_cross_asset_fallback(
    wallet_kind: str, method: str
) -> None:
    expected = x402.resolve_b402_asset(97, AssetId.TEST_USDC)

    with pytest.raises(x402.UnsupportedWalletRouteError) as raised:
        x402.require_b402_wallet_route(wallet_kind, expected, method)

    assert raised.value.asset_id is AssetId.TEST_USDC
    assert raised.value.transfer_method == method


def test_unknown_wallet_is_typed_unsupported() -> None:
    expected = x402.resolve_b402_asset(56, AssetId.U)

    with pytest.raises(x402.UnsupportedWalletRouteError) as raised:
        x402.require_b402_wallet_route("mpc", expected, "eip3009")

    assert raised.value.wallet_kind == "mpc"
    assert raised.value.asset_id is AssetId.U


def test_route_helper_rejects_manually_forged_expected_metadata() -> None:
    expected = x402.resolve_b402_asset(56, AssetId.U)

    with pytest.raises(x402.UnsupportedWalletRouteError) as raised:
        x402.require_b402_wallet_route("evm-local", replace(expected, symbol="USDC"), "eip3009")

    assert raised.value.asset_id is AssetId.U
    assert raised.value.network == "eip155:56"


def test_route_normalizes_string_enum_identity_to_catalog_object() -> None:
    expected = x402.resolve_b402_asset(56, AssetId.U)
    fabricated = x402.ExpectedB402Asset(
        network=expected.network,
        chain_id=expected.chain_id,
        asset_id="U",  # type: ignore[arg-type]
        symbol=expected.symbol,
        address=expected.address,
        decimals=expected.decimals,
        b402_methods=expected.b402_methods,
        eip3009_domain=expected.eip3009_domain,
        is_default=expected.is_default,
        b402_kinds=expected.b402_kinds,
    )

    route = x402.require_b402_wallet_route("evm-local", fabricated, "eip3009")

    assert route.expected_asset == expected
    assert route.expected_asset is not fabricated
    assert route.expected_asset.asset_id is AssetId.U


def test_typed_error_uses_canonical_identity_for_fabricated_string_enum() -> None:
    expected = x402.resolve_b402_asset(56, AssetId.U)
    fabricated = replace(expected, asset_id="U")  # type: ignore[arg-type]

    with pytest.raises(x402.UnsupportedWalletRouteError) as raised:
        x402.require_b402_wallet_route("evm-local", fabricated, "permit2-exact")

    assert raised.value.asset_id is AssetId.U


def test_route_rejects_forged_b402_kind_identity() -> None:
    expected = x402.resolve_b402_asset(97, AssetId.TEST_USDT)
    forged_kind = replace(expected.b402_kinds[0], name="Tether USD")

    with pytest.raises(x402.UnsupportedWalletRouteError):
        x402.require_b402_wallet_route(
            "altana",
            replace(expected, b402_kinds=(forged_kind,)),
            "permit2-exact",
        )


def test_payment_option_derives_same_expected_asset_and_keeps_atomic_amount() -> None:
    token = get_asset(97, AssetId.TEST_USDC)
    option = X402PaymentOption.from_cli(
        {
            "network": "eip155:97",
            "asset": token.address,
            "amount": "1000001",
            "payTo": "0x0000000000000000000000000000000000000001",
            "transferMethod": "permit2-exact",
        }
    )

    expected = x402.expected_asset_from_payment_option(option)

    assert expected.asset_id is AssetId.TEST_USDC
    assert expected.decimals == 6
    assert option.amount == 1_000_001
    assert isinstance(option.amount, int)


def test_existing_payer_protocol_remains_structural() -> None:
    class ThirdPartyPayer:
        def quote(self, url: str, *, method: str = "GET", body: str | None = None):
            raise NotImplementedError

        def request(
            self,
            url: str,
            *,
            max_payment: int,
            method: str = "GET",
            body: str | None = None,
        ):
            raise NotImplementedError

    assert isinstance(ThirdPartyPayer(), X402Payer)
