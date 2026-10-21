"""Exercise example entry points with real price checks and no chain writes."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import bnbagent

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "0x" + "11" * 20
PROVIDER = "0x" + "22" * 20


def load_example(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "examples" / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "change,accepted",
    [
        ({}, True),
        ({"price": "1001"}, False),
        ({"chain_id": 56}, False),
        ({"currency": "0x" + "33" * 20}, False),
        ({"maximum": ""}, False),
        ({"price": "1e3"}, False),
    ],
)
def test_buyer_authorizes_before_any_write(monkeypatch, change, accepted):
    buyer = load_example("a2a-agent/scripts/buyer.py", "audit_buyer")
    for key, value in {
        "BUYER_PRIVATE_KEY": "test",
        "BUYER_WALLET_PASSWORD": "test",
        "EXPECTED_PROVIDER_ADDRESS": PROVIDER,
        "BUYER_MAX_PRICE": change.get("maximum", "1000"),
        "BUYER_CHAIN_ID": "97",
        "BUYER_PAYMENT_TOKEN": TOKEN,
    }.items():
        monkeypatch.setenv(key, value)
    client = MagicMock()
    client.w3.eth.chain_id = 97
    client.payment_token = TOKEN
    client.verify_negotiation_quote.return_value = SimpleNamespace(valid=True)
    client.create_job.return_value = {"jobId": 1, "transactionHash": "0xtest"}
    monkeypatch.setattr(bnbagent, "ERC8183Client", lambda **_: client)
    monkeypatch.setattr(bnbagent, "EVMWalletProvider", lambda **_: object())
    quote = {
        "chain_id": change.get("chain_id", 97),
        "response": {
            "accepted": True,
            "terms": {
                "price": change.get("price", "1000"),
                "currency": change.get("currency", TOKEN),
            }
        },
    }
    if accepted:
        assert buyer.fund_job(quote) == 1
        client.fund.assert_called_once_with(1, 1000)
    else:
        with pytest.raises(SystemExit):
            buyer.fund_job(quote)
        for name in ("create_job", "register_job", "set_budget", "fund", "approve_payment_token"):
            getattr(client, name).assert_not_called()


@pytest.mark.parametrize(
    "url", ["http://169.254.169.254/latest/meta-data/", "http://127.0.0.1/", "ipfs://../secret"]
)
def test_report_refuses_private_destinations_before_network(monkeypatch, url):
    report = load_example("client/gen_job_report.py", "audit_report")
    import bnbagent.utils.public_http as public_http

    connect = MagicMock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(public_http.socket, "socket", connect)
    client = MagicMock()
    client.get_deliverable_url.return_value = url
    _, body = report._resolve_deliverable(client, 1)
    assert body.startswith("<fetch failed:")
    connect.assert_not_called()
