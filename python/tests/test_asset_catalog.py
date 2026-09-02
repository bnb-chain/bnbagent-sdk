"""Cross-language contract tests for the BNB Chain payment-asset catalog."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from typing import Any

import pytest
from web3 import Web3

import bnbagent.networks as networks


def _api(name: str) -> Any:
    assert hasattr(networks, name), f"bnbagent.networks.{name} is not implemented"
    return getattr(networks, name)


def _snapshot() -> list[dict[str, object]]:
    list_assets = _api("list_assets")
    result: list[dict[str, object]] = []
    for chain_id in (networks.BSC_MAINNET_CHAIN_ID, networks.BSC_TESTNET_CHAIN_ID):
        for asset in list_assets(chain_id):
            result.append(
                {
                    "chain_id": asset.chain_id,
                    "asset_id": asset.asset_id.value,
                    "symbol": asset.symbol,
                    "address": asset.address,
                    "decimals": asset.decimals,
                    "b402_methods": asset.b402_methods,
                    "b402_kinds": tuple(
                        (kind.method, kind.name, kind.version) for kind in asset.b402_kinds
                    ),
                    "eip3009_domain": (
                        None
                        if asset.eip3009_domain is None
                        else (
                            asset.eip3009_domain.name,
                            asset.eip3009_domain.version,
                        )
                    ),
                    "is_default": asset.is_default,
                }
            )
    return result


def test_asset_catalog_snapshot_matches_locked_bsc_matrix():
    assert _snapshot() == [
        {
            "chain_id": 56,
            "asset_id": "U",
            "symbol": "U",
            "address": "0xcE24439F2D9C6a2289F741120FE202248B666666",
            "decimals": 18,
            "b402_methods": ("eip3009", "permit2-exact"),
            "b402_kinds": (
                ("eip3009", "United Stables", "1"),
                ("permit2-exact", "United Stables", "1"),
            ),
            "eip3009_domain": ("United Stables", "1"),
            "is_default": True,
        },
        {
            "chain_id": 56,
            "asset_id": "BINANCE_PEG_USDC",
            "symbol": "USDC",
            "address": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
            "decimals": 18,
            "b402_methods": ("permit2-exact",),
            "b402_kinds": (("permit2-exact", "USD Coin", "1"),),
            "eip3009_domain": None,
            "is_default": False,
        },
        {
            "chain_id": 56,
            "asset_id": "BINANCE_PEG_USDT",
            "symbol": "USDT",
            "address": "0x55d398326f99059fF775485246999027B3197955",
            "decimals": 18,
            "b402_methods": ("permit2-exact",),
            "b402_kinds": (("permit2-exact", "Tether USD", "1"),),
            "eip3009_domain": None,
            "is_default": False,
        },
        {
            "chain_id": 97,
            "asset_id": "TEST_U",
            "symbol": "U",
            "address": "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565",
            "decimals": 18,
            "b402_methods": ("eip3009",),
            "b402_kinds": (("eip3009", "United Stables", "1"),),
            "eip3009_domain": ("United Stables", "1"),
            "is_default": True,
        },
        {
            "chain_id": 97,
            "asset_id": "TEST_USDC",
            "symbol": "USDC",
            "address": "0xEC1C60D64a06896Df296438c12edD14E974FDE47",
            "decimals": 6,
            "b402_methods": ("permit2-exact",),
            "b402_kinds": (("permit2-exact", "USD Coin", "1"),),
            "eip3009_domain": None,
            "is_default": False,
        },
        {
            "chain_id": 97,
            "asset_id": "TEST_USDT",
            "symbol": "USDT",
            "address": "0x337610d27c682E347C9cD60BD4b3b107C9d34dDd",
            "decimals": 18,
            "b402_methods": ("permit2-exact",),
            "b402_kinds": (("permit2-exact", "USDT Token", "1"),),
            "eip3009_domain": None,
            "is_default": False,
        },
    ]


def test_catalog_addresses_are_checksummed_and_reverse_lookup_is_case_insensitive():
    get_asset_by_address = _api("get_asset_by_address")
    for row in _snapshot():
        address = str(row["address"])
        assert Web3.is_checksum_address(address)
        resolved = get_asset_by_address(int(row["chain_id"]), address.lower())
        assert resolved.address == address
        assert resolved.asset_id.value == row["asset_id"]


def test_b402_kind_identity_snapshot_is_deeply_immutable():
    first = _api("get_asset")(56, _api("AssetId").U)

    assert isinstance(first.b402_kinds, tuple)
    with pytest.raises(FrozenInstanceError):
        first.b402_kinds[0].name = "Forged Token"  # type: ignore[misc]


def test_friendly_aliases_are_resolved_only_with_network_context():
    resolve_asset_alias = _api("resolve_asset_alias")
    assert resolve_asset_alias(56, "U").value == "U"
    assert resolve_asset_alias(56, "USDC").value == "BINANCE_PEG_USDC"
    assert resolve_asset_alias(56, "USDT").value == "BINANCE_PEG_USDT"
    assert resolve_asset_alias(97, "U").value == "TEST_U"
    assert resolve_asset_alias(97, "USDC").value == "TEST_USDC"
    assert resolve_asset_alias(97, "USDT").value == "TEST_USDT"


def test_canonical_parser_rejects_symbols_without_network_context():
    parse_asset_id = _api("parse_asset_id")
    assert parse_asset_id("BINANCE_PEG_USDC").value == "BINANCE_PEG_USDC"
    assert parse_asset_id("TEST_USDT").value == "TEST_USDT"
    with pytest.raises(ValueError, match="canonical AssetId"):
        parse_asset_id("USDC")
    with pytest.raises(ValueError, match="canonical AssetId"):
        parse_asset_id("USDT")


def test_catalog_fails_closed_for_unknown_chain_asset_alias_and_address():
    asset_id = _api("AssetId")
    get_asset = _api("get_asset")
    get_asset_by_address = _api("get_asset_by_address")
    list_assets = _api("list_assets")
    resolve_asset_alias = _api("resolve_asset_alias")

    with pytest.raises(KeyError, match="chain_id=1"):
        list_assets(1)
    with pytest.raises(KeyError, match="chain_id=1"):
        resolve_asset_alias(1, "USDC")
    with pytest.raises(KeyError, match="alias"):
        resolve_asset_alias(56, "BUSD")
    with pytest.raises(KeyError, match="not available"):
        get_asset(97, asset_id.BINANCE_PEG_USDC)
    with pytest.raises(KeyError, match="not registered"):
        get_asset_by_address(56, "0x0000000000000000000000000000000000000001")
    with pytest.raises(KeyError, match="not registered"):
        get_asset_by_address(97, "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d")


def test_catalog_constructor_rejects_duplicate_key_and_duplicate_address():
    asset_catalog = _api("AssetCatalog")
    asset_id = _api("AssetId")
    first = _api("get_asset")(56, asset_id.U)

    with pytest.raises(ValueError, match="duplicate catalog key"):
        asset_catalog((first, first))

    duplicate_address = replace(first, asset_id=asset_id.BINANCE_PEG_USDC)
    with pytest.raises(ValueError, match="duplicate catalog address"):
        asset_catalog((first, duplicate_address))


def test_catalog_constructor_rejects_noncanonical_id_and_nonchecksum_address():
    asset_catalog = _api("AssetCatalog")
    asset_id = _api("AssetId")
    first = _api("get_asset")(56, asset_id.U)

    invalid_id = replace(first, asset_id="NOT_CANONICAL")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="canonical AssetId"):
        asset_catalog((invalid_id,))

    lowercase_address = replace(first, address=first.address.lower())
    with pytest.raises(ValueError, match="not checksummed"):
        asset_catalog((lowercase_address,))


def test_catalog_constructor_requires_exactly_one_kind_per_b402_method():
    asset_catalog = _api("AssetCatalog")
    b402_kind = _api("B402Kind")
    first = _api("get_asset")(56, _api("AssetId").U)

    with pytest.raises(ValueError, match="missing B402 kind"):
        asset_catalog((replace(first, b402_kinds=first.b402_kinds[:1]),))
    with pytest.raises(ValueError, match="duplicate B402 kind"):
        asset_catalog((replace(first, b402_kinds=first.b402_kinds * 2),))
    with pytest.raises(ValueError, match="extra B402 kind"):
        asset_catalog(
            (
                replace(
                    first,
                    b402_methods=("eip3009",),
                    b402_kinds=(
                        first.b402_kinds[0],
                        b402_kind("permit2-exact", "United Stables", "1"),
                    ),
                ),
            )
        )


def test_catalog_constructor_rejects_duplicate_methods_and_eip3009_mismatch():
    asset_catalog = _api("AssetCatalog")
    b402_kind = _api("B402Kind")
    first = _api("get_asset")(56, _api("AssetId").U)

    with pytest.raises(ValueError, match="duplicate B402 method"):
        asset_catalog(
            (
                replace(
                    first,
                    b402_methods=("eip3009", "eip3009"),
                    b402_kinds=(first.b402_kinds[0],),
                ),
            )
        )
    with pytest.raises(ValueError, match="EIP-3009 kind must match"):
        asset_catalog(
            (
                replace(
                    first,
                    b402_kinds=(
                        b402_kind("eip3009", "Wrong Token", "1"),
                        first.b402_kinds[1],
                    ),
                ),
            )
        )


def test_asset_amount_conversion_uses_exact_non_negative_decimal_strings():
    asset_id = _api("AssetId")
    to_asset_atomic = _api("to_asset_atomic")

    assert to_asset_atomic(56, asset_id.U, "0") == 0
    assert to_asset_atomic(56, asset_id.U, "1.000000000000000001") == 10**18 + 1
    assert to_asset_atomic(97, asset_id.TEST_USDC, "1.000001") == 1_000_001

    for invalid in ("", " 1", "1 ", "+1", "-1", ".1", "1.", "1e-6", "1E6"):
        with pytest.raises(ValueError, match="decimal amount"):
            to_asset_atomic(56, asset_id.U, invalid)
    with pytest.raises(ValueError, match="decimal places"):
        to_asset_atomic(97, asset_id.TEST_USDC, "1.0000001")
    with pytest.raises(TypeError, match="string"):
        to_asset_atomic(56, asset_id.U, 1.1)


def test_legacy_default_address_and_eip3009_allowlist_remain_compatible():
    asset_id = _api("AssetId")
    get_asset = _api("get_asset")

    assert networks.get_address(56).payment_token == get_asset(56, asset_id.U).address
    assert networks.get_address(97).payment_token == get_asset(97, asset_id.TEST_U).address

    known = networks.known_payment_tokens()
    assert known == frozenset(
        {
            (56, "0xcE24439F2D9C6a2289F741120FE202248B666666"),
            (97, "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565"),
        }
    )
    assert all(
        "USDC" not in asset_id.value and "USDT" not in asset_id.value
        for asset_id in (
            get_asset(chain_id, candidate).asset_id
            for chain_id, candidate in (
                (56, asset_id.U),
                (97, asset_id.TEST_U),
            )
        )
    )
