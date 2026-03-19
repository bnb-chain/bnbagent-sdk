"""Tests for Multicall3 batch read utility."""

from unittest.mock import MagicMock, patch

import pytest

from bnbagent.core.multicall import multicall_read


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

    # encodeABI returns unique bytes per call
    def encode_abi(fn_name, args):
        return f"calldata_{args[0]}".encode()

    contract.encodeABI.side_effect = encode_abi

    # decode_function_result returns a tuple wrapping the decoded values
    def decode_fn_result(fn_name, return_data):
        return (return_data,)

    contract.decode_function_result.side_effect = decode_fn_result

    # Build expected aggregate3 results per batch
    all_results = []
    for i in range(num_calls):
        if i in failures:
            all_results.append((False, b""))
        else:
            all_results.append((True, f"result_{i}".encode()))

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
        contract.encodeABI.return_value = b"calldata"

        mc3 = MagicMock()
        mc3.functions.aggregate3.return_value.call.side_effect = Exception("connection refused")
        w3.eth.contract.return_value = mc3

        with pytest.raises(Exception, match="connection refused"):
            multicall_read(w3, contract, "getJob", [(0,)])
