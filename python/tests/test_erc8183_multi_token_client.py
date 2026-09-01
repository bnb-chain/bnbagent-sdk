"""Multi-token behavior tests for the high-level ERC-8183 facade."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, call, patch

import pytest
from web3 import Web3

from bnbagent.config import NetworkConfig
from bnbagent.erc8183 import ERC8183Client
from bnbagent.networks import AssetId, get_asset
from tests.conftest import FAKE_ADDRESS

FAKE_COMMERCE = Web3.to_checksum_address("0x" + "aa" * 20)
FAKE_ROUTER = Web3.to_checksum_address("0x" + "bb" * 20)
FAKE_POLICY = Web3.to_checksum_address("0x" + "cc" * 20)
CUSTOM_TOKEN = "0x1234567890abcdef1234567890abcdef12345678"
CUSTOM_TOKEN_CHECKSUM = Web3.to_checksum_address(CUSTOM_TOKEN)


def _fake_network() -> NetworkConfig:
    return NetworkConfig(
        name="custom-test",
        rpc_url="https://fake-rpc.example.com",
        chain_id=12345,
        commerce_contract=FAKE_COMMERCE,
        router_contract=FAKE_ROUTER,
        policy_contract=FAKE_POLICY,
    )


@pytest.fixture
def multi_facade(mock_web3):
    wallet = MagicMock()
    wallet.address = FAKE_ADDRESS
    with (
        patch("bnbagent.erc8183.client.create_web3", return_value=mock_web3),
        patch("bnbagent.erc8183.client.CommerceClient") as commerce_cls,
        patch("bnbagent.erc8183.client.RouterClient") as router_cls,
        patch("bnbagent.erc8183.client.PolicyClient") as policy_cls,
        patch("bnbagent.erc8183.client.MinimalERC20Client") as erc20_cls,
    ):
        commerce = MagicMock()
        commerce.address = FAKE_COMMERCE
        router = MagicMock()
        router.address = FAKE_ROUTER
        policy = MagicMock()
        policy.address = FAKE_POLICY
        commerce_cls.return_value = commerce
        router_cls.return_value = router
        policy_cls.return_value = policy
        client = ERC8183Client(wallet, network=_fake_network())
        yield client, erc20_cls


class TestTokenResolution:
    def test_create_with_canonical_asset_id_uses_current_chain_catalog(self, multi_facade):
        facade, _ = multi_facade
        facade.network = replace(facade.network, chain_id=97)
        token = get_asset(97, AssetId.TEST_USDC).address

        facade.create_job_with_token(
            asset=AssetId.TEST_USDC,
            expired_at=123,
            description="six decimals",
            skip_expiry_check=True,
        )

        facade.commerce.create_job_with_token.assert_called_once_with(
            provider="0x0000000000000000000000000000000000000000",
            evaluator=FAKE_ROUTER,
            expired_at=123,
            description="six decimals",
            hook=FAKE_ROUTER,
            token=token,
        )

    def test_create_accepts_catalog_address_for_current_chain(self, multi_facade):
        facade, _ = multi_facade
        facade.network = replace(facade.network, chain_id=97)
        token = get_asset(97, AssetId.TEST_USDT).address

        facade.create_job_with_token(asset=token.lower(), expired_at=123, skip_expiry_check=True)

        assert facade.commerce.create_job_with_token.call_args.kwargs["token"] == token

    @pytest.mark.parametrize("asset", ["USDC", "USDT", AssetId.BINANCE_PEG_USDC])
    def test_create_rejects_alias_or_cross_chain_asset(self, multi_facade, asset):
        facade, _ = multi_facade
        facade.network = replace(facade.network, chain_id=97)

        with pytest.raises((ValueError, KeyError)):
            facade.create_job_with_token(asset=asset, expired_at=123, skip_expiry_check=True)

        facade.commerce.create_job_with_token.assert_not_called()

    def test_known_chain_rejects_non_catalog_address_for_new_job(self, multi_facade):
        facade, _ = multi_facade
        facade.network = replace(facade.network, chain_id=97)

        with pytest.raises(KeyError, match="not registered"):
            facade.create_job_with_token(
                asset=CUSTOM_TOKEN, expired_at=123, skip_expiry_check=True
            )

    def test_custom_chain_accepts_direct_address_but_not_asset_id(self, multi_facade):
        facade, _ = multi_facade

        facade.create_job_with_token(asset=CUSTOM_TOKEN, expired_at=123, skip_expiry_check=True)
        assert (
            facade.commerce.create_job_with_token.call_args.kwargs["token"]
            == CUSTOM_TOKEN_CHECKSUM
        )

        facade.commerce.create_job_with_token.reset_mock()
        with pytest.raises(KeyError, match="chain_id=12345"):
            facade.create_job_with_token(
                asset=AssetId.TEST_USDC, expired_at=123, skip_expiry_check=True
            )
        facade.commerce.create_job_with_token.assert_not_called()


class TestPerTokenHelpers:
    def test_job_token_and_unsupported_support_read_are_exposed(self, multi_facade):
        facade, _ = multi_facade
        facade.commerce.job_payment_token.return_value = CUSTOM_TOKEN
        facade.commerce.is_payment_token_supported.return_value = False

        assert facade.job_payment_token(7) == CUSTOM_TOKEN_CHECKSUM
        assert facade.is_payment_token_supported(CUSTOM_TOKEN.lower()) is False
        facade.commerce.is_payment_token_supported.assert_called_once_with(CUSTOM_TOKEN_CHECKSUM)

    def test_metadata_cache_is_keyed_by_checksum_address_for_6_and_18_decimals(self, multi_facade):
        facade, erc20_cls = multi_facade
        usdc = get_asset(97, AssetId.TEST_USDC).address
        usdt = get_asset(97, AssetId.TEST_USDT).address
        usdc_client = MagicMock()
        usdc_client.decimals.return_value = 6
        usdc_client.symbol.return_value = "USDC"
        usdt_client = MagicMock()
        usdt_client.decimals.return_value = 18
        usdt_client.symbol.return_value = "USDT"
        erc20_cls.side_effect = [usdc_client, usdt_client]

        usdc_metadata = facade.token_metadata(usdc.lower())
        assert facade.token_metadata(usdc) is usdc_metadata
        usdt_metadata = facade.token_metadata(usdt)

        assert (usdc_metadata.address, usdc_metadata.decimals, usdc_metadata.symbol) == (
            usdc,
            6,
            "USDC",
        )
        assert (usdt_metadata.address, usdt_metadata.decimals, usdt_metadata.symbol) == (
            usdt,
            18,
            "USDT",
        )
        assert usdc_client.decimals.call_count == 1
        assert usdc_client.symbol.call_count == 1
        assert usdt_client.decimals.call_count == 1
        assert usdt_client.symbol.call_count == 1
        assert erc20_cls.call_args_list == [
            call(facade.w3, usdc, facade._wallet_provider),
            call(facade.w3, usdt, facade._wallet_provider),
        ]

    def test_balance_allowance_and_approve_use_selected_token_client(self, multi_facade):
        facade, erc20_cls = multi_facade
        erc20 = MagicMock()
        erc20.balance_of.return_value = 55
        erc20.allowance.return_value = 44
        erc20.approve.return_value = {"status": 1}
        erc20_cls.return_value = erc20

        assert facade.token_balance_for(CUSTOM_TOKEN, FAKE_ADDRESS) == 55
        assert facade.token_allowance_for(CUSTOM_TOKEN, FAKE_ADDRESS, FAKE_COMMERCE) == 44
        assert facade.approve_token(CUSTOM_TOKEN, FAKE_COMMERCE, 33) == {"status": 1}
        assert erc20_cls.call_count == 1
        erc20.balance_of.assert_called_once_with(FAKE_ADDRESS)
        erc20.allowance.assert_called_once_with(FAKE_ADDRESS, FAKE_COMMERCE)
        erc20.approve.assert_called_once_with(FAKE_COMMERCE, 33)

    def test_legacy_helpers_continue_to_use_commerce_default_token(self, multi_facade):
        facade, erc20_cls = multi_facade
        facade.commerce.payment_token.return_value = CUSTOM_TOKEN
        erc20 = MagicMock()
        erc20.decimals.return_value = 18
        erc20.symbol.return_value = "U"
        erc20.balance_of.return_value = 9
        erc20.allowance.return_value = 8
        erc20.approve.return_value = {"status": 1}
        erc20_cls.return_value = erc20

        assert facade.payment_token == CUSTOM_TOKEN_CHECKSUM
        assert facade.token_decimals() == 18
        assert facade.token_symbol() == "U"
        assert facade.token_balance() == 9
        assert facade.token_allowance(FAKE_ADDRESS, FAKE_COMMERCE) == 8
        assert facade.approve_payment_token(FAKE_COMMERCE, 7) == {"status": 1}
        assert erc20_cls.call_count == 1
