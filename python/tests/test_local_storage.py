"""Tests for LocalStorageProvider — filesystem storage."""

import os
import stat

import pytest

from bnbagent.exceptions import StorageError
from bnbagent.storage.local_storage_provider import LocalStorageProvider


class TestLocalStorageProvider:
    def test_creates_directory(self, tmp_path):
        base = tmp_path / "storage"
        _provider = LocalStorageProvider(str(base))
        assert base.exists()

    def test_directory_permissions(self, tmp_path):
        base = tmp_path / "storage"
        LocalStorageProvider(str(base))
        mode = os.stat(base).st_mode
        assert mode & stat.S_IRWXU  # Owner has rwx

    @pytest.mark.asyncio
    async def test_upload_returns_file_url(self, tmp_path):
        provider = LocalStorageProvider(str(tmp_path / "data"))
        url = await provider.upload({"key": "value"})
        assert url.startswith("file://")

    @pytest.mark.asyncio
    async def test_upload_with_filename(self, tmp_path):
        provider = LocalStorageProvider(str(tmp_path / "data"))
        url = await provider.upload({"test": 1}, filename="myfile.json")
        assert "myfile.json" in url

    @pytest.mark.asyncio
    async def test_upload_without_filename_uses_job_id(self, tmp_path):
        provider = LocalStorageProvider(str(tmp_path / "data"))
        url = await provider.upload({"job": {"id": 42}})
        assert "job-42.json" in url

    @pytest.mark.asyncio
    async def test_upload_without_filename_uses_hash(self, tmp_path):
        provider = LocalStorageProvider(str(tmp_path / "data"))
        url = await provider.upload({"random": "data"})
        assert url.endswith(".json")

    @pytest.mark.asyncio
    async def test_file_permissions(self, tmp_path):
        provider = LocalStorageProvider(str(tmp_path / "data"))
        url = await provider.upload({"key": "val"}, "test.json")
        filepath = tmp_path / "data" / "test.json"
        mode = os.stat(filepath).st_mode
        assert mode & stat.S_IRUSR
        assert mode & stat.S_IWUSR

    def test_upload_sync(self, tmp_path):
        from bnbagent.storage import upload_sync
        provider = LocalStorageProvider(str(tmp_path / "data"))
        url = upload_sync(provider, {"sync": True}, "sync-test.json")
        assert url.startswith("file://")
        assert "sync-test.json" in url

    def test_from_env_default(self, tmp_path, monkeypatch):
        monkeypatch.delenv("STORAGE_LOCAL_PATH", raising=False)
        provider = LocalStorageProvider.from_env()
        assert str(provider._base) == ".agent-data"

    def test_from_env_custom_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STORAGE_LOCAL_PATH", str(tmp_path / "custom"))
        provider = LocalStorageProvider.from_env()
        assert str(provider._base) == str(tmp_path / "custom")

    @pytest.mark.asyncio
    async def test_download_success(self, tmp_path):
        provider = LocalStorageProvider(str(tmp_path / "data"))
        original = {"download": "test"}
        url = await provider.upload(original, "dl.json")
        result = await provider.download(url)
        assert result["download"] == "test"

    @pytest.mark.asyncio
    async def test_download_not_found(self, tmp_path):
        base = tmp_path / "data"
        provider = LocalStorageProvider(str(base))
        # File inside base dir that doesn't exist
        missing = base / "nonexistent.json"
        with pytest.raises(StorageError, match="not found"):
            await provider.download(f"file://{missing}")

    @pytest.mark.asyncio
    async def test_exists_true(self, tmp_path):
        provider = LocalStorageProvider(str(tmp_path / "data"))
        url = await provider.upload({"exists": True}, "check.json")
        assert await provider.exists(url) is True

    @pytest.mark.asyncio
    async def test_exists_false(self, tmp_path):
        base = tmp_path / "data"
        provider = LocalStorageProvider(str(base))
        missing = base / "nosuch.json"
        assert await provider.exists(f"file://{missing}") is False

    @pytest.mark.asyncio
    async def test_upload_path_traversal_relative(self, tmp_path):
        provider = LocalStorageProvider(str(tmp_path / "data"))
        with pytest.raises(StorageError, match="Path traversal blocked"):
            await provider.upload({"k": "v"}, "../escape.json")
        assert not (tmp_path / "escape.json").exists()

    @pytest.mark.asyncio
    async def test_upload_path_traversal_absolute(self, tmp_path):
        provider = LocalStorageProvider(str(tmp_path / "data"))
        outside = tmp_path / "outside.json"
        with pytest.raises(StorageError, match="Path traversal blocked"):
            await provider.upload({"k": "v"}, str(outside))
        assert not outside.exists()

    @pytest.mark.asyncio
    async def test_upload_path_traversal_nested(self, tmp_path):
        provider = LocalStorageProvider(str(tmp_path / "data"))
        with pytest.raises(StorageError, match="Path traversal blocked"):
            await provider.upload({"k": "v"}, "a/../../escape.json")
        assert not (tmp_path / "escape.json").exists()

    @pytest.mark.asyncio
    async def test_upload_path_traversal_via_symlink(self, tmp_path):
        base = tmp_path / "data"
        outside = tmp_path / "outside"
        outside.mkdir()
        provider = LocalStorageProvider(str(base))
        (base / "link").symlink_to(outside, target_is_directory=True)
        with pytest.raises(StorageError, match="Path traversal blocked"):
            await provider.upload({"k": "v"}, "link/escape.json")
        assert not (outside / "escape.json").exists()

    def test_upload_sync_path_traversal_blocked(self, tmp_path):
        from bnbagent.storage import upload_sync
        provider = LocalStorageProvider(str(tmp_path / "data"))
        with pytest.raises(StorageError, match="Path traversal blocked"):
            upload_sync(provider, {"k": "v"}, "../escape.json")
        assert not (tmp_path / "escape.json").exists()

    @pytest.mark.asyncio
    async def test_download_path_traversal_blocked(self, tmp_path):
        provider = LocalStorageProvider(str(tmp_path / "data"))
        with pytest.raises(StorageError, match="Path traversal blocked"):
            await provider.download("file:///etc/passwd")

    @pytest.mark.asyncio
    async def test_exists_path_traversal_blocked(self, tmp_path):
        provider = LocalStorageProvider(str(tmp_path / "data"))
        with pytest.raises(StorageError, match="Path traversal blocked"):
            await provider.exists("file:///etc/passwd")
