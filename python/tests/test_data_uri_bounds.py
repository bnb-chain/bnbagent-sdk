import base64

from bnbagent.erc8004.agent import ERC8004Agent


def test_data_uri_is_bounded_before_decode_and_parse():
    data = b'{"name":"' + b"x" * (1024 * 1024) + b'"}'
    assert (
        ERC8004Agent.parse_agent_uri(
            "data:application/json;base64," + base64.b64encode(data).decode()
        )
        is None
    )
    assert ERC8004Agent.parse_agent_uri(
        "data:application/json;base64," + base64.b64encode(b'{"name":"ok"}').decode()
    ) == {"name": "ok"}


def test_data_uri_requires_an_object():
    assert (
        ERC8004Agent.parse_agent_uri(
            "data:application/json;base64," + base64.b64encode(b"[]").decode()
        )
        is None
    )
