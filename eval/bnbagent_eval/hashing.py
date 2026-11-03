"""Canonical JSON and keccak-256 hashing.

Rules for *our* hashes: sorted keys, compact separators, UTF-8 (``ensure_ascii=False``),
floats quantized to 6 decimals, NaN/Infinity rejected.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

from eth_hash.auto import keccak

FLOAT_DECIMALS = 6

__all__ = ["canonical_json", "keccak_hex", "hash_obj", "quantize"]


def quantize(value: float) -> float:
    """Round a float to the canonical precision; reject NaN and Infinity."""
    if math.isnan(value) or math.isinf(value):
        raise ValueError("NaN and Infinity are not allowed in canonical JSON")
    q = round(value, FLOAT_DECIMALS)
    return 0.0 if q == 0 else q  # normalize -0.0


def _normalize(obj: Any) -> Any:
    if isinstance(obj, bool) or obj is None or isinstance(obj, (int, str)):
        return obj
    if isinstance(obj, float):
        return quantize(obj)
    if isinstance(obj, Mapping):
        out = {}
        for k, v in obj.items():
            if not isinstance(k, str):
                raise TypeError(f"canonical JSON keys must be strings, got {type(k).__name__}")
            out[k] = _normalize(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [_normalize(v) for v in obj]
    if isinstance(obj, (bytes, bytearray)):
        return "0x" + bytes(obj).hex()
    raise TypeError(f"object of type {type(obj).__name__} is not canonical-JSON serializable")


def canonical_json(obj: Any) -> str:
    """Serialize ``obj`` (JSON-compatible values only) to canonical JSON text."""
    return json.dumps(
        _normalize(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def keccak_hex(data: bytes | str) -> str:
    """keccak-256 of bytes (or UTF-8 text) as a 0x-prefixed hex string."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return "0x" + keccak(data).hex()


def hash_obj(obj: Any) -> str:
    """keccak-256 of the canonical JSON of ``obj``."""
    return keccak_hex(canonical_json(obj))
