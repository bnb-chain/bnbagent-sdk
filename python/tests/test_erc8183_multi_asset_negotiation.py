"""Multi-asset ERC-8183 negotiation behavior."""

from unittest.mock import MagicMock, patch

import pytest

from bnbagent.erc8183.negotiation import NegotiationHandler, ReasonCode
from bnbagent.networks import AssetId, get_asset

CHAIN_ID = 97
COMMERCE = "0xA206c0517b6371c6638Cd9e4A42cC9F02A33B0de"
TEST_U = get_asset(CHAIN_ID, AssetId.TEST_U)
TEST_USDC = get_asset(CHAIN_ID, AssetId.TEST_USDC)
TEST_USDT = get_asset(CHAIN_ID, AssetId.TEST_USDT)
MAINNET_CHAIN_ID = 56
MAINNET_U = get_asset(MAINNET_CHAIN_ID, AssetId.U)
MAINNET_USD1 = get_asset(MAINNET_CHAIN_ID, AssetId.USD1)


def _request(currency: str | None = None) -> dict:
    terms = {"deliverables": "summary", "quality_standards": "accurate"}
    if currency is not None:
        terms["currency"] = currency
    return {"task_description": "Summarize the report", "terms": terms}


def _client(*, supported: set[str], default: str = TEST_U.address) -> MagicMock:
    client = MagicMock()
    client.network.chain_id = CHAIN_ID
    client.commerce.address = COMMERCE
    client.payment_token = default
    client.is_payment_token_supported.side_effect = lambda token: (
        token.lower() in {address.lower() for address in supported}
    )
    return client


def _multi_handler(client: MagicMock) -> NegotiationHandler:
    return NegotiationHandler.from_erc8183_client_multi(
        erc8183_client=client,
        service_prices={
            AssetId.TEST_USDC: "100000",
            AssetId.TEST_USDT: "100000000000000000",
        },
    )


def test_explicit_currency_selects_exact_active_offer_and_atomic_price():
    handler = _multi_handler(_client(supported={TEST_USDC.address, TEST_USDT.address}))

    result = handler.negotiate(_request(TEST_USDC.address.lower()))

    assert result.accepted is True
    assert result.response["terms"]["currency"] == TEST_USDC.address
    assert result.response["terms"]["price"] == "100000"


def test_usdc_usdt_only_does_not_substitute_for_omitted_default():
    handler = _multi_handler(_client(supported={TEST_USDC.address, TEST_USDT.address}))

    result = handler.negotiate(_request())

    assert result.accepted is False
    assert result.response["reason_code"] == ReasonCode.UNSUPPORTED
    assert result.response["details"] == {
        "supported_assets": [AssetId.TEST_USDC.value, AssetId.TEST_USDT.value]
    }


def test_disabled_asset_is_removed_without_disabling_other_offers():
    handler = _multi_handler(_client(supported={TEST_USDC.address}))

    assert handler.negotiate(_request(TEST_USDC.address)).accepted is True
    rejected = handler.negotiate(_request(TEST_USDT.address))
    assert rejected.accepted is False
    assert rejected.response["reason_code"] == ReasonCode.UNSUPPORTED
    assert rejected.response["details"] == {"supported_assets": [AssetId.TEST_USDC.value]}


def test_disabled_default_is_not_substituted_even_when_configured():
    client = _client(supported={TEST_USDC.address})
    handler = NegotiationHandler.from_erc8183_client_multi(
        erc8183_client=client,
        service_prices={AssetId.TEST_U: "1", AssetId.TEST_USDC: "2"},
    )

    result = handler.negotiate(_request())

    assert result.accepted is False
    assert result.response["details"] == {"supported_assets": [AssetId.TEST_USDC.value]}


def test_successful_refresh_removes_only_newly_disabled_asset():
    enabled = {TEST_USDC.address.lower(), TEST_USDT.address.lower()}
    client = _client(supported={TEST_USDC.address, TEST_USDT.address})
    client.is_payment_token_supported.side_effect = lambda token: token.lower() in enabled
    handler = _multi_handler(client)
    enabled.remove(TEST_USDT.address.lower())

    assert handler.refresh_payment_tokens() == (AssetId.TEST_USDC,)
    assert handler.negotiate(_request(TEST_USDC.address)).accepted is True
    assert handler.negotiate(_request(TEST_USDT.address)).accepted is False


def test_negotiate_auto_refreshes_and_rejects_newly_disabled_asset():
    enabled = {TEST_USDC.address.lower(), TEST_USDT.address.lower()}
    client = _client(supported=set())
    client.is_payment_token_supported.side_effect = lambda token: token.lower() in enabled
    handler = _multi_handler(client)
    enabled.remove(TEST_USDT.address.lower())

    rejected = handler.negotiate(_request(TEST_USDT.address))

    assert rejected.accepted is False
    assert rejected.response["details"] == {"supported_assets": [AssetId.TEST_USDC.value]}
    assert handler.negotiate(_request(TEST_USDC.address)).accepted is True


def test_negotiate_refresh_rpc_failure_returns_safe_dormant_response():
    client = _client(supported={TEST_USDC.address})
    handler = _multi_handler(client)
    client.is_payment_token_supported.side_effect = TimeoutError("rpc://secret-key")

    result = handler.negotiate(_request(TEST_USDC.address))

    assert result.accepted is False
    assert result.response["reason_code"] == ReasonCode.UNSUPPORTED
    assert result.response["details"] == {"supported_assets": []}
    assert "secret" not in str(result.response)


def test_refresh_failure_clears_active_snapshot_fail_closed():
    client = _client(supported={TEST_USDC.address})
    handler = _multi_handler(client)
    client.is_payment_token_supported.side_effect = RuntimeError("rpc secret details")

    with pytest.raises(RuntimeError, match="payment-token refresh failed"):
        handler.refresh_payment_tokens()

    result = handler.negotiate(_request(TEST_USDC.address))
    assert result.accepted is False
    assert result.response["details"] == {"supported_assets": []}
    assert "rpc secret details" not in str(result.response)


def test_omitted_currency_uses_catalog_default_not_commerce_payment_token():
    client = _client(supported={TEST_USDC.address}, default=TEST_USDC.address)
    handler = NegotiationHandler.from_erc8183_client_multi(
        erc8183_client=client,
        service_prices={AssetId.TEST_USDC: "1"},
    )

    omitted = handler.negotiate(_request())
    explicit = handler.negotiate(_request(TEST_USDC.address))

    assert omitted.accepted is False
    assert omitted.response["details"] == {"supported_assets": [AssetId.TEST_USDC.value]}
    assert explicit.accepted is True


def test_explicit_usd1_offer_is_immutable_and_returns_alternatives_instead_of_switching():
    client = MagicMock()
    client.network.chain_id = MAINNET_CHAIN_ID
    client.commerce.address = COMMERCE
    client.is_payment_token_supported.return_value = True
    handler = NegotiationHandler.from_erc8183_client_multi(
        erc8183_client=client,
        service_prices={AssetId.USD1: "100000000000000000"},
    )

    accepted = handler.negotiate(_request(MAINNET_USD1.address.lower()))
    assert accepted.accepted is True
    assert accepted.response["terms"] == {
        "deliverables": "summary",
        "quality_standards": "accurate",
        "evaluation_required": True,
        "evaluator_type": "uma_oov3",
        "price": "100000000000000000",
        "currency": MAINNET_USD1.address,
    }
    for currency in (MAINNET_U.address, "0x0000000000000000000000000000000000000000"):
        rejected = handler.negotiate(_request(currency))
        assert rejected.accepted is False
        assert rejected.response["reason_code"] == ReasonCode.UNSUPPORTED
        assert rejected.response["details"] == {"supported_assets": [AssetId.USD1.value]}
    omitted = handler.negotiate(_request())
    assert omitted.accepted is False
    assert omitted.response["details"] == {"supported_assets": [AssetId.USD1.value]}


def test_multi_constructor_fails_closed_without_exactly_one_catalog_default():
    client = _client(supported={TEST_USDC.address})
    with patch(
        "bnbagent.erc8183.negotiation.list_assets",
        return_value=(TEST_USDC, TEST_USDT),
    ):
        with pytest.raises(ValueError, match="exactly one default"):
            NegotiationHandler.from_erc8183_client_multi(
                erc8183_client=client,
                service_prices={AssetId.TEST_USDC: "1"},
            )


@pytest.mark.parametrize(
    "asset_id",
    ["USDC", AssetId.BINANCE_PEG_USDC, "NOT_AN_ASSET"],
)
def test_multi_config_rejects_symbol_cross_chain_and_unknown_ids(asset_id):
    client = _client(supported={TEST_USDC.address})

    with pytest.raises((KeyError, ValueError)):
        NegotiationHandler.from_erc8183_client_multi(
            erc8183_client=client,
            service_prices={asset_id: "1"},
        )


def test_multi_config_accepts_zero_atomic_price():
    client = _client(supported={TEST_USDC.address})
    handler = NegotiationHandler.from_erc8183_client_multi(
        erc8183_client=client,
        service_prices={AssetId.TEST_USDC: "0"},
    )

    result = handler.negotiate(_request(TEST_USDC.address))

    assert result.accepted is True
    assert result.response["terms"]["price"] == "0"


def test_multi_config_accepts_integer_zero_as_canonical_wire_string():
    client = _client(supported={TEST_USDC.address})
    handler = NegotiationHandler.from_erc8183_client_multi(
        erc8183_client=client,
        service_prices={AssetId.TEST_USDC: 0},
    )

    result = handler.negotiate(_request(TEST_USDC.address))

    assert result.accepted is True
    assert result.response["terms"]["price"] == "0"


@pytest.mark.parametrize("price", ["00", "01", "1.0", -1, True])
def test_multi_config_rejects_noncanonical_atomic_price(price):
    client = _client(supported={TEST_USDC.address})

    with pytest.raises(ValueError, match="non-negative integer"):
        NegotiationHandler.from_erc8183_client_multi(
            erc8183_client=client,
            service_prices={AssetId.TEST_USDC: price},
        )


def test_multi_handler_rejects_single_price_override():
    handler = _multi_handler(_client(supported={TEST_USDC.address}))

    result = handler.negotiate(_request(TEST_USDC.address), price="1")

    assert result.accepted is False
    assert result.response["reason_code"] == ReasonCode.AMBIGUOUS_TERMS


def test_legacy_integer_zero_constructor_and_override_use_canonical_string():
    handler = NegotiationHandler(service_price=0, currency=TEST_U.address)

    default_result = handler.negotiate(_request())
    override_result = handler.negotiate(_request(), price=0)

    assert default_result.response["terms"]["price"] == "0"
    assert override_result.response["terms"]["price"] == "0"


@pytest.mark.parametrize("price", [True, -1, "00", "01"])
def test_legacy_constructor_rejects_invalid_atomic_price(price):
    with pytest.raises(ValueError, match="non-negative integer"):
        NegotiationHandler(service_price=price, currency=TEST_U.address)


def test_legacy_single_currency_preserves_default_and_rejects_other_explicit_token():
    handler = NegotiationHandler(service_price="7", currency=TEST_U.address)

    assert handler.negotiate(_request()).accepted is True
    assert handler.negotiate(_request(TEST_U.address)).accepted is True
    rejected = handler.negotiate(_request(TEST_USDC.address))
    assert rejected.accepted is False
    assert rejected.response["reason_code"] == ReasonCode.UNSUPPORTED
    assert rejected.response["details"] == {"supported_assets": []}
