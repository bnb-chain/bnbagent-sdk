"""Tests for APEX app factory and sub-app architecture."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bnbagent.apex.config import APEXConfig
from bnbagent.apex.server import APEXState, create_apex_app, create_apex_state
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


class TestCreateApexApp:
    """Tests for create_apex_app() — standalone and mounted modes."""

    def _make_config(self, tmp_path):
        return APEXConfig(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key="0x" + "cd" * 32,
            wallet_password="test-pw",
            storage=LocalStorageProvider(str(tmp_path / "data")),
        )

    # ── Standalone mode (default prefix="/apex") ─────────────────────────

    def test_returns_fastapi(self, patched_web3, tmp_path):
        from fastapi import FastAPI

        app = create_apex_app(config=self._make_config(tmp_path))
        assert isinstance(app, FastAPI)

    def test_standalone_routes_at_apex_prefix(self, patched_web3, tmp_path):
        """Default standalone mode has routes at /apex/*."""
        app = create_apex_app(config=self._make_config(tmp_path))
        paths = [r.path for r in app.routes]
        assert "/apex/submit" in paths
        assert "/apex/health" in paths
        assert "/apex/status" in paths

    def test_standalone_has_root_endpoint(self, patched_web3, tmp_path):
        """Standalone mode includes a root / endpoint."""
        from fastapi.testclient import TestClient

        app = create_apex_app(config=self._make_config(tmp_path))
        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["service"] == "APEX Agent"
        assert "endpoints" in data

    def test_standalone_health(self, patched_web3, tmp_path):
        from fastapi.testclient import TestClient

        app = create_apex_app(config=self._make_config(tmp_path))
        client = TestClient(app)
        resp = client.get("/apex/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_standalone_status_includes_pricing(self, patched_web3, tmp_path):
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
        app = create_apex_app(config=config)
        client = TestClient(app)
        resp = client.get("/apex/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["service_price"] == "20000000000000000000"
        assert data["decimals"] == 18
        assert "currency" in data

    def test_standalone_get_response_success(self, patched_web3, tmp_path):
        """GET /apex/job/{id}/response returns stored deliverable data."""
        import json
        from fastapi.testclient import TestClient

        storage_dir = tmp_path / "data"
        storage_dir.mkdir()
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
        app = create_apex_app(config=config)
        client = TestClient(app)

        resp = client.get("/apex/job/42/response")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["response"] == "hello from agent"

    def test_standalone_get_response_not_found(self, patched_web3, tmp_path):
        from fastapi.testclient import TestClient

        app = create_apex_app(config=self._make_config(tmp_path))
        client = TestClient(app)

        resp = client.get("/apex/job/999/response")
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    # ── Mounted mode (prefix="") ─────────────────────────────────────────

    def test_mounted_routes_at_mount_path(self, patched_web3, tmp_path):
        """When mounted with prefix='', routes respond at the mount path."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        parent = FastAPI()
        apex_app = create_apex_app(
            config=self._make_config(tmp_path), prefix=""
        )
        parent.mount("/apex", apex_app)

        client = TestClient(parent)
        resp = client.get("/apex/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        resp = client.get("/apex/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_mounted_no_root_endpoint(self, patched_web3, tmp_path):
        """Mounted mode (prefix='') does not add a root / endpoint."""
        app = create_apex_app(
            config=self._make_config(tmp_path), prefix=""
        )
        paths = [r.path for r in app.routes]
        assert "/" not in paths

    # ── Common behavior ──────────────────────────────────────────────────

    def test_state_accessible_via_app_state(self, patched_web3, tmp_path):
        app = create_apex_app(config=self._make_config(tmp_path))
        assert hasattr(app.state, "apex")
        assert isinstance(app.state.apex, APEXState)
        assert app.state.apex.job_ops is not None

    def test_on_job_skipped_parameter_accepted(self, patched_web3, tmp_path):
        callback = MagicMock()
        app = create_apex_app(
            config=self._make_config(tmp_path),
            on_job=lambda job: "result",
            on_job_skipped=callback,
        )
        assert app is not None

    def test_has_lifespan_with_on_job(self, patched_web3, tmp_path):
        app = create_apex_app(
            config=self._make_config(tmp_path),
            on_job=lambda job: "result",
        )
        assert app.router.lifespan_context is not None

    # ── /job/execute endpoint ────────────────────────────────────────────────

    def test_process_endpoint_exists_with_on_job(self, patched_web3, tmp_path):
        app = create_apex_app(
            config=self._make_config(tmp_path),
            on_job=lambda job: "result",
        )
        paths = [r.path for r in app.routes]
        assert "/apex/job/execute" in paths

    def test_process_endpoint_absent_without_on_job(self, patched_web3, tmp_path):
        app = create_apex_app(config=self._make_config(tmp_path))
        paths = [r.path for r in app.routes]
        assert "/apex/job/execute" not in paths
        assert "/job/execute" not in paths

    def test_process_endpoint_requires_job_id(self, patched_web3, tmp_path):
        from fastapi.testclient import TestClient

        app = create_apex_app(
            config=self._make_config(tmp_path),
            on_job=lambda job: "result",
        )
        client = TestClient(app)
        resp = client.post("/apex/job/execute", json={})
        assert resp.status_code == 400
        assert "job_id" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_process_endpoint_rejects_invalid_job(self, patched_web3, tmp_path):
        from fastapi.testclient import TestClient

        app = create_apex_app(
            config=self._make_config(tmp_path),
            on_job=lambda job: "result",
        )
        client = TestClient(app)

        with patch.object(
            app.state.apex.job_ops, "verify_job", new_callable=AsyncMock
        ) as mock_verify:
            mock_verify.return_value = {"valid": False, "error": "not funded"}
            resp = client.post("/apex/job/execute", json={"job_id": 42})
            assert resp.status_code == 400

    def test_process_endpoint_success_includes_response_content(
        self, patched_web3, tmp_path
    ):
        """Successful /job/execute response includes response_content."""
        from fastapi.testclient import TestClient

        app = create_apex_app(
            config=self._make_config(tmp_path),
            on_job=lambda job: "my agent output",
        )
        client = TestClient(app)

        mock_job = {
            "jobId": 42,
            "description": "test",
            "budget": 10**18,
            "client": "0x" + "11" * 20,
            "provider": app.state.apex.job_ops.agent_address,
            "evaluator": "0x" + "33" * 20,
            "status": "FUNDED",
            "expiredAt": 9999999999,
        }

        with (
            patch.object(
                app.state.apex.job_ops,
                "verify_job",
                new_callable=AsyncMock,
                return_value={"valid": True, "job": mock_job},
            ),
            patch.object(
                app.state.apex.job_ops,
                "submit_result",
                new_callable=AsyncMock,
                return_value={
                    "success": True,
                    "txHash": "0xabc",
                    "dataUrl": "ipfs://Qm...",
                    "deliverableHash": "0xdef",
                },
            ),
        ):
            resp = client.post("/apex/job/execute", json={"job_id": 42})
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
            assert data["response_content"] == "my agent output"

    def test_process_endpoint_mounted_mode(self, patched_web3, tmp_path):
        """With prefix='', /job/execute is at root level for mounting."""
        app = create_apex_app(
            config=self._make_config(tmp_path),
            on_job=lambda job: "result",
            prefix="",
        )
        paths = [r.path for r in app.routes]
        assert "/job/execute" in paths
