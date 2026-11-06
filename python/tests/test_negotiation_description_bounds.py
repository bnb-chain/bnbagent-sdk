from unittest.mock import MagicMock

import pytest

from bnbagent.erc8183.commerce import CommerceClient
from bnbagent.erc8183.negotiation import NegotiationHandler
from bnbagent.erc8183.schema import JobDescription
from tests.test_erc8183_job_ops import COMMERCE, ME


@pytest.mark.parametrize("text", ['{"task":"' + "x" * 4096 + '"}', "界" * 1400])
def test_description_limit_is_utf8_bytes_before_parsing(text):
    with pytest.raises(ValueError, match="4096-byte"):
        JobDescription.from_str(text)


def test_oversized_negotiation_never_reaches_the_wallet_signer():
    wallet = MagicMock()
    handler = NegotiationHandler(
        service_price="1",
        currency=ME,
        wallet_provider=wallet,
        chain_id=97,
        verifying_contract=COMMERCE,
    )
    result = handler.negotiate(
        {
            "task_description": "x" * 5000,
            "terms": {"deliverables": "report", "quality_standards": "good"},
        }
    )
    assert result.to_dict()["response"]["accepted"] is False
    wallet.sign_message.assert_not_called()


def test_direct_commerce_rejects_utf8_oversize_before_contract_call():
    client = object.__new__(CommerceClient)
    client.contract = MagicMock()
    with pytest.raises(ValueError, match="4096-byte"):
        client.create_job(
            provider=ME, evaluator=ME, expired_at=9999999999, description="界" * 1400
        )
    client.contract.functions.createJob.assert_not_called()
