import pytest

from bnbagent.exceptions import StorageError
from bnbagent.storage.ipfs_storage_provider import IPFSStorageProvider


def test_oversized_cid_cannot_build_a_gateway_request():
    provider = IPFSStorageProvider("https://pin.example", "key")
    with pytest.raises(StorageError):
        provider.get_gateway_url("ipfs://b" + "a" * 100000)


def test_invalid_ipfs_input_is_rejected_before_string_operations():
    provider = IPFSStorageProvider("https://pin.example", "key")
    with pytest.raises(StorageError):
        provider.get_gateway_url(None)
