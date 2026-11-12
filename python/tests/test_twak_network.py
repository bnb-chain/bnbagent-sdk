import pytest


def test_twak_expected_network_is_checked_before_confirmation():
    from bnbagent.x402 import X402PolicyError
    from tests.test_twak_x402_payer import URL, _payer, _stub_provider

    provider = _stub_provider()
    payer = _payer(provider, expected_network="eip155:8453")
    with pytest.raises(X402PolicyError, match="network"):
        payer.request(URL, max_payment=10**20)
    provider.x402_request.assert_not_called()
