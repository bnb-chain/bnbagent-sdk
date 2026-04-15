"""Shared fixtures for bnbagent SDK test suite."""

from unittest.mock import AsyncMock, MagicMock

import pytest

FAKE_ADDRESS = "0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18"
FAKE_PRIVATE_KEY = "0x" + "ab" * 32
FAKE_CONTRACT_ADDRESS = "0xf8b6921fea71dfca3482a4a69576198d2072d188"
FAKE_TX_HASH = "0x" + "de" * 32


@pytest.fixture
def mock_web3():
    """Deep mock of Web3 with common eth methods."""
    w3 = MagicMock()
    w3.provider.endpoint_uri = "https://fake-rpc.example.com"
    w3.eth.get_transaction_count.return_value = 0
    w3.eth.block_number = 1000

    account_mock = MagicMock()
    account_mock.address = FAKE_ADDRESS
    w3.eth.account.from_key.return_value = account_mock

    signed_tx = MagicMock()
    signed_tx.raw_transaction = b"\x00" * 32
    w3.eth.account.sign_transaction.return_value = signed_tx

    w3.eth.send_raw_transaction.return_value = bytes.fromhex(FAKE_TX_HASH[2:])
    w3.eth.wait_for_transaction_receipt.return_value = {
        "transactionHash": bytes.fromhex(FAKE_TX_HASH[2:]),
        "status": 1,
        "blockNumber": 100,
        "gasUsed": 21000,
    }

    return w3


@pytest.fixture
def mock_contract():
    """Mock contract with functions and events sub-objects."""
    contract = MagicMock()
    return contract


@pytest.fixture
def fake_receipt():
    """Standard successful tx receipt dict."""
    return {
        "transactionHash": bytes.fromhex(FAKE_TX_HASH[2:]),
        "status": 1,
        "blockNumber": 100,
        "gasUsed": 21000,
    }


@pytest.fixture
def fake_abi():
    """Empty ABI list to bypass ABI file loading."""
    return []


@pytest.fixture
def apex_client(mock_web3, fake_abi):
    """APEXClient instance with mocked web3."""
    from bnbagent.apex.client import APEXClient

    return APEXClient(mock_web3, FAKE_CONTRACT_ADDRESS, FAKE_PRIVATE_KEY, fake_abi)


@pytest.fixture
def evaluator_client(mock_web3, fake_abi):
    """APEXEvaluatorClient instance with mocked web3."""
    from bnbagent.apex.evaluator_client import APEXEvaluatorClient

    return APEXEvaluatorClient(mock_web3, FAKE_CONTRACT_ADDRESS, FAKE_PRIVATE_KEY, fake_abi)


@pytest.fixture
def mock_storage():
    """AsyncMock of StorageProvider."""
    storage = AsyncMock()
    storage.upload.return_value = "file:///tmp/test.json"
    storage.download.return_value = {"test": "data"}
    storage.exists.return_value = True
    return storage


@pytest.fixture(autouse=True)
def clear_nonce_singletons():
    """Clear NonceManager singletons after each test."""
    yield
    from bnbagent.core.nonce_manager import NonceManager

    NonceManager._clear_all()
