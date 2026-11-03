from __future__ import annotations

import pytest

from bnbagent_eval.hashing import canonical_json, hash_obj, keccak_hex, quantize


def test_keccak_known_vector() -> None:
    assert keccak_hex(b"") == "0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"


def test_key_order_independent() -> None:
    assert canonical_json({"b": 1, "a": [1, {"d": 2, "c": 3}]}) == '{"a":[1,{"c":3,"d":2}],"b":1}'
    assert hash_obj({"x": 1, "y": 2}) == hash_obj({"y": 2, "x": 1})


def test_float_quantization() -> None:
    assert hash_obj({"s": 0.1 + 0.2}) == hash_obj({"s": 0.3})
    assert canonical_json(-0.0) == "0.0"
    assert quantize(0.1234567) == 0.123457


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nan_and_infinity_rejected(bad: float) -> None:
    with pytest.raises(ValueError):
        canonical_json({"x": bad})


def test_unicode_preserved() -> None:
    assert canonical_json({"t": "代理 ✓"}) == '{"t":"代理 ✓"}'


def test_bytes_hex_and_bad_types() -> None:
    assert canonical_json(b"\x01\xff") == '"0x01ff"'
    with pytest.raises(TypeError):
        canonical_json({1: "int key"})
    with pytest.raises(TypeError):
        canonical_json(object())
