"""Tests for IPFSStorageProvider — IPFS pinning service storage."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bnbagent.exceptions import StorageError
from bnbagent.storage.ipfs_provider import IPFSStorageProvider

# Valid CIDv0 for tests (Qm + 44 base58 chars)
VALID_CID = "QmYwAPJzv5CZsnA625s3Xf2nemtYgPpHdWEz79ojWnPbdG"
VALID_CID_2 = "QmPK1s3pNYLi9ERiq3BDxKa4XosgWwFRQUydHUtz4YgpqB"
VALID_CID_3 = "QmRZxt2b1FVZPNqd8hsiykDL3TdBDeTSPX9Kv46HmX4Gx8"


def _make_provider():
    return IPFSStorageProvider(
        pinning_api_url="https://api.pinata.cloud/pinning/pinJSONToIPFS",
        pinning_api_key="test-jwt-token",
        gateway_url="https://gateway.pinata.cloud/ipfs/",
    )


class TestIPFSStorageProvider:
    @pytest.mark.asyncio
    async def test_upload_posts_to_pinata(self):
        provider = _make_provider()
        mock_response = MagicMock()
        mock_response.json.return_value = {"IpfsHash": VALID_CID}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("bnbagent.storage.ipfs_provider.httpx.AsyncClient", return_value=mock_client):
            url = await provider.upload({"test": "data"})

        assert url == f"ipfs://{VALID_CID}"
        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert "Bearer test-jwt-token" in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_upload_returns_ipfs_url(self):
        provider = _make_provider()
        mock_response = MagicMock()
        mock_response.json.return_value = {"IpfsHash": VALID_CID}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("bnbagent.storage.ipfs_provider.httpx.AsyncClient", return_value=mock_client):
            url = await provider.upload({"data": 1})

        assert url.startswith("ipfs://")

    @pytest.mark.asyncio
    async def test_upload_with_filename(self):
        provider = _make_provider()
        mock_response = MagicMock()
        mock_response.json.return_value = {"IpfsHash": VALID_CID_2}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("bnbagent.storage.ipfs_provider.httpx.AsyncClient", return_value=mock_client):
            _url = await provider.upload({"data": 1}, filename="job-5.json")

        call_kwargs = mock_client.post.call_args
        payload = call_kwargs[1]["json"]
        assert payload["pinataMetadata"]["name"] == "job-5"

    @pytest.mark.asyncio
    async def test_upload_missing_cid_raises(self):
        provider = _make_provider()
        mock_response = MagicMock()
        mock_response.json.return_value = {"unexpected": "response"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("bnbagent.storage.ipfs_provider.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(StorageError, match="Unexpected pinning response"):
                await provider.upload({"data": 1})

    @pytest.mark.asyncio
    async def test_download_from_gateway(self):
        provider = _make_provider()
        mock_response = MagicMock()
        mock_response.json.return_value = {"downloaded": True}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("bnbagent.storage.ipfs_provider.httpx.AsyncClient", return_value=mock_client):
            result = await provider.download(f"ipfs://{VALID_CID}")

        assert result["downloaded"] is True
        mock_client.get.assert_called_once()
        call_args = mock_client.get.call_args[0][0]
        assert VALID_CID in call_args

    @pytest.mark.asyncio
    async def test_exists_true(self):
        provider = _make_provider()
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.head.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("bnbagent.storage.ipfs_provider.httpx.AsyncClient", return_value=mock_client):
            assert await provider.exists(f"ipfs://{VALID_CID}") is True

    @pytest.mark.asyncio
    async def test_exists_false(self):
        provider = _make_provider()
        mock_response = MagicMock()
        mock_response.status_code = 404

        mock_client = AsyncMock()
        mock_client.head.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("bnbagent.storage.ipfs_provider.httpx.AsyncClient", return_value=mock_client):
            assert await provider.exists(f"ipfs://{VALID_CID_2}") is False

    def test_get_gateway_url(self):
        provider = _make_provider()
        url = provider.get_gateway_url(f"ipfs://{VALID_CID}")
        assert url == f"https://gateway.pinata.cloud/ipfs/{VALID_CID}"

    @pytest.mark.asyncio
    async def test_upload_uses_cid_key(self):
        """Test that the provider also accepts 'cid' key in response."""
        provider = _make_provider()
        mock_response = MagicMock()
        mock_response.json.return_value = {"cid": VALID_CID_3}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("bnbagent.storage.ipfs_provider.httpx.AsyncClient", return_value=mock_client):
            url = await provider.upload({"test": 1})

        assert url == f"ipfs://{VALID_CID_3}"

    def test_extract_cid_rejects_invalid(self):
        with pytest.raises(StorageError, match="Invalid IPFS CID format"):
            IPFSStorageProvider._extract_cid("ipfs://not-a-valid-cid")

    def test_extract_cid_accepts_valid_v0(self):
        cid = IPFSStorageProvider._extract_cid(f"ipfs://{VALID_CID}")
        assert cid == VALID_CID
