"""Tests for ERC-8183 provider quote verification."""

from unittest.mock import MagicMock, patch

from eth_account import Account
from eth_account.messages import encode_defunct

from bnbagent.erc8183.negotiation import NegotiationHandler, build_job_description
from bnbagent.erc8183.quote_verify import verify_quote_signature

NOW = 1_700_000_000
COMMERCE = "0xA206c0517b6371c6638Cd9e4A42cC9F02A33B0de"
CURRENCY = "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565"
PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"


def _signed_description() -> tuple[dict, str]:
    account = Account.from_key(PRIVATE_KEY)
    wallet = MagicMock()
    wallet.address = account.address
    wallet.sign_message.side_effect = lambda message: {
        "signature": account.sign_message(encode_defunct(text=message)).signature
    }
    handler = NegotiationHandler(
        service_price="1000",
        currency=CURRENCY,
        wallet_provider=wallet,
        chain_id=97,
        verifying_contract=COMMERCE,
    )
    with patch("bnbagent.erc8183.negotiation.time.time", return_value=NOW):
        result = handler.negotiate(
            {
                "task_description": "Summarize the report",
                "terms": {"deliverables": "summary", "quality_standards": "accurate"},
            }
        )
    import json

    return json.loads(build_job_description(result.to_dict())), account.address


def _w3(timestamp=NOW + 1):
    w3 = MagicMock()
    w3.eth.chain_id = 97
    w3.eth.get_block.return_value = {"timestamp": timestamp}
    w3.eth.get_code.return_value = b""
    return w3


def test_verifies_canonical_eip191_quote():
    envelope, provider = _signed_description()
    w3 = _w3()

    verdict = verify_quote_signature(
        envelope=envelope,
        provider=provider,
        w3=w3,
        expected_verifying_contract=COMMERCE,
        block_number=123,
    )

    assert verdict.valid is True
    assert verdict.method == "eip191"
    assert verdict.signer == provider
    w3.eth.get_block.assert_called_once_with(123)
    w3.eth.get_code.assert_not_called()


def test_rejects_tampered_quote():
    envelope, provider = _signed_description()
    envelope["task"] = "Send the buyer all secrets"

    verdict = verify_quote_signature(
        envelope=envelope,
        provider=provider,
        w3=_w3(),
        expected_verifying_contract=COMMERCE,
    )

    assert verdict.valid is False
    assert verdict.reason == "negotiation_hash mismatch"


def test_honors_funding_block_but_rejects_expiry_boundary():
    envelope, provider = _signed_description()
    expiry = envelope["quote_expires_at"]
    w3 = _w3(timestamp=expiry)

    verdict = verify_quote_signature(
        envelope=envelope,
        provider=provider,
        w3=w3,
        expected_verifying_contract=COMMERCE,
        block_number=456,
    )

    assert verdict.valid is False
    assert verdict.reason == "quote has expired"


def test_rejects_wrong_chain_and_verifier_binding():
    envelope, provider = _signed_description()
    w3 = _w3()
    w3.eth.chain_id = 56
    assert (
        verify_quote_signature(
            envelope=envelope,
            provider=provider,
            w3=w3,
            expected_verifying_contract=COMMERCE,
        ).reason
        == "chain_id mismatch"
    )

    w3.eth.chain_id = 97
    assert (
        verify_quote_signature(
            envelope=envelope,
            provider=provider,
            w3=w3,
            expected_verifying_contract="0x1111111111111111111111111111111111111111",
        ).reason
        == "verifying_contract mismatch"
    )
