"""Tests for create_apex_state startup validation — APEX_AGENT_URL requirement."""

from unittest.mock import MagicMock

import pytest

from bnbagent.apex.config import APEXConfig
from bnbagent.apex.server.routes import create_apex_state
from bnbagent.storage_providers.local_provider import LocalStorageProvider


def _mock_wallet():
    wp = MagicMock()
    wp.address = "0x" + "aa" * 20
    return wp


def _config(storage, agent_url=None):
    return APEXConfig(
        wallet_provider=_mock_wallet(),
        storage=storage,
        agent_url=agent_url,
    )


class TestCreateApexStateStartupValidation:
    def test_local_storage_without_agent_url_raises(self, monkeypatch):
        monkeypatch.setattr(
            "bnbagent.apex.server.routes.APEXJobOps.apex_client",
            property(lambda self: MagicMock(
                payment_token="0x" + "00" * 20,
                token_decimals=MagicMock(return_value=18),
            )),
            raising=False,
        )
        config = _config(LocalStorageProvider(".agent-data"), agent_url=None)
        with pytest.raises(ValueError, match="APEX_AGENT_URL"):
            create_apex_state(config)

    def test_local_storage_with_agent_url_succeeds(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "bnbagent.apex.server.routes.APEXJobOps",
            _FakeJobOps,
        )
        config = _config(
            LocalStorageProvider(str(tmp_path)),
            agent_url="http://localhost:8003/apex",
        )
        state = create_apex_state(config)
        assert state is not None

    def test_custom_storage_without_agent_url_succeeds(self, monkeypatch):
        monkeypatch.setattr(
            "bnbagent.apex.server.routes.APEXJobOps",
            _FakeJobOps,
        )
        mock_storage = MagicMock(spec=[])  # not a LocalStorageProvider
        config = _config(mock_storage, agent_url=None)
        state = create_apex_state(config)
        assert state is not None


class _FakeJobOps:
    """Minimal APEXJobOps stand-in that skips RPC calls."""

    def __init__(self, wallet_provider, network=None, **kwargs):
        self.agent_address = wallet_provider.address

    @property
    def apex_client(self):
        client = MagicMock()
        client.payment_token = "0x" + "00" * 20
        client.token_decimals = MagicMock(return_value=18)
        return client
