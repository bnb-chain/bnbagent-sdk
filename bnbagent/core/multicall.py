"""Multicall3 batch read utility for EVM chains.

Aggregates multiple read-only contract calls into a single RPC request via
the canonical Multicall3 contract (deployed at the same address on all EVM
chains). This avoids `eth_getLogs` rate limits by using cheap `eth_call`.

Example::

    from bnbagent.core.multicall import multicall_read

    results = multicall_read(
        w3=web3_instance,
        contract=commerce_contract,
        function_name="getJob",
        call_args_list=[(0,), (1,), (2,)],
    )
    for success, decoded in results:
        if success:
            print(decoded)
"""

from __future__ import annotations

import logging
import time
from typing import Any

from eth_abi import decode as abi_decode
from web3 import Web3
from web3.contract import Contract

logger = logging.getLogger(__name__)


def _encode_call(contract: Contract, function_name: str, args: list) -> str:
    """Encode a contract function call, compatible with web3.py 6.x and 7.x."""
    if hasattr(contract, "encodeABI"):
        return contract.encodeABI(fn_name=function_name, args=args)
    return contract.encode_abi(abi_element_identifier=function_name, args=args)


def _abi_type(output: dict) -> str:
    """Build the full eth_abi type string for an ABI output, handling tuples recursively."""
    base = output["type"]
    if base == "tuple" and "components" in output:
        inner = ",".join(_abi_type(c) for c in output["components"])
        return f"({inner})"
    if base == "tuple[]" and "components" in output:
        inner = ",".join(_abi_type(c) for c in output["components"])
        return f"({inner})[]"
    return base


def _get_output_types(contract: Contract, function_name: str) -> list[str]:
    """Extract output types for a function from the contract ABI."""
    for item in contract.abi:
        if item.get("type") == "function" and item.get("name") == function_name:
            return [_abi_type(o) for o in item.get("outputs", [])]
    raise ValueError(f"Function {function_name} not found in ABI")

# Canonical Multicall3 address — same on all EVM chains
MULTICALL3_ADDRESS = "0xcA11bde05977b3631167028862bE2a173976CA11"

# Minimal ABI: only the aggregate3 function
MULTICALL3_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"name": "target", "type": "address"},
                    {"name": "allowFailure", "type": "bool"},
                    {"name": "callData", "type": "bytes"},
                ],
                "name": "calls",
                "type": "tuple[]",
            }
        ],
        "name": "aggregate3",
        "outputs": [
            {
                "components": [
                    {"name": "success", "type": "bool"},
                    {"name": "returnData", "type": "bytes"},
                ],
                "name": "returnData",
                "type": "tuple[]",
            }
        ],
        "stateMutability": "payable",
        "type": "function",
    }
]

DEFAULT_BATCH_SIZE = 100
MAX_RETRIES = 5
RETRY_BASE_DELAY = 1.0


def multicall_read(
    w3: Web3,
    contract: Contract,
    function_name: str,
    call_args_list: list[tuple],
    batch_size: int = DEFAULT_BATCH_SIZE,
    allow_failure: bool = True,
) -> list[tuple[bool, Any]]:
    """Batch read calls via Multicall3.

    Encodes each call against *contract*, batches them into Multicall3
    ``aggregate3`` invocations, and decodes the results.

    Args:
        w3: Web3 instance connected to an RPC node.
        contract: Target contract (e.g. ERC-8183) — used for ABI encoding/decoding.
        function_name: Name of the view function to call (e.g. ``"getJob"``).
        call_args_list: List of argument tuples, one per call.
        batch_size: Max calls per ``aggregate3`` invocation (default: 100).
        allow_failure: If True, individual call failures return ``(False, None)``
            instead of reverting the entire batch.

    Returns:
        List of ``(success, decoded_result)`` tuples matching input order.
        On failure (when ``allow_failure=True``), the tuple is ``(False, None)``.
    """
    if not call_args_list:
        return []

    multicall3 = w3.eth.contract(
        address=Web3.to_checksum_address(MULTICALL3_ADDRESS),
        abi=MULTICALL3_ABI,
    )

    target = contract.address

    # Encode all calldata upfront
    encoded_calls = []
    for args in call_args_list:
        calldata = _encode_call(contract, function_name, list(args))
        encoded_calls.append(
            {
                "target": target,
                "allowFailure": allow_failure,
                "callData": calldata,
            }
        )

    output_types = _get_output_types(contract, function_name)

    # Split into batches
    results: list[tuple[bool, Any]] = []
    for i in range(0, len(encoded_calls), batch_size):
        batch = encoded_calls[i : i + batch_size]
        raw_results = _aggregate3_with_retry(multicall3, batch)

        for j, (success, return_data) in enumerate(raw_results):
            if success and return_data:
                try:
                    decoded = abi_decode(output_types, return_data)
                    # unwrap single-value results
                    if len(decoded) == 1:
                        decoded = decoded[0]
                    results.append((True, decoded))
                except Exception:
                    results.append((False, None))
            else:
                results.append((False, None))

    return results


def _aggregate3_with_retry(multicall3: Contract, calls: list[dict]) -> list[tuple[bool, bytes]]:
    """Call aggregate3 with exponential backoff retry on rate limits."""
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            return multicall3.functions.aggregate3(calls).call()
        except Exception as e:
            last_error = e
            error_str = str(e).lower()
            is_rate_limit = "429" in error_str or "too many requests" in error_str
            if is_rate_limit and attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2**attempt)
                logger.warning(
                    f"[Multicall3] Rate limited, retry {attempt + 1}/{MAX_RETRIES} "
                    f"in {delay:.1f}s"
                )
                time.sleep(delay)
            else:
                raise
    raise last_error  # type: ignore
