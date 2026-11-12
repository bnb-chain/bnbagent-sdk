from unittest.mock import patch

import httpx
import pytest

from bnbagent.exceptions import StorageError
from bnbagent.storage.ipfs_storage_provider import IPFSStorageProvider


@pytest.mark.asyncio
async def test_pinning_response_is_stream_bounded():
    provider = IPFSStorageProvider("https://pin.example", "key")
    real_client = httpx.AsyncClient
    with patch(
        "bnbagent.storage.ipfs_storage_provider.httpx.AsyncClient",
        side_effect=lambda **kw: real_client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 65537)),
            **kw,
        ),
    ):
        with pytest.raises(StorageError, match="byte limit"):
            await provider.upload({"ok": True})
