"""Tests for APEXConfig — configuration management."""

from unittest.mock import MagicMock

import pytest

from bnbagent.apex.config import APEXConfig

VALID_PK = "0x" + "cd" * 32
VALID_PASSWORD = "test-password"


class TestInit:
    def test_valid_config_with_wallet_password(self):
        config = APEXConfig(private_key=VALID_PK, wallet_password=VALID_PASSWORD)
        # private_key should be cleared after auto-wrap
        assert config.private_key == ""
        assert config.wallet_provider is not None
        assert config.effective_chain_id == 97

    def test_explicit_wallet_provider(self):
        mock_wallet = MagicMock()
        mock_wallet.address = "0x" + "ff" * 20
        config = APEXConfig(wallet_provider=mock_wallet)
        assert config.wallet_provider is mock_wallet
        assert config.private_key == ""

    def test_explicit_rpc_and_address(self):
        config = APEXConfig(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key=VALID_PK,
            wallet_password=VALID_PASSWORD,
        )
        assert config.rpc_url == "https://rpc.example.com"
        assert config.erc8183_address == "0x" + "ab" * 20

    def test_private_key_without_password_raises(self):
        with pytest.raises(ValueError, match="wallet_password is required"):
            APEXConfig(private_key=VALID_PK)

    def test_missing_private_key_no_keystore_auto_generates(self, monkeypatch, tmp_path):
        """When no PRIVATE_KEY and no keystore, from_env() succeeds and auto-generates a wallet."""
        monkeypatch.delenv("PRIVATE_KEY", raising=False)
        monkeypatch.delenv("RPC_URL", raising=False)
        monkeypatch.setenv("WALLET_PASSWORD", "test-pw")
        import bnbagent.wallets.evm_wallet_provider as wp
        monkeypatch.setattr(wp, "_WALLETS_DIR", tmp_path / "wallets")
        config = APEXConfig.from_env()
        assert config.wallet_provider is not None

    def test_password_only_no_keystore_auto_generates(self, monkeypatch, tmp_path):
        """Direct constructor: wallet_password only, no keystore → auto-generate wallet."""
        import bnbagent.wallets.evm_wallet_provider as wp
        monkeypatch.setattr(wp, "_WALLETS_DIR", tmp_path / "wallets")
        config = APEXConfig(wallet_password="test-pw")
        assert config.wallet_provider is not None
        assert config.wallet_provider.source == "created_new"

    def test_missing_wallet_password_in_from_env(self, monkeypatch):
        monkeypatch.setenv("PRIVATE_KEY", VALID_PK)
        monkeypatch.delenv("WALLET_PASSWORD", raising=False)
        with pytest.raises(ValueError, match="WALLET_PASSWORD is required"):
            APEXConfig.from_env()

    def test_no_private_key_no_wallet_ok(self):
        """APEXConfig without any wallet does not raise (read-only config)."""
        config = APEXConfig()
        assert config.wallet_provider is None

    def test_payment_token_decimals_default(self):
        config = APEXConfig()
        assert config.payment_token_decimals == 18

    def test_payment_token_decimals_custom(self):
        config = APEXConfig(payment_token_decimals=6)
        assert config.payment_token_decimals == 6

    def test_normalizes_private_key_and_wraps(self):
        config = APEXConfig(
            private_key="cd" * 32,  # No 0x prefix
            wallet_password=VALID_PASSWORD,
        )
        assert config.private_key == ""
        assert config.wallet_provider is not None

    def test_private_key_cleared_after_wrap(self):
        config = APEXConfig(private_key=VALID_PK, wallet_password=VALID_PASSWORD)
        assert config.private_key == ""
        assert VALID_PK not in repr(config)

    def test_repr_with_wallet_provider(self):
        mock_wallet = MagicMock()
        mock_wallet.address = "0x" + "ff" * 20
        config = APEXConfig(wallet_provider=mock_wallet)
        r = repr(config)
        assert "wallet=" in r
        assert "0xffffffff" in r.lower()

    def test_repr_no_wallet(self):
        config = APEXConfig()
        r = repr(config)
        assert "wallet=None" in r


class TestFromEnv:
    def test_rpc_url_from_env(self, monkeypatch):
        monkeypatch.setenv("RPC_URL", "https://rpc.example.com")
        monkeypatch.setenv("ERC8183_ADDRESS", "0x" + "ab" * 20)
        monkeypatch.setenv("PRIVATE_KEY", VALID_PK)
        monkeypatch.setenv("WALLET_PASSWORD", VALID_PASSWORD)
        config = APEXConfig.from_env()
        assert config.rpc_url == "https://rpc.example.com"

    def test_missing_vars_raises(self, monkeypatch):
        monkeypatch.delenv("RPC_URL", raising=False)
        monkeypatch.delenv("ERC8183_ADDRESS", raising=False)
        monkeypatch.delenv("PRIVATE_KEY", raising=False)
        with pytest.raises(ValueError):
            APEXConfig.from_env()

    def test_optional_fields_from_env(self, monkeypatch):
        monkeypatch.setenv("RPC_URL", "https://rpc.example.com")
        monkeypatch.setenv("ERC8183_ADDRESS", "0x" + "ab" * 20)
        monkeypatch.setenv("PRIVATE_KEY", VALID_PK)
        monkeypatch.setenv("WALLET_PASSWORD", VALID_PASSWORD)
        monkeypatch.setenv("CHAIN_ID", "56")
        monkeypatch.setenv("SERVICE_PRICE", "5000000000000000000")
        config = APEXConfig.from_env()
        assert config.chain_id == 56
        assert config.service_price == "5000000000000000000"

    def test_wallet_provider_auto_created(self, monkeypatch):
        monkeypatch.setenv("PRIVATE_KEY", VALID_PK)
        monkeypatch.setenv("WALLET_PASSWORD", VALID_PASSWORD)
        config = APEXConfig.from_env()
        assert config.wallet_provider is not None
        assert config.private_key == ""


class TestFromEnvOptional:
    def test_returns_none_when_missing(self, monkeypatch):
        monkeypatch.delenv("RPC_URL", raising=False)
        monkeypatch.delenv("RPC_URL", raising=False)
        monkeypatch.delenv("ERC8183_ADDRESS", raising=False)
        monkeypatch.delenv("PRIVATE_KEY", raising=False)
        result = APEXConfig.from_env_optional()
        assert result is None

    def test_returns_config_when_valid(self, monkeypatch):
        monkeypatch.setenv("RPC_URL", "https://rpc.example.com")
        monkeypatch.setenv("ERC8183_ADDRESS", "0x" + "ab" * 20)
        monkeypatch.setenv("PRIVATE_KEY", VALID_PK)
        monkeypatch.setenv("WALLET_PASSWORD", VALID_PASSWORD)
        result = APEXConfig.from_env_optional()
        assert isinstance(result, APEXConfig)
