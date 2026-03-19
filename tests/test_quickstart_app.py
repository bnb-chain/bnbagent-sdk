"""Tests for APEX app factory."""

from unittest.mock import MagicMock, patch

import pytest

from bnbagent.apex.config import APEXConfig
from bnbagent.apex.server.routes import (
    APEXState,
    create_apex_app,
    create_apex_routes,
    create_apex_state,
)
from bnbagent.storage.local_provider import LocalStorageProvider

VALID_CONFIG = APEXConfig(
    rpc_url="https://fake-rpc.example.com",
    erc8183_address="0x" + "ab" * 20,
    private_key="0x" + "cd" * 32,
    wallet_password="test-pw",
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
            wallet_password="test-pw",
            storage=LocalStorageProvider(str(tmp_path / "data")),
        )
        state = create_apex_state(config)
        assert isinstance(state, APEXState)
        assert state.config is config
        assert state.job_ops is not None
        assert state.negotiation_handler is not None

    def test_ipfs_storage(self, patched_web3):
        mock_storage = MagicMock()
        config = APEXConfig(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key="0x" + "cd" * 32,
            wallet_password="test-pw",
            storage=mock_storage,
        )
        state = create_apex_state(config)
        assert isinstance(state, APEXState)

    def test_repr_hides_sensitive(self, patched_web3, tmp_path):
        config = APEXConfig(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key="0x" + "cd" * 32,
            wallet_password="test-pw",
            storage=LocalStorageProvider(str(tmp_path / "data")),
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
            wallet_password="test-pw",
            storage=LocalStorageProvider(str(tmp_path / "data")),
        )
        router = create_apex_routes(config=config)
        assert isinstance(router, APIRouter)

    def test_all_endpoints_exist(self, patched_web3, tmp_path):
        config = APEXConfig(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key="0x" + "cd" * 32,
            wallet_password="test-pw",
            storage=LocalStorageProvider(str(tmp_path / "data")),
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
            wallet_password="test-pw",
            storage=LocalStorageProvider(str(tmp_path / "data")),
        )
        callback = MagicMock()
        router = create_apex_routes(config=config, on_submit=callback)
        assert router is not None

    def test_accepts_pre_created_state(self, patched_web3, tmp_path):
        config = APEXConfig(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key="0x" + "cd" * 32,
            wallet_password="test-pw",
            storage=LocalStorageProvider(str(tmp_path / "data")),
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
            wallet_password="test-pw",
            storage=LocalStorageProvider(str(tmp_path / "data")),
        )
        app = create_apex_app(config=config)
        assert isinstance(app, FastAPI)

    def test_health_endpoint(self, patched_web3, tmp_path):
        config = APEXConfig(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key="0x" + "cd" * 32,
            wallet_password="test-pw",
            storage=LocalStorageProvider(str(tmp_path / "data")),
        )
        app = create_apex_app(config=config)
        routes = [r.path for r in app.routes]
        assert "/health" in routes

    def test_fixed_apex_prefix(self, patched_web3, tmp_path):
        config = APEXConfig(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key="0x" + "cd" * 32,
            wallet_password="test-pw",
            storage=LocalStorageProvider(str(tmp_path / "data")),
        )
        app = create_apex_app(config=config)
        routes = [r.path for r in app.routes]
        assert any("/apex/submit" in r for r in routes)

    def test_middleware_enabled_by_default(self, patched_web3, tmp_path):
        from bnbagent.apex.server.middleware import APEXMiddleware

        config = APEXConfig(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key="0x" + "cd" * 32,
            wallet_password="test-pw",
            storage=LocalStorageProvider(str(tmp_path / "data")),
        )
        app = create_apex_app(config=config)
        # Starlette stores middleware in user_middleware list before build,
        # but after build the middleware stack is wrapped. Check that
        # the middleware class is referenced in the app's middleware stack.
        has_middleware = any(
            m.cls is APEXMiddleware for m in app.user_middleware
        )
        assert has_middleware

    def test_middleware_disabled(self, patched_web3, tmp_path):
        from bnbagent.apex.server.middleware import APEXMiddleware

        config = APEXConfig(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key="0x" + "cd" * 32,
            wallet_password="test-pw",
            storage=LocalStorageProvider(str(tmp_path / "data")),
        )
        app = create_apex_app(config=config, middleware=False)
        has_middleware = any(
            m.cls is APEXMiddleware for m in app.user_middleware
        )
        assert not has_middleware

    def test_middleware_includes_prefixed_skip_paths(self, patched_web3, tmp_path):
        from bnbagent.apex.server.middleware import APEXMiddleware

        config = APEXConfig(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key="0x" + "cd" * 32,
            wallet_password="test-pw",
            storage=LocalStorageProvider(str(tmp_path / "data")),
        )
        app = create_apex_app(config=config)
        mw_entry = next(m for m in app.user_middleware if m.cls is APEXMiddleware)
        skip = mw_entry.kwargs.get("skip_paths", [])
        # Both /negotiate and /apex/negotiate should be in skip paths
        assert "/negotiate" in skip
        assert "/apex/negotiate" in skip

    def test_custom_skip_paths(self, patched_web3, tmp_path):
        from bnbagent.apex.server.middleware import APEXMiddleware

        config = APEXConfig(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key="0x" + "cd" * 32,
            wallet_password="test-pw",
            storage=LocalStorageProvider(str(tmp_path / "data")),
        )
        app = create_apex_app(config=config, skip_paths=["/my-public"])
        mw_entry = next(m for m in app.user_middleware if m.cls is APEXMiddleware)
        skip = mw_entry.kwargs.get("skip_paths", [])
        assert "/my-public" in skip

    def test_status_endpoint_includes_pricing(self, patched_web3, tmp_path):
        from fastapi.testclient import TestClient

        config = APEXConfig(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key="0x" + "cd" * 32,
            wallet_password="test-pw",
            service_price="20000000000000000000",
            payment_token_decimals=18,
            storage=LocalStorageProvider(str(tmp_path / "data")),
        )
        app = create_apex_app(config=config, middleware=False)
        client = TestClient(app)
        resp = client.get("/apex/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["service_price"] == "20000000000000000000"
        assert data["decimals"] == 18
        assert "currency" in data

    def test_on_job_skipped_parameter_accepted(self, patched_web3, tmp_path):
        config = APEXConfig(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key="0x" + "cd" * 32,
            wallet_password="test-pw",
            storage=LocalStorageProvider(str(tmp_path / "data")),
        )
        callback = MagicMock()
        app = create_apex_app(
            config=config,
            on_job=lambda job: "result",
            on_job_skipped=callback,
        )
        assert app is not None
