"""Tests for APEX app factory and APEX extension class."""

from unittest.mock import MagicMock, patch

import pytest

from bnbagent.apex.config import APEXConfig
from bnbagent.apex.server.routes import (
    APEX,
    APEXState,
    create_apex_app,
    create_apex_routes,
    create_apex_state,
)
from bnbagent.apex.server.middleware import APEXMiddleware, DEFAULT_SKIP_PATHS
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
        assert "/job/{job_id}/response" in paths
        assert "/job/{job_id}/verify" in paths
        assert "/negotiate" in paths
        assert "/status" in paths
        assert "/health" in paths

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
        assert "/apex/health" in routes

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

    def test_get_response_endpoint_success(self, patched_web3, tmp_path):
        """GET /apex/job/{id}/response returns stored deliverable data."""
        import json
        from fastapi.testclient import TestClient

        storage_dir = tmp_path / "data"
        storage_dir.mkdir()
        # Write a job file that LocalStorageProvider can find
        job_file = storage_dir / "job-42.json"
        job_file.write_text(json.dumps({
            "response": "hello from agent",
            "job": {"id": 42},
            "metadata": {"timestamps": {"submitted_at": 1700000000}},
        }))

        config = APEXConfig(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key="0x" + "cd" * 32,
            wallet_password="test-pw",
            storage=LocalStorageProvider(str(storage_dir)),
        )
        app = create_apex_app(config=config, middleware=False)
        client = TestClient(app)

        resp = client.get("/apex/job/42/response")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["response"] == "hello from agent"

    def test_get_response_endpoint_not_found(self, patched_web3, tmp_path):
        """GET /apex/job/{id}/response returns 404 when no response exists."""
        from fastapi.testclient import TestClient

        config = APEXConfig(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key="0x" + "cd" * 32,
            wallet_password="test-pw",
            storage=LocalStorageProvider(str(tmp_path / "data")),
        )
        app = create_apex_app(config=config, middleware=False)
        client = TestClient(app)

        resp = client.get("/apex/job/999/response")
        assert resp.status_code == 404
        data = resp.json()
        assert data["success"] is False

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


class TestAPEX:
    """Tests for the APEX extension class."""

    def _make_config(self, tmp_path):
        return APEXConfig(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key="0x" + "cd" * 32,
            wallet_password="test-pw",
            storage=LocalStorageProvider(str(tmp_path / "data")),
        )

    def test_init_app_registers_routes(self, patched_web3, tmp_path):
        from fastapi import FastAPI

        apex = APEX(config=self._make_config(tmp_path))
        app = FastAPI()
        apex.init_app(app, prefix="/apex")

        routes = [r.path for r in app.routes]
        assert "/apex/submit" in routes
        assert "/apex/job/{job_id}" in routes
        assert "/apex/job/{job_id}/response" in routes
        assert "/apex/job/{job_id}/verify" in routes
        assert "/apex/negotiate" in routes
        assert "/apex/status" in routes
        assert "/apex/health" in routes

    def test_init_app_adds_middleware(self, patched_web3, tmp_path):
        from fastapi import FastAPI

        from bnbagent.apex.server.middleware import APEXMiddleware

        apex = APEX(config=self._make_config(tmp_path))
        app = FastAPI()
        apex.init_app(app)

        has_middleware = any(m.cls is APEXMiddleware for m in app.user_middleware)
        assert has_middleware

    def test_init_app_without_middleware(self, patched_web3, tmp_path):
        from fastapi import FastAPI

        from bnbagent.apex.server.middleware import APEXMiddleware

        apex = APEX(config=self._make_config(tmp_path), middleware=False)
        app = FastAPI()
        apex.init_app(app)

        has_middleware = any(m.cls is APEXMiddleware for m in app.user_middleware)
        assert not has_middleware

    def test_init_app_wraps_lifespan_with_on_job(self, patched_web3, tmp_path):
        from fastapi import FastAPI

        apex = APEX(config=self._make_config(tmp_path), on_job=lambda job: "result")
        app = FastAPI()
        original_lifespan = app.router.lifespan_context
        apex.init_app(app)

        # lifespan should be wrapped (different from original)
        assert app.router.lifespan_context is not original_lifespan

    def test_init_app_no_job_loop_without_on_job(self, patched_web3, tmp_path):
        from fastapi import FastAPI

        apex = APEX(config=self._make_config(tmp_path))
        app = FastAPI()
        apex.init_app(app)

        # Without on_job, no job loop task should be created
        assert apex._job_loop_task is None

    def test_init_app_wraps_custom_lifespan(self, patched_web3, tmp_path):
        from contextlib import asynccontextmanager

        from fastapi import FastAPI

        startup_called = []

        @asynccontextmanager
        async def custom_lifespan(app):
            startup_called.append("custom")
            yield

        apex = APEX(config=self._make_config(tmp_path), on_job=lambda job: "result")
        app = FastAPI(lifespan=custom_lifespan)
        apex.init_app(app)

        # lifespan should be wrapped around the custom one
        assert app.router.lifespan_context is not custom_lifespan

    def test_state_accessible(self, patched_web3, tmp_path):
        apex = APEX(config=self._make_config(tmp_path))
        assert isinstance(apex.state, APEXState)
        assert apex.job_ops is apex.state.job_ops

    def test_double_init_app_raises(self, patched_web3, tmp_path):
        from fastapi import FastAPI

        apex = APEX(config=self._make_config(tmp_path))
        app = FastAPI()
        apex.init_app(app)

        with pytest.raises(RuntimeError, match="APEX already initialized"):
            apex.init_app(app)

    def test_create_apex_app_still_works(self, patched_web3, tmp_path):
        """Regression: create_apex_app should still work after refactor."""
        from fastapi import FastAPI

        config = self._make_config(tmp_path)
        app = create_apex_app(config=config, on_job=lambda job: "result")
        assert isinstance(app, FastAPI)
        routes = [r.path for r in app.routes]
        assert "/apex/health" in routes
        assert "/" in routes

    def test_custom_prefix(self, patched_web3, tmp_path):
        from fastapi import FastAPI

        apex = APEX(config=self._make_config(tmp_path))
        app = FastAPI()
        apex.init_app(app, prefix="/my-apex")

        routes = [r.path for r in app.routes]
        assert "/my-apex/submit" in routes
        assert "/my-apex/health" in routes


class TestManualRoutes:
    """Tests for the Option 3 pattern: create_apex_routes() with manual middleware + job loop."""

    def _make_config(self, tmp_path):
        return APEXConfig(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key="0x" + "cd" * 32,
            wallet_password="test-pw",
            storage=LocalStorageProvider(str(tmp_path / "data")),
        )

    def test_manual_mount_with_prefix(self, patched_web3, tmp_path):
        """Routes are accessible at the given prefix."""
        from fastapi import FastAPI

        config = self._make_config(tmp_path)
        state = create_apex_state(config)
        app = FastAPI()
        router = create_apex_routes(state=state)
        app.include_router(router, prefix="/apex")

        routes = [r.path for r in app.routes]
        assert "/apex/submit" in routes
        assert "/apex/negotiate" in routes
        assert "/apex/status" in routes
        assert "/apex/health" in routes
        assert "/apex/job/{job_id}" in routes
        assert "/apex/job/{job_id}/response" in routes
        assert "/apex/job/{job_id}/verify" in routes

    def test_manual_middleware_with_prefixed_skip_paths(self, patched_web3, tmp_path):
        """Middleware skip_paths must include prefixed versions for correct behavior."""
        from fastapi import FastAPI

        config = self._make_config(tmp_path)
        state = create_apex_state(config)
        app = FastAPI()
        prefix = "/apex"

        router = create_apex_routes(state=state)
        app.include_router(router, prefix=prefix)

        skip_paths = list(DEFAULT_SKIP_PATHS) + [f"{prefix}{p}" for p in DEFAULT_SKIP_PATHS]
        app.add_middleware(APEXMiddleware, job_ops=state.job_ops, skip_paths=skip_paths)

        has_middleware = any(m.cls is APEXMiddleware for m in app.user_middleware)
        assert has_middleware

        mw_entry = next(m for m in app.user_middleware if m.cls is APEXMiddleware)
        effective_skip = mw_entry.kwargs.get("skip_paths", [])
        # Both bare and prefixed versions must be present
        assert "/negotiate" in effective_skip
        assert "/apex/negotiate" in effective_skip
        assert "/status" in effective_skip
        assert "/apex/status" in effective_skip
        assert "/health" in effective_skip
        assert "/apex/health" in effective_skip

    def test_manual_middleware_without_prefix_misses_paths(self, patched_web3, tmp_path):
        """Without adding prefixed skip_paths, the middleware would block APEX skip paths."""
        from fastapi import FastAPI

        config = self._make_config(tmp_path)
        state = create_apex_state(config)
        app = FastAPI()

        router = create_apex_routes(state=state)
        app.include_router(router, prefix="/apex")

        # Only default skip paths — no prefixed versions
        app.add_middleware(APEXMiddleware, job_ops=state.job_ops)

        mw_entry = next(m for m in app.user_middleware if m.cls is APEXMiddleware)
        effective_skip = mw_entry.kwargs.get("skip_paths", DEFAULT_SKIP_PATHS)
        # /apex/negotiate is NOT in skip paths — this is the footgun
        assert "/apex/negotiate" not in effective_skip

    def test_no_middleware_added_by_default(self, patched_web3, tmp_path):
        """create_apex_routes alone does NOT add middleware."""
        from fastapi import FastAPI

        config = self._make_config(tmp_path)
        state = create_apex_state(config)
        app = FastAPI()

        router = create_apex_routes(state=state)
        app.include_router(router, prefix="/apex")

        has_middleware = any(
            hasattr(m, "cls") and m.cls is APEXMiddleware
            for m in app.user_middleware
        )
        assert not has_middleware

    def test_status_endpoint_works(self, patched_web3, tmp_path):
        """Status endpoint is accessible via the manually mounted router."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        config = self._make_config(tmp_path)
        config.service_price = "5000000000000000000"
        config.payment_token_decimals = 18
        state = create_apex_state(config)

        app = FastAPI()
        router = create_apex_routes(state=state)
        app.include_router(router, prefix="/apex")

        client = TestClient(app)
        resp = client.get("/apex/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["service_price"] == "5000000000000000000"
        assert data["decimals"] == 18

    def test_health_endpoint_works(self, patched_web3, tmp_path):
        """Health endpoint is accessible via the manually mounted router."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        config = self._make_config(tmp_path)
        state = create_apex_state(config)

        app = FastAPI()
        router = create_apex_routes(state=state)
        app.include_router(router, prefix="/apex")

        client = TestClient(app)
        resp = client.get("/apex/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_shared_state_across_router_and_middleware(self, patched_web3, tmp_path):
        """The same state object is shared between routes and middleware."""
        from fastapi import FastAPI

        config = self._make_config(tmp_path)
        state = create_apex_state(config)
        app = FastAPI()

        router = create_apex_routes(state=state)
        app.include_router(router, prefix="/apex")
        app.add_middleware(APEXMiddleware, job_ops=state.job_ops)

        mw_entry = next(m for m in app.user_middleware if m.cls is APEXMiddleware)
        # The job_ops passed to middleware is the same as state.job_ops
        assert mw_entry.kwargs["job_ops"] is state.job_ops
