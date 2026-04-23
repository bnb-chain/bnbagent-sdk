"""Tests for storage factory functions."""

import pytest

from bnbagent.storage.config import StorageConfig
from bnbagent.storage.factory import create_storage_provider, storage_provider_from_env
from bnbagent.storage.ipfs_provider import IPFSStorageProvider
from bnbagent.storage.local_provider import LocalStorageProvider


class TestCreateStorageProvider:
    def test_default_local(self, tmp_path):
        config = StorageConfig(type="local", base_dir=str(tmp_path / "local"))
        provider = create_storage_provider(config)
        assert isinstance(provider, LocalStorageProvider)

    def test_explicit_local(self, tmp_path):
        config = StorageConfig(type="local", base_dir=str(tmp_path / "local"))
        provider = create_storage_provider(config)
        assert isinstance(provider, LocalStorageProvider)

    def test_ipfs_with_jwt(self):
        config = StorageConfig(type="ipfs", api_key="test-jwt")
        provider = create_storage_provider(config)
        assert isinstance(provider, IPFSStorageProvider)

    def test_ipfs_without_jwt_raises(self):
        config = StorageConfig(type="ipfs")
        with pytest.raises(ValueError, match="api_key.*required.*IPFS"):
            create_storage_provider(config)


class TestStorageProviderFromEnv:
    def test_default_local(self, monkeypatch, tmp_path):
        monkeypatch.delenv("STORAGE_PROVIDER", raising=False)
        monkeypatch.delenv("STORAGE_API_KEY", raising=False)
        provider = storage_provider_from_env(local_path=str(tmp_path / "local"))
        assert isinstance(provider, LocalStorageProvider)

    def test_ipfs_with_env(self, monkeypatch):
        monkeypatch.setenv("STORAGE_PROVIDER", "ipfs")
        monkeypatch.setenv("STORAGE_API_KEY", "test-jwt-env")
        provider = storage_provider_from_env()
        assert isinstance(provider, IPFSStorageProvider)

    def test_ipfs_missing_api_key_returns_none(self, monkeypatch):
        monkeypatch.setenv("STORAGE_PROVIDER", "ipfs")
        monkeypatch.delenv("STORAGE_API_KEY", raising=False)
        provider = storage_provider_from_env()
        assert provider is None

    def test_custom_gateway(self, monkeypatch):
        monkeypatch.setenv("STORAGE_PROVIDER", "ipfs")
        monkeypatch.setenv("STORAGE_API_KEY", "test-jwt")
        monkeypatch.setenv("STORAGE_GATEWAY_URL", "https://custom.gateway.io/ipfs/")
        provider = storage_provider_from_env()
        assert isinstance(provider, IPFSStorageProvider)
        assert "custom.gateway.io" in provider._gateway
