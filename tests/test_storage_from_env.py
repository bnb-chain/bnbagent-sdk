"""Tests for provider-level from_env() classmethods."""

import pytest

from bnbagent.storage.local_storage_provider import LocalStorageProvider
from bnbagent.storage.ipfs_storage_provider import IPFSStorageProvider


class TestLocalProviderFromEnv:
    def test_default_path(self, monkeypatch):
        monkeypatch.delenv("STORAGE_LOCAL_PATH", raising=False)
        provider = LocalStorageProvider.from_env()
        assert str(provider._base) == ".agent-data"

    def test_respects_storage_local_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STORAGE_LOCAL_PATH", str(tmp_path / "custom"))
        provider = LocalStorageProvider.from_env()
        assert str(provider._base) == str(tmp_path / "custom")

    def test_returns_local_provider_instance(self, monkeypatch):
        monkeypatch.delenv("STORAGE_LOCAL_PATH", raising=False)
        provider = LocalStorageProvider.from_env()
        assert isinstance(provider, LocalStorageProvider)


class TestIPFSProviderFromEnv:
    def test_requires_api_key(self, monkeypatch):
        monkeypatch.delenv("STORAGE_API_KEY", raising=False)
        with pytest.raises(ValueError, match="STORAGE_API_KEY"):
            IPFSStorageProvider.from_env()

    def test_with_api_key(self, monkeypatch):
        monkeypatch.setenv("STORAGE_API_KEY", "test-jwt")
        monkeypatch.delenv("STORAGE_API_URL", raising=False)
        monkeypatch.delenv("STORAGE_GATEWAY_URL", raising=False)
        provider = IPFSStorageProvider.from_env()
        assert isinstance(provider, IPFSStorageProvider)
        assert provider._api_key == "test-jwt"

    def test_default_pinata_urls(self, monkeypatch):
        monkeypatch.setenv("STORAGE_API_KEY", "test-jwt")
        monkeypatch.delenv("STORAGE_API_URL", raising=False)
        monkeypatch.delenv("STORAGE_GATEWAY_URL", raising=False)
        provider = IPFSStorageProvider.from_env()
        assert "pinata.cloud" in provider._pinning_url
        assert "pinata.cloud" in provider._gateway

    def test_custom_api_url(self, monkeypatch):
        monkeypatch.setenv("STORAGE_API_KEY", "test-jwt")
        monkeypatch.setenv("STORAGE_API_URL", "https://custom.pin.io/pinJSON")
        monkeypatch.delenv("STORAGE_GATEWAY_URL", raising=False)
        provider = IPFSStorageProvider.from_env()
        assert provider._pinning_url == "https://custom.pin.io/pinJSON"

    def test_custom_gateway_url(self, monkeypatch):
        monkeypatch.setenv("STORAGE_API_KEY", "test-jwt")
        monkeypatch.delenv("STORAGE_API_URL", raising=False)
        monkeypatch.setenv("STORAGE_GATEWAY_URL", "https://custom.gateway.io/ipfs/")
        provider = IPFSStorageProvider.from_env()
        assert provider._gateway == "https://custom.gateway.io/ipfs"
