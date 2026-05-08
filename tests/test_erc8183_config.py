"""Tests for ERC8183Config — configuration management (ERC-8183)."""

from unittest.mock import MagicMock

import pytest

from bnbagent.erc8183.config import ERC8183Config
from bnbagent.config import NetworkConfig

VALID_PK = "0x" + "cd" * 32
VALID_PASSWORD = "test-password"


class TestInit:
    def test_valid_config_with_wallet_password(self):
        config = ERC8183Config(private_key=VALID_PK, wallet_password=VALID_PASSWORD)
        # private_key is cleared after auto-wrap
        assert config.private_key == ""
        assert config.wallet_provider is not None
        assert config.effective_chain_id == 97

    def test_explicit_wallet_provider(self):
        mock_wallet = MagicMock()
        mock_wallet.address = "0x" + "ff" * 20
        config = ERC8183Config(wallet_provider=mock_wallet)
        assert config.wallet_provider is mock_wallet
        assert config.private_key == ""

    def test_explicit_network_config(self):
        custom = NetworkConfig(
            name="custom",
            chain_id=12345,
            rpc_url="https://rpc.example.com",
            commerce_contract="0x" + "ab" * 20,
            router_contract="0x" + "cd" * 20,
            policy_contract="0x" + "ef" * 20,
        )
        config = ERC8183Config(
            network=custom,
            private_key=VALID_PK,
            wallet_password=VALID_PASSWORD,
        )
        assert config.effective_rpc_url == "https://rpc.example.com"
        assert config.effective_chain_id == 12345
        assert config.effective_commerce_address == "0x" + "ab" * 20
        assert config.effective_router_address == "0x" + "cd" * 20
        assert config.effective_policy_address == "0x" + "ef" * 20

    def test_network_config_ignores_env_overrides(self, monkeypatch):
        # Explicit NetworkConfig object must NOT be mutated by env vars.
        monkeypatch.setenv("ERC8183_COMMERCE_ADDRESS", "0x" + "11" * 20)
        custom = NetworkConfig(
            name="custom",
            chain_id=12345,
            rpc_url="https://rpc.example.com",
            commerce_contract="0x" + "ab" * 20,
        )
        config = ERC8183Config(network=custom)
        assert config.effective_commerce_address == "0x" + "ab" * 20

    def test_defaults_come_from_network(self):
        config = ERC8183Config()
        # bsc-testnet default — any non-empty string is fine; this asserts a
        # default exists and is resolved without overrides.
        assert config.effective_commerce_address.startswith("0x")
        assert config.effective_router_address.startswith("0x")
        assert config.effective_policy_address.startswith("0x")

    def test_private_key_without_password_raises(self):
        with pytest.raises(ValueError, match="wallet_password is required"):
            ERC8183Config(private_key=VALID_PK)

    def test_password_only_no_keystore_auto_generates(self, monkeypatch, tmp_path):
        import bnbagent.wallets.evm_wallet_provider as wp

        monkeypatch.setattr(wp, "_WALLETS_DIR", tmp_path / "wallets")
        config = ERC8183Config(wallet_password="test-pw")
        assert config.wallet_provider is not None
        assert config.wallet_provider.source == "created_new"

    def test_no_private_key_no_wallet_ok(self):
        """ERC8183Config without any wallet does not raise (read-only config)."""
        config = ERC8183Config()
        assert config.wallet_provider is None

    def test_normalizes_private_key_and_wraps(self):
        config = ERC8183Config(
            private_key="cd" * 32,  # No 0x prefix
            wallet_password=VALID_PASSWORD,
        )
        assert config.private_key == ""
        assert config.wallet_provider is not None

    def test_private_key_cleared_after_wrap(self):
        config = ERC8183Config(private_key=VALID_PK, wallet_password=VALID_PASSWORD)
        assert config.private_key == ""
        assert VALID_PK not in repr(config)

    def test_warns_when_private_key_env_persists_after_wrap(
        self, monkeypatch, caplog
    ):
        import logging

        monkeypatch.setenv("PRIVATE_KEY", VALID_PK)
        with caplog.at_level(logging.WARNING, logger="bnbagent.core.config"):
            ERC8183Config(private_key=VALID_PK, wallet_password=VALID_PASSWORD)
        assert any(
            "PRIVATE_KEY is still set" in r.message for r in caplog.records
        )

    def test_no_env_warning_when_private_key_unset(self, monkeypatch, caplog):
        import logging

        monkeypatch.delenv("PRIVATE_KEY", raising=False)
        with caplog.at_level(logging.WARNING, logger="bnbagent.core.config"):
            ERC8183Config(private_key=VALID_PK, wallet_password=VALID_PASSWORD)
        assert not any(
            "PRIVATE_KEY is still set" in r.message for r in caplog.records
        )

    def test_repr_with_wallet_provider(self):
        mock_wallet = MagicMock()
        mock_wallet.address = "0x" + "ff" * 20
        config = ERC8183Config(wallet_provider=mock_wallet)
        r = repr(config)
        assert "wallet=" in r
        assert "0xffffffff" in r.lower()

    def test_repr_no_wallet(self):
        config = ERC8183Config()
        r = repr(config)
        assert "wallet=None" in r


class TestFromEnv:
    def test_rpc_url_and_addresses_from_env(self, monkeypatch):
        monkeypatch.setenv("RPC_URL", "https://rpc.example.com")
        monkeypatch.setenv("ERC8183_COMMERCE_ADDRESS", "0x" + "ab" * 20)
        monkeypatch.setenv("ERC8183_ROUTER_ADDRESS", "0x" + "cd" * 20)
        monkeypatch.setenv("ERC8183_POLICY_ADDRESS", "0x" + "ef" * 20)
        monkeypatch.setenv("PRIVATE_KEY", VALID_PK)
        monkeypatch.setenv("WALLET_PASSWORD", VALID_PASSWORD)
        config = ERC8183Config.from_env()
        assert config.effective_rpc_url == "https://rpc.example.com"
        assert config.effective_commerce_address == "0x" + "ab" * 20
        assert config.effective_router_address == "0x" + "cd" * 20
        assert config.effective_policy_address == "0x" + "ef" * 20

    def test_missing_wallet_password_raises(self, monkeypatch):
        monkeypatch.setenv("PRIVATE_KEY", VALID_PK)
        monkeypatch.delenv("WALLET_PASSWORD", raising=False)
        with pytest.raises(ValueError, match="WALLET_PASSWORD is required"):
            ERC8183Config.from_env()

    def test_service_price_from_env(self, monkeypatch):
        monkeypatch.setenv("RPC_URL", "https://rpc.example.com")
        monkeypatch.setenv("PRIVATE_KEY", VALID_PK)
        monkeypatch.setenv("WALLET_PASSWORD", VALID_PASSWORD)
        monkeypatch.setenv("ERC8183_SERVICE_PRICE", "5000000000000000000")
        config = ERC8183Config.from_env()
        assert config.service_price == "5000000000000000000"

    def test_wallet_provider_auto_created(self, monkeypatch):
        monkeypatch.setenv("PRIVATE_KEY", VALID_PK)
        monkeypatch.setenv("WALLET_PASSWORD", VALID_PASSWORD)
        config = ERC8183Config.from_env()
        assert config.wallet_provider is not None
        assert config.private_key == ""

    def test_agent_url_from_env(self, monkeypatch):
        monkeypatch.setenv("PRIVATE_KEY", VALID_PK)
        monkeypatch.setenv("WALLET_PASSWORD", VALID_PASSWORD)
        monkeypatch.setenv("ERC8183_AGENT_URL", "http://localhost:8003/erc8183")
        config = ERC8183Config.from_env()
        assert config.agent_url == "http://localhost:8003/erc8183"

    def test_agent_url_defaults_to_none(self, monkeypatch):
        monkeypatch.setenv("PRIVATE_KEY", VALID_PK)
        monkeypatch.setenv("WALLET_PASSWORD", VALID_PASSWORD)
        monkeypatch.delenv("ERC8183_AGENT_URL", raising=False)
        config = ERC8183Config.from_env()
        assert config.agent_url is None


class TestFromEnvOptional:
    def test_returns_none_when_missing(self, monkeypatch):
        monkeypatch.delenv("WALLET_PASSWORD", raising=False)
        monkeypatch.delenv("PRIVATE_KEY", raising=False)
        result = ERC8183Config.from_env_optional()
        assert result is None

    def test_returns_config_when_valid(self, monkeypatch):
        monkeypatch.setenv("PRIVATE_KEY", VALID_PK)
        monkeypatch.setenv("WALLET_PASSWORD", VALID_PASSWORD)
        result = ERC8183Config.from_env_optional()
        assert isinstance(result, ERC8183Config)
