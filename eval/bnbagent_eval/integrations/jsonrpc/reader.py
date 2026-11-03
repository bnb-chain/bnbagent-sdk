"""``ChainReaderPort`` over JSON-RPC: receipts, transactions, balances, calls and logs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import abi
from .client import JsonRpcClient, from_hex

# JSON-RPC fields returned as hex quantities and converted to ints.
QUANTITY_KEYS = frozenset(
    {
        "blockNumber",
        "blobGasPrice",
        "blobGasUsed",
        "chainId",
        "cumulativeGasUsed",
        "effectiveGasPrice",
        "gas",
        "gasPrice",
        "gasUsed",
        "logIndex",
        "maxFeePerBlobGas",
        "maxFeePerGas",
        "maxPriorityFeePerGas",
        "nonce",
        "status",
        "transactionIndex",
        "type",
        "value",
    }
)


def normalize(obj: Mapping[str, Any]) -> dict[str, Any]:
    """Copy a receipt / transaction / log with hex quantities as ints (nested logs too)."""
    out: dict[str, Any] = {}
    for key, value in obj.items():
        if key in QUANTITY_KEYS and isinstance(value, str) and value[:2].lower() == "0x":
            out[key] = from_hex(value)
        elif key == "logs" and isinstance(value, list):
            out[key] = [normalize(v) if isinstance(v, Mapping) else v for v in value]
        else:
            out[key] = value
    return out


class JsonRpcReader:
    """``ChainReaderPort``. ``block=None`` reads at the client's pinned block."""

    def __init__(self, rpc: JsonRpcClient, *, chain_id: int):
        self.rpc = rpc
        self.chain_id = chain_id

    async def block_number(self) -> int:
        return await self.rpc.head()

    async def get_transaction(self, tx_hash: str) -> dict[str, Any] | None:
        tx = await self.rpc.eth_getTransactionByHash(tx_hash)
        return normalize(tx) if tx else None

    async def get_receipt(self, tx_hash: str) -> dict[str, Any] | None:
        receipt = await self.rpc.eth_getTransactionReceipt(tx_hash)
        return normalize(receipt) if receipt else None

    async def get_balance(
        self, address: str, *, token: str | None = None, block: int | None = None
    ) -> int:
        if token is None:
            return await self.rpc.eth_getBalance(address, block)
        erc20 = abi.load(abi.ERC20)
        data = await self.rpc.eth_call(token, erc20.encode_call("balanceOf", address), block)
        return int(erc20.decode_output("balanceOf", data)[0])

    async def call(self, to: str, data: str, *, block: int | None = None) -> str:
        return await self.rpc.eth_call(to, data, block)

    async def get_logs(self, filter: Mapping[str, Any]) -> list[dict[str, Any]]:
        return [normalize(log) for log in await self.rpc.eth_getLogs(filter)]


__all__ = ["QUANTITY_KEYS", "JsonRpcReader", "normalize"]
