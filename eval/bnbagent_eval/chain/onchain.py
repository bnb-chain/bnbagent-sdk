"""Tx step enrichment: receipts from a ``ChainReaderPort`` and calldata decoding.

``enrich`` returns a new Session whose Tx steps carry receipt data (status, gas used, block,
logs, sender) and a decoded call. ERC-20 calls are decoded from their selectors; other calls
need an ABI for the ``to`` address and ``eth-abi`` (``[evm]``). With ``block`` set, receipts
mined after that block are ignored, so re-running later gives the same session.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from typing import Any

from .._optional import require
from ..hashing import keccak_hex
from ..metrics.onchain._evm import decode_erc20, to_int
from ..ports import ChainReaderPort
from ..types.trajectory import DecodedCall, Session, TxStep

__all__ = ["abi_decode", "enrich"]


def _abi_type(param: Mapping[str, Any]) -> str:
    typ = str(param["type"])
    if typ.startswith("tuple"):
        inner = ",".join(_abi_type(c) for c in param.get("components", ()))
        return f"({inner}){typ[len('tuple') :]}"
    return typ


def _plain(value: Any) -> Any:
    """JSON-friendly decoded values: bytes as 0x hex, tuples as lists."""
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, str) and value.startswith("0x"):
        return value.lower()
    return value


def abi_decode(calldata: str, abi: Sequence[Mapping[str, Any]]) -> DecodedCall | None:
    """Decode ``calldata`` against the function entries of ``abi`` (needs ``eth-abi``)."""
    data = calldata.lower().removeprefix("0x")
    if len(data) < 8:
        return None
    for entry in abi:
        if entry.get("type", "function") != "function" or "name" not in entry:
            continue
        inputs = list(entry.get("inputs", ()))
        types = [_abi_type(p) for p in inputs]
        signature = f"{entry['name']}({','.join(types)})"
        if keccak_hex(signature)[2:10] != data[:8]:
            continue
        eth_abi = require("eth_abi", "evm")
        values = eth_abi.decode(types, bytes.fromhex(data[8:]))
        args = {
            (p.get("name") or f"arg{i}"): _plain(v)
            for i, (p, v) in enumerate(zip(inputs, values, strict=True))
        }
        return DecodedCall(function=str(entry["name"]), args=args)
    return None


def _receipt_fields(receipt: Mapping[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key, name in (
        ("status", "status"),
        ("gasUsed", "gas_used"),
        ("blockNumber", "block_number"),
    ):
        value = to_int(receipt.get(key))
        if value is not None:
            fields[name] = value
    if receipt.get("from"):
        fields["from_address"] = str(receipt["from"]).lower()
    if receipt.get("logs") is not None:
        fields["logs"] = tuple(dict(log) for log in receipt["logs"])
    return fields


async def _enrich_step(
    step: TxStep,
    reader: ChainReaderPort | None,
    abis: Mapping[str, Sequence[Mapping[str, Any]]],
    block: int | None,
) -> TxStep:
    changes: dict[str, Any] = {}
    if reader is not None and step.tx_hash:
        receipt = await reader.get_receipt(step.tx_hash)
        mined = to_int(receipt.get("blockNumber")) if receipt else None
        if receipt and (block is None or (mined is not None and mined <= block)):
            changes.update(_receipt_fields(receipt))
            if step.calldata in ("", "0x") or not (step.from_address or "from_address" in changes):
                tx = await reader.get_transaction(step.tx_hash)
                if tx:
                    if step.calldata in ("", "0x") and tx.get("input"):
                        changes["calldata"] = str(tx["input"])
                    if not (step.from_address or changes.get("from_address")) and tx.get("from"):
                        changes["from_address"] = str(tx["from"]).lower()
    if step.decoded is None or not step.decoded.function:
        calldata = changes.get("calldata", step.calldata)
        abi = next((v for k, v in abis.items() if k.lower() == step.to.lower()), None)
        decoded = abi_decode(calldata, abi) if abi else None
        decoded = decoded or decode_erc20(calldata)
        if decoded is not None:
            changes["decoded"] = decoded
    return dataclasses.replace(step, **changes) if changes else step


async def enrich(
    session: Session,
    reader: ChainReaderPort | None,
    *,
    abis: dict[str, list] | None = None,
    block: int | None = None,
) -> Session:
    """A copy of ``session`` with Tx steps enriched from receipts and decoded calldata."""
    abis = abis or {}
    traces = []
    for trace in session.traces:
        steps = []
        for step in trace.steps:
            if isinstance(step, TxStep):
                step = await _enrich_step(step, reader, abis, block)
            steps.append(step)
        traces.append(dataclasses.replace(trace, steps=tuple(steps)))
    return dataclasses.replace(session, traces=tuple(traces))
