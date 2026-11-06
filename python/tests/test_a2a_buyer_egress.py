import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest


def load_buyer():
    path = Path(__file__).parents[1] / "examples/a2a-agent/scripts/buyer.py"
    spec = importlib.util.spec_from_file_location("private_a2a_buyer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_registry_card_cannot_redirect_to_internal_origin(monkeypatch):
    buyer = load_buyer()
    monkeypatch.setenv("AGENT_ID", "1")
    monkeypatch.setattr(
        buyer, "fetch_public_json", lambda *a, **k: {"url": "http://169.254.169.254/"}
    )
    with pytest.raises(ValueError, match="origin"):
        buyer.resolve_message_url("https://agent.example")


def test_registry_skill_post_refuses_private_socket(monkeypatch):
    buyer = load_buyer()
    monkeypatch.setenv("AGENT_ID", "1")
    with patch("bnbagent.utils.public_http.socket.socket") as socket:
        with pytest.raises(ValueError):
            buyer.send_skill("http://127.0.0.1/", {"skill": "status"})
    socket.assert_not_called()
