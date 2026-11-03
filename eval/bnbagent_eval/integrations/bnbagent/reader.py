"""``ChainReaderPort`` over the SDK client's web3 instance."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from web3 import Web3
from web3.exceptions import TransactionNotFound

from ._errors import call

__all__ = ["BnbagentChainReader", "plain"]

_BALANCE_OF = "0x70a08231"  # balanceOf(address)


def plain(value: Any) -> Any:
    """web3 ``AttributeDict`` / ``HexBytes`` trees → plain dicts, lists and 0x strings."""
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    if isinstance(value, Mapping):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


class BnbagentChainReader:
    """Receipts, balances, calls and logs, optionally pinned to a block."""

    def __init__(self, web3: Any, *, chain_id: int):
        self.web3 = web3
        self.chain_id = int(chain_id)

    @staticmethod
    def _block(block: int | None) -> int | str:
        return "latest" if block is None else int(block)

    async def block_number(self) -> int:
        return int(await call(lambda: self.web3.eth.block_number, op="block_number"))

    async def _maybe(self, fn: Any, tx_hash: str, op: str) -> dict[str, Any] | None:
        def run() -> Any:
            try:
                return fn(tx_hash)
            except TransactionNotFound:
                return None

        result = await call(run, op=op)
        return None if result is None else plain(result)

    async def get_transaction(self, tx_hash: str) -> dict[str, Any] | None:
        return await self._maybe(self.web3.eth.get_transaction, tx_hash, "get_transaction")

    async def get_receipt(self, tx_hash: str) -> dict[str, Any] | None:
        return await self._maybe(self.web3.eth.get_transaction_receipt, tx_hash, "get_receipt")

    async def get_balance(
        self, address: str, *, token: str | None = None, block: int | None = None
    ) -> int:
        account = Web3.to_checksum_address(address)
        if token is None:
            fn = self.web3.eth.get_balance
            return int(await call(fn, account, block_identifier=self._block(block), op="balance"))
        data = _BALANCE_OF + account[2:].lower().rjust(64, "0")
        raw = await call(
            self.web3.eth.call,
            {"to": Web3.to_checksum_address(token), "data": data},
            block_identifier=self._block(block),
            op="token balance",
        )
        return int.from_bytes(bytes(raw), "big") if raw else 0

    async def call(self, to: str, data: str, *, block: int | None = None) -> str:
        raw = await call(
            self.web3.eth.call,
            {"to": Web3.to_checksum_address(to), "data": data},
            block_identifier=self._block(block),
            op="eth_call",
        )
        return "0x" + bytes(raw).hex()

    async def get_logs(self, filter: Mapping[str, Any]) -> list[dict[str, Any]]:
        logs = await call(self.web3.eth.get_logs, dict(filter), op="get_logs")
        return [plain(log) for log in logs]
