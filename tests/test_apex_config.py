"""Tests for APEXConfig — configuration management."""

import pytest

from bnbagent.apex.config import APEXConfig


VALID_CONFIG = {
    "rpc_url": "https://rpc.example.com",
    "erc8183_address": "0x" + "ab" * 20,
    "private_key": "0x" + "cd" * 32,
}


class TestInit:
    def test_valid_config(self):
        config = APEXConfig(**VALID_CONFIG)
        assert config.rpc_url == VALID_CONFIG["rpc_url"]
        assert config.erc8183_address == VALID_CONFIG["erc8183_address"]
        assert config.chain_id == 97

    def test_missing_rpc_url(self):
        with pytest.raises(ValueError, match="rpc_url is required"):
            APEXConfig(rpc_url="", erc8183_address="0x" + "ab" * 20, private_key="0x" + "cd" * 32)

    def test_missing_erc8183_address(self):
        with pytest.raises(ValueError, match="erc8183_address is required"):
            APEXConfig(rpc_url="https://rpc.example.com", erc8183_address="", private_key="0x" + "cd" * 32)

    def test_missing_private_key(self):
        with pytest.raises(ValueError, match="private_key is required"):
            APEXConfig(rpc_url="https://rpc.example.com", erc8183_address="0x" + "ab" * 20, private_key="")

    def test_ipfs_without_jwt(self):
        with pytest.raises(ValueError, match="pinata_jwt is required"):
            APEXConfig(**VALID_CONFIG, storage_provider="ipfs")

    def test_normalizes_private_key(self):
        config = APEXConfig(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key="cd" * 32,  # No 0x prefix
        )
        assert config.private_key.startswith("0x")

    def test_repr_hides_key(self):
        config = APEXConfig(**VALID_CONFIG)
        r = repr(config)
        assert "***" in r
        assert VALID_CONFIG["private_key"] not in r


class TestFromEnv:
    def test_bsc_rpc_url_priority(self, monkeypatch):
        monkeypatch.setenv("BSC_RPC_URL", "https://bsc.example.com")
        monkeypatch.setenv("RPC_URL", "https://generic.example.com")
        monkeypatch.setenv("ERC8183_ADDRESS", "0x" + "ab" * 20)
        monkeypatch.setenv("PRIVATE_KEY", "0x" + "cd" * 32)
        config = APEXConfig.from_env()
        assert config.rpc_url == "https://bsc.example.com"

    def test_rpc_url_fallback(self, monkeypatch):
        monkeypatch.delenv("BSC_RPC_URL", raising=False)
        monkeypatch.setenv("RPC_URL", "https://generic.example.com")
        monkeypatch.setenv("ERC8183_ADDRESS", "0x" + "ab" * 20)
        monkeypatch.setenv("PRIVATE_KEY", "0x" + "cd" * 32)
        config = APEXConfig.from_env()
        assert config.rpc_url == "https://generic.example.com"

    def test_missing_vars_raises(self, monkeypatch):
        monkeypatch.delenv("BSC_RPC_URL", raising=False)
        monkeypatch.delenv("RPC_URL", raising=False)
        monkeypatch.delenv("ERC8183_ADDRESS", raising=False)
        monkeypatch.delenv("PRIVATE_KEY", raising=False)
        with pytest.raises(ValueError):
            APEXConfig.from_env()

    def test_optional_fields_from_env(self, monkeypatch):
        monkeypatch.setenv("BSC_RPC_URL", "https://rpc.example.com")
        monkeypatch.setenv("ERC8183_ADDRESS", "0x" + "ab" * 20)
        monkeypatch.setenv("PRIVATE_KEY", "0x" + "cd" * 32)
        monkeypatch.setenv("CHAIN_ID", "56")
        monkeypatch.setenv("AGENT_PRICE", "5000000000000000000")
        config = APEXConfig.from_env()
        assert config.chain_id == 56
        assert config.agent_price == "5000000000000000000"


class TestFromEnvOptional:
    def test_returns_none_when_missing(self, monkeypatch):
        monkeypatch.delenv("BSC_RPC_URL", raising=False)
        monkeypatch.delenv("RPC_URL", raising=False)
        monkeypatch.delenv("ERC8183_ADDRESS", raising=False)
        monkeypatch.delenv("PRIVATE_KEY", raising=False)
        result = APEXConfig.from_env_optional()
        assert result is None

    def test_returns_config_when_valid(self, monkeypatch):
        monkeypatch.setenv("BSC_RPC_URL", "https://rpc.example.com")
        monkeypatch.setenv("ERC8183_ADDRESS", "0x" + "ab" * 20)
        monkeypatch.setenv("PRIVATE_KEY", "0x" + "cd" * 32)
        result = APEXConfig.from_env_optional()
        assert isinstance(result, APEXConfig)
