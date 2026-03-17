"""Tests for APEX app factory."""

from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from bnbagent.apex.config import APEXConfig
from bnbagent.apex.server.routes import (
    APEXState,
    create_apex_state,
    create_apex_routes,
    create_apex_app,
)

VALID_CONFIG = APEXConfig(
    rpc_url="https://fake-rpc.example.com",
    erc8183_address="0x" + "ab" * 20,
    private_key="0x" + "cd" * 32,
)


@pytest.fixture
def patched_web3():
    """Patch Web3 to avoid real RPC connections."""
    with patch("bnbagent.apex.server.job_ops.Web3") as mock_web3_cls:
        mock_w3 = MagicMock()
        mock_w3.provider.endpoint_uri = "https://fake-rpc.example.com"
        mock_w3.eth.get_transaction_count.return_value = 0
        account_mock = MagicMock()
        account_mock.address = "0x" + "ff" * 20
        mock_w3.eth.account.from_key.return_value = account_mock
        mock_w3.middleware_onion = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider.return_value = MagicMock()
        yield mock_web3_cls


class TestCreateApexState:
    def test_local_storage(self, patched_web3, tmp_path):
        config = APEXConfig(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key="0x" + "cd" * 32,
            local_storage_path=str(tmp_path / "data"),
        )
        state = create_apex_state(config)
        assert isinstance(state, APEXState)
        assert state.config is config
        assert state.job_ops is not None
        assert state.negotiation_handler is not None

    def test_ipfs_storage(self, patched_web3):
        config = APEXConfig(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key="0x" + "cd" * 32,
            storage_provider="ipfs",
            pinata_jwt="test-jwt",
        )
        state = create_apex_state(config)
        assert isinstance(state, APEXState)

    def test_repr_hides_sensitive(self, patched_web3, tmp_path):
        config = APEXConfig(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key="0x" + "cd" * 32,
            local_storage_path=str(tmp_path / "data"),
        )
        state = create_apex_state(config)
        r = repr(state)
        assert "0x" + "cd" * 32 not in r


class TestCreateApexRoutes:
    def test_returns_api_router(self, patched_web3, tmp_path):
        from fastapi import APIRouter
        config = APEXConfig(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key="0x" + "cd" * 32,
            local_storage_path=str(tmp_path / "data"),
        )
        router = create_apex_routes(config=config)
        assert isinstance(router, APIRouter)

    def test_all_endpoints_exist(self, patched_web3, tmp_path):
        config = APEXConfig(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key="0x" + "cd" * 32,
            local_storage_path=str(tmp_path / "data"),
        )
        router = create_apex_routes(config=config)
        paths = [route.path for route in router.routes]
        assert "/submit" in paths
        assert "/job/{job_id}" in paths
        assert "/job/{job_id}/verify" in paths
        assert "/negotiate" in paths
        assert "/status" in paths

    def test_on_submit_callback(self, patched_web3, tmp_path):
        config = APEXConfig(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key="0x" + "cd" * 32,
            local_storage_path=str(tmp_path / "data"),
        )
        callback = MagicMock()
        router = create_apex_routes(config=config, on_submit=callback)
        assert router is not None

    def test_accepts_pre_created_state(self, patched_web3, tmp_path):
        config = APEXConfig(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key="0x" + "cd" * 32,
            local_storage_path=str(tmp_path / "data"),
        )
        state = create_apex_state(config)
        router = create_apex_routes(state=state)
        assert router is not None


class TestCreateApexApp:
    def test_returns_fastapi(self, patched_web3, tmp_path):
        from fastapi import FastAPI
        config = APEXConfig(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key="0x" + "cd" * 32,
            local_storage_path=str(tmp_path / "data"),
        )
        app = create_apex_app(config=config)
        assert isinstance(app, FastAPI)

    def test_health_endpoint(self, patched_web3, tmp_path):
        config = APEXConfig(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key="0x" + "cd" * 32,
            local_storage_path=str(tmp_path / "data"),
        )
        app = create_apex_app(config=config)
        routes = [r.path for r in app.routes]
        assert "/health" in routes

    def test_custom_prefix(self, patched_web3, tmp_path):
        config = APEXConfig(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key="0x" + "cd" * 32,
            local_storage_path=str(tmp_path / "data"),
        )
        app = create_apex_app(config=config, prefix="/apex")
        routes = [r.path for r in app.routes]
        assert any("/apex/submit" in r for r in routes)
