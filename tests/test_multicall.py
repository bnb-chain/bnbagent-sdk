"""Tests for Multicall3 batch read utility."""

from unittest.mock import MagicMock, patch

import pytest
from eth_abi import encode as abi_encode

from bnbagent.core.multicall import _encode_call, _get_output_types, multicall_read


def _make_mocks(num_calls, batch_size=100, failures=None):
    """Build mock w3, contract, and multicall3 for testing.

    Args:
        num_calls: Number of calls in call_args_list.
        batch_size: Batch size for multicall_read.
        failures: Set of indices that should return (False, b"") in aggregate3.

    Returns:
        (w3, contract, multicall3_mock, call_args_list)
    """
    failures = failures or set()

    w3 = MagicMock()
    contract = MagicMock()
    contract.address = "0x" + "ab" * 20

    # Provide ABI so _get_output_types can extract output types
    contract.abi = [
        {
            "type": "function",
            "name": "getJob",
            "inputs": [{"name": "id", "type": "uint256"}],
            "outputs": [{"name": "r", "type": "bytes"}],
            "stateMutability": "view",
        }
    ]

    # encodeABI returns unique bytes per call (web3 6.x style, picked up by _encode_call)
    def encode_abi(fn_name, args):
        return f"calldata_{args[0]}".encode()

    contract.encodeABI.side_effect = encode_abi

    # Build expected aggregate3 results per batch
    # Return data must be ABI-encoded to match the output type (bytes)
    all_results = []
    for i in range(num_calls):
        if i in failures:
            all_results.append((False, b""))
        else:
            all_results.append((True, abi_encode(["bytes"], [f"result_{i}".encode()])))

    # Split into batches to return from successive aggregate3 calls
    batched_results = []
    for start in range(0, num_calls, batch_size):
        batched_results.append(all_results[start : start + batch_size])

    multicall3_mock = MagicMock()
    multicall3_mock.functions.aggregate3.return_value.call.side_effect = batched_results

    w3.eth.contract.return_value = multicall3_mock

    call_args_list = [(i,) for i in range(num_calls)]
    return w3, contract, multicall3_mock, call_args_list


class TestSingleBatch:
    def test_all_succeed(self):
        w3, contract, mc3, args = _make_mocks(5)
        results = multicall_read(w3, contract, "getJob", args, batch_size=100)

        assert len(results) == 5
        assert all(success for success, _ in results)
        # Single aggregate3 call
        assert mc3.functions.aggregate3.return_value.call.call_count == 1


class TestMultipleBatches:
    def test_three_batches(self):
        w3, contract, mc3, args = _make_mocks(250, batch_size=100)
        results = multicall_read(w3, contract, "getJob", args, batch_size=100)

        assert len(results) == 250
        assert all(success for success, _ in results)
        # 3 batches: 100 + 100 + 50
        assert mc3.functions.aggregate3.return_value.call.call_count == 3


class TestPartialFailure:
    def test_failed_calls_return_false_none(self):
        w3, contract, mc3, args = _make_mocks(5, failures={1, 3})
        results = multicall_read(w3, contract, "getJob", args, batch_size=100)

        assert len(results) == 5
        assert results[0] == (True, b"result_0")
        assert results[1] == (False, None)
        assert results[2] == (True, b"result_2")
        assert results[3] == (False, None)
        assert results[4] == (True, b"result_4")


class TestEmptyList:
    def test_returns_empty(self):
        w3 = MagicMock()
        contract = MagicMock()
        results = multicall_read(w3, contract, "getJob", [])
        assert results == []


class TestRpcErrorPropagates:
    def test_exception_raised(self):
        w3 = MagicMock()
        contract = MagicMock()
        contract.address = "0x" + "ab" * 20
        contract.abi = [
            {
                "type": "function",
                "name": "getJob",
                "inputs": [{"name": "id", "type": "uint256"}],
                "outputs": [{"name": "r", "type": "bytes"}],
                "stateMutability": "view",
            }
        ]
        contract.encodeABI.return_value = b"calldata"

        mc3 = MagicMock()
        mc3.functions.aggregate3.return_value.call.side_effect = Exception("connection refused")
        w3.eth.contract.return_value = mc3

        with pytest.raises(Exception, match="connection refused"):
            multicall_read(w3, contract, "getJob", [(0,)])


class TestRealContract:
    """Smoke tests using a real web3 Contract object (no RPC needed).

    These catch API changes in web3.py that mocked tests would miss.
    Uses a tuple-struct output (like ERC-8183 getJob) to cover the
    real-world ABI shape.
    """

    SAMPLE_ABI = [
        {
            "type": "function",
            "name": "getJob",
            "inputs": [{"name": "jobId", "type": "uint256"}],
            "outputs": [
                {
                    "name": "",
                    "type": "tuple",
                    "components": [
                        {"name": "client", "type": "address"},
                        {"name": "provider", "type": "address"},
                        {"name": "budget", "type": "uint256"},
                        {"name": "status", "type": "uint8"},
                        {"name": "description", "type": "string"},
                    ],
                }
            ],
            "stateMutability": "view",
        }
    ]

    def _make_contract(self):
        from web3 import Web3

        w3 = Web3()
        return w3.eth.contract(
            address="0xcA11bde05977b3631167028862bE2a173976CA11",
            abi=self.SAMPLE_ABI,
        )

    def test_encode_call_returns_hex(self):
        contract = self._make_contract()
        calldata = _encode_call(contract, "getJob", [1])
        assert isinstance(calldata, (str, bytes))
        assert len(calldata) > 0

    def test_get_output_types_tuple(self):
        contract = self._make_contract()
        types = _get_output_types(contract, "getJob")
        assert types == ["(address,address,uint256,uint8,string)"]

    def test_decode_roundtrip(self):
        """Encode args, then decode return data — full path without RPC."""
        from eth_abi import decode as abi_decode
        from eth_abi import encode as _abi_encode

        contract = self._make_contract()

        # Encode a call (verifies _encode_call works with real Contract)
        _encode_call(contract, "getJob", [42])

        # Simulate ABI-encoded return data and decode it
        output_types = _get_output_types(contract, "getJob")
        addr = "0xcA11bde05977b3631167028862bE2a173976CA11"
        fake_return = _abi_encode(
            output_types,
            [(addr, addr, 100, 1, "test job")],
        )
        decoded = abi_decode(output_types, fake_return)
        # decoded is ((addr, addr, 100, 1, "test job"),) — unwrap tuple
        job = decoded[0]
        assert job[2] == 100
        assert job[3] == 1
        assert job[4] == "test job"
