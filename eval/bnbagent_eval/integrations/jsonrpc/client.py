"""A small async Ethereum JSON-RPC client over httpx (no web3).

Quantities go out as hex strings; reads use a block parameter that defaults to the client's
pinned block (``"latest"`` unless pinned to a number). JSON-RPC and transport failures become
:class:`ChainIntegrationError` with ``retryable`` set for timeouts, connection errors, HTTP 429 /
5xx and node rate / range limits.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping
from typing import Any

import httpx

from ...exceptions import ChainIntegrationError

BlockParam = int | str | None
BLOCK_TAGS = frozenset({"latest", "earliest", "pending", "safe", "finalized"})
DEFAULT_MAX_LOG_RANGE = 5_000


def to_hex(value: int) -> str:
    """An int as a JSON-RPC quantity (``0x0``, ``0x1a``)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"not a non-negative integer quantity: {value!r}")
    return hex(value)


def from_hex(value: Any) -> int:
    """A JSON-RPC quantity (hex string, or already an int) as an int."""
    if isinstance(value, bool):
        raise ValueError(f"not a quantity: {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value[:2].lower() == "0x":
        return int(value, 16) if len(value) > 2 else 0
    raise ValueError(f"not a hex quantity: {value!r}")


def block_param(block: BlockParam, default: int | str = "latest") -> str:
    """Normalize a block number / tag / hex quantity to the JSON-RPC block parameter."""
    if block is None:
        block = default
    if isinstance(block, int) and not isinstance(block, bool):
        return to_hex(block)
    if isinstance(block, str):
        if block in BLOCK_TAGS:
            return block
        if block[:2].lower() == "0x":
            return to_hex(from_hex(block))
    raise ValueError(f"invalid block parameter {block!r}")


def _rpc_error(method: str, error: Any) -> ChainIntegrationError:
    if not isinstance(error, Mapping):
        return ChainIntegrationError(f"{method}: {error!r}", code="rpc_error")
    code = error.get("code")
    message = str(error.get("message", ""))
    lowered = message.lower()
    text = f"{method}: JSON-RPC error {code}: {message}"
    if code == 3 or "revert" in lowered:
        return ChainIntegrationError(text, code="reverted")
    if code in (-32005, 429) or "limit exceeded" in lowered or "rate limit" in lowered:
        return ChainIntegrationError(text, code="rpc_limit", retryable=True)
    if "timeout" in lowered or "timed out" in lowered:
        return ChainIntegrationError(text, code="timeout", retryable=True)
    return ChainIntegrationError(text, code="rpc_error")


class JsonRpcClient:
    """Async JSON-RPC client. Pass ``client=`` to reuse (or mock) an ``httpx.AsyncClient``."""

    def __init__(
        self,
        url: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 15.0,
        block: int | str = "latest",
        max_log_range: int | None = DEFAULT_MAX_LOG_RANGE,
        headers: Mapping[str, str] | None = None,
    ):
        block_param(block)  # validate early
        if max_log_range is not None and max_log_range < 1:
            raise ValueError("max_log_range must be >= 1")
        self.url = url
        self.block = block
        self.max_log_range = max_log_range
        self._headers = dict(headers or {})
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._ids = itertools.count(1)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> JsonRpcClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    def block_param(self, block: BlockParam = None) -> str:
        return block_param(block, self.block)

    async def request(self, method: str, params: list[Any]) -> Any:
        """One JSON-RPC call; returns ``result`` or raises ``ChainIntegrationError``."""
        payload = {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params}
        try:
            response = await self._client.post(self.url, json=payload, headers=self._headers)
        except httpx.TimeoutException as exc:
            raise ChainIntegrationError(
                f"{method}: RPC timeout", code="timeout", retryable=True
            ) from exc
        except httpx.TransportError as exc:
            raise ChainIntegrationError(
                f"{method}: RPC transport error ({type(exc).__name__})",
                code="transport",
                retryable=True,
            ) from exc
        status = response.status_code
        if status == 429:
            raise ChainIntegrationError(f"{method}: HTTP 429", code="rate_limited", retryable=True)
        if status >= 500:
            raise ChainIntegrationError(
                f"{method}: HTTP {status}", code="http_error", retryable=True
            )
        if status >= 400:
            raise ChainIntegrationError(f"{method}: HTTP {status}", code="http_error")
        try:
            body = response.json()
        except ValueError as exc:
            raise ChainIntegrationError(
                f"{method}: response is not JSON", code="bad_response"
            ) from exc
        if not isinstance(body, Mapping):
            raise ChainIntegrationError(f"{method}: unexpected response", code="bad_response")
        if body.get("error") is not None:
            raise _rpc_error(method, body["error"])
        if "result" not in body:
            raise ChainIntegrationError(f"{method}: response has no result", code="bad_response")
        return body["result"]

    # ------------------------------------------------------------------ eth_*

    async def eth_call(self, to: str, data: str, block: BlockParam = None) -> str:
        result = await self.request(
            "eth_call", [{"to": to, "data": data}, self.block_param(block)]
        )
        if not isinstance(result, str):
            raise ChainIntegrationError("eth_call: result is not hex", code="bad_response")
        return result

    async def eth_getTransactionReceipt(self, tx_hash: str) -> dict[str, Any] | None:
        return await self.request("eth_getTransactionReceipt", [tx_hash])

    async def eth_getTransactionByHash(self, tx_hash: str) -> dict[str, Any] | None:
        return await self.request("eth_getTransactionByHash", [tx_hash])

    async def eth_getBalance(self, address: str, block: BlockParam = None) -> int:
        return from_hex(await self.request("eth_getBalance", [address, self.block_param(block)]))

    async def eth_blockNumber(self) -> int:
        return from_hex(await self.request("eth_blockNumber", []))

    async def eth_getBlockByNumber(
        self, block: BlockParam = None, full: bool = False
    ) -> dict[str, Any] | None:
        return await self.request("eth_getBlockByNumber", [self.block_param(block), full])

    async def head(self) -> int:
        """The pinned block number, or the node's current block."""
        if isinstance(self.block, int):
            return self.block
        if self.block == "latest":
            return await self.eth_blockNumber()
        block = await self.eth_getBlockByNumber(self.block)
        if not block:
            raise ChainIntegrationError(f"no {self.block} block", code="bad_response")
        return from_hex(block["number"])

    async def _resolve(self, block: BlockParam) -> int:
        if block is None:
            return await self.head()
        if isinstance(block, int):
            return block
        if block == "earliest":
            return 0
        if block in BLOCK_TAGS:
            if block == "latest" and isinstance(self.block, str):
                return await self.eth_blockNumber()
            found = await self.eth_getBlockByNumber(block)
            if not found:
                raise ChainIntegrationError(f"no {block} block", code="bad_response")
            return from_hex(found["number"])
        return from_hex(block)

    async def eth_getLogs(self, filter: Mapping[str, Any]) -> list[dict[str, Any]]:
        """``eth_getLogs``, split into ``max_log_range``-block chunks when the range is wider.

        A missing ``toBlock`` means the pinned block; a missing ``fromBlock`` means ``toBlock``.
        """
        query = dict(filter)
        if "blockHash" in query:
            return list(await self.request("eth_getLogs", [query]) or [])
        to_raw = query.pop("toBlock", None)
        from_raw = query.pop("fromBlock", None)
        if self.max_log_range is None:
            query["toBlock"] = self.block_param(to_raw)
            query["fromBlock"] = block_param(from_raw, query["toBlock"])
            return list(await self.request("eth_getLogs", [query]) or [])
        end = await self._resolve(to_raw)
        start = end if from_raw is None else await self._resolve(from_raw)
        logs: list[dict[str, Any]] = []
        while start <= end:
            stop = min(end, start + self.max_log_range - 1)
            chunk = {**query, "fromBlock": to_hex(start), "toBlock": to_hex(stop)}
            logs.extend(await self.request("eth_getLogs", [chunk]) or [])
            start = stop + 1
        return logs


__all__ = ["BLOCK_TAGS", "JsonRpcClient", "block_param", "from_hex", "to_hex"]
