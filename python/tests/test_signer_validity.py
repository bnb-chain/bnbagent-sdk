from types import SimpleNamespace


from unittest.mock import MagicMock


import pytest


@pytest.mark.parametrize("validity", ["expired", "unbounded", "inverted", "missing"])
def test_structural_signer_never_sees_invalid_payment_window(validity):
    import time

    from bnbagent.x402 import X402PolicyError, X402Signer
    from tests.test_x402_signer import _payload

    wallet = SimpleNamespace(address="0x" + "aa" * 20, sign_typed_data=MagicMock())
    signer = X402Signer(wallet)
    payload = _payload(from_addr=wallet.address)
    now = int(time.time())
    if validity == "expired":
        payload["message"].update(validAfter=now - 120, validBefore=now - 60)
    elif validity == "unbounded":
        payload["message"].update(validAfter=0, validBefore=2**256 - 1)
    elif validity == "inverted":
        payload["message"].update(validAfter=now + 60, validBefore=now)
    else:
        payload["message"].pop("validBefore")
    with pytest.raises(X402PolicyError):
        signer.sign_payment(**payload, expected_to=payload["message"]["to"])
    wallet.sign_typed_data.assert_not_called()
