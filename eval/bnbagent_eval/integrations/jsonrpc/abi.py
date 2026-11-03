"""Minimal ABI handling over the vendored fragments: selectors, calldata, results, events.

Encoding and decoding use ``eth-abi`` (the ``[evm]`` extra), imported lazily so the JSON-RPC
client and receipt / balance reads work without it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from functools import cache
from importlib import resources
from typing import Any

from ..._optional import require
from ...exceptions import ChainIntegrationError
from ...hashing import keccak_hex


def _eth_abi() -> Any:
    return require("eth_abi", "evm")


def type_string(param: Mapping[str, Any]) -> str:
    """Canonical ABI type of a parameter (tuples expanded)."""
    kind = param["type"]
    if kind.startswith("tuple"):
        inner = ",".join(type_string(c) for c in param["components"])
        return f"({inner}){kind[len('tuple') :]}"
    return kind


def _hex_bytes(data: str) -> bytes:
    text = data[2:] if data.startswith(("0x", "0X")) else data
    try:
        return bytes.fromhex(text)
    except ValueError as exc:
        raise ChainIntegrationError(
            f"malformed hex data {data[:20]!r}", code="bad_response"
        ) from exc


class Abi:
    """One contract's vendored fragment list."""

    def __init__(self, name: str, entries: Sequence[Mapping[str, Any]]):
        self.name = name
        self.functions = {e["name"]: e for e in entries if e["type"] == "function"}
        self.events = {e["name"]: e for e in entries if e["type"] == "event"}

    def _fn(self, fn: str) -> Mapping[str, Any]:
        try:
            return self.functions[fn]
        except KeyError:
            raise KeyError(f"{self.name} ABI has no function {fn!r}") from None

    def input_types(self, fn: str) -> list[str]:
        return [type_string(p) for p in self._fn(fn)["inputs"]]

    def output_types(self, fn: str) -> list[str]:
        return [type_string(p) for p in self._fn(fn)["outputs"]]

    def signature(self, fn: str) -> str:
        return f"{fn}({','.join(self.input_types(fn))})"

    def selector(self, fn: str) -> str:
        """0x-prefixed 4-byte function selector."""
        return keccak_hex(self.signature(fn))[:10]

    def encode_call(self, fn: str, *args: Any) -> str:
        """0x-prefixed calldata for ``fn(*args)``."""
        encoded = _eth_abi().encode(self.input_types(fn), list(args))
        return self.selector(fn) + encoded.hex()

    def decode_output(self, fn: str, data: str) -> tuple[Any, ...]:
        raw = _hex_bytes(data)
        if not raw and self.output_types(fn):
            raise ChainIntegrationError(
                f"{self.name}.{fn} returned no data (no contract at the address?)",
                code="empty_result",
            )
        try:
            return tuple(_eth_abi().decode(self.output_types(fn), raw))
        except Exception as exc:  # eth-abi raises several decoding error types
            raise ChainIntegrationError(
                f"cannot decode {self.name}.{fn} result: {exc}", code="bad_response"
            ) from exc

    # ------------------------------------------------------------------ events

    def event_topic(self, event: str) -> str:
        entry = self.events[event]
        types = ",".join(type_string(p) for p in entry["inputs"])
        return keccak_hex(f"{event}({types})")

    def decode_event(self, event: str, log: Mapping[str, Any]) -> dict[str, Any]:
        """Decode a raw log's data and value-type indexed topics into ``{name: value}``."""
        entry = self.events[event]
        inputs = entry["inputs"]
        abi = _eth_abi()
        topics = list(log.get("topics") or [])[1:]
        out: dict[str, Any] = {}
        indexed = [p for p in inputs if p.get("indexed")]
        for param, topic in zip(indexed, topics, strict=False):
            out[param["name"]] = abi.decode([type_string(param)], _hex_bytes(topic))[0]
        plain = [p for p in inputs if not p.get("indexed")]
        values = abi.decode([type_string(p) for p in plain], _hex_bytes(log.get("data", "0x")))
        out.update({p["name"]: v for p, v in zip(plain, values, strict=True)})
        return out


@cache
def load(name: str) -> Abi:
    """Load ``abis/<name>.json`` from the package."""
    path = resources.files(__package__).joinpath("abis").joinpath(f"{name}.json")
    return Abi(name, json.loads(path.read_text(encoding="utf-8")))


def topic_uint(value: int) -> str:
    """A uint256 as a 32-byte log topic."""
    return "0x" + int(value).to_bytes(32, "big").hex()


IDENTITY = "IdentityRegistry"
COMMERCE = "AgenticCommerce"
POLICY = "OptimisticPolicy"
ROUTER = "EvaluatorRouter"
ERC20 = "ERC20"
