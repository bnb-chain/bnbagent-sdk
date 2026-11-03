"""Edge cases of the onchain.* helpers and intent matching (coverage gate)."""

from __future__ import annotations

import pytest

from bnbagent_eval.metrics.onchain._evm import (
    arg,
    decode_erc20,
    to_int,
    token_transfer_amount,
)
from bnbagent_eval.metrics.onchain.actions import _values_match, expected_tx_calls
from bnbagent_eval.types import DecodedCall

from .test_onchain import AGENT, ROUTER, TOKEN, calldata, case, run, tx


def test_to_int_rejects_non_numeric_types() -> None:
    assert to_int(None) is None and to_int([1]) is None and to_int(1.5) is None


def test_decode_erc20_rejects_non_hex_words() -> None:
    assert decode_erc20("0x095ea7b3" + "zz" * 64) is None


def test_arg_unknown_name_and_transfer_amount_of_other_calls() -> None:
    assert arg(DecodedCall(function="f", args={"x": 1}), "spender") is None
    approve = tx(calldata=calldata("0x095ea7b3", ROUTER, 5))
    assert token_transfer_amount(approve, TOKEN) == 0


@pytest.mark.parametrize(
    ("expected", "actual", "tol", "ok"),
    [
        (1.5, 1.5, 0.0, True),  # floats that are not ints
        (1.0, 1.02, 0.05, True),
        ("0xAbZ", "0xabz", 0.0, True),  # non-numeric 0x strings: case-insensitive
        ("swap", "swap", 0.0, True),
        ("swap", "Swap", 0.0, False),  # plain strings: exact
        ([1, "0xAa"], [1, "0xaa"], 0.0, True),
        ([1, 2], [1], 0.0, False),
        ({"a": 1, "b": [2]}, {"a": 1, "b": [2]}, 0.0, True),
        ({"a": 1}, {"a": 1, "b": 2}, 0.0, False),
        (None, None, 0.0, True),  # falls back to canonical JSON
        (True, None, 0.0, False),
    ],
)
def test_values_match(expected, actual, tol, ok) -> None:
    assert _values_match(expected, actual, tol) is ok


def test_expected_calls_must_be_mappings() -> None:
    with pytest.raises(ValueError, match="mapping"):
        expected_tx_calls(["approve"])


async def test_intent_match_contract_and_function_mismatch() -> None:
    decoded = DecodedCall(contract="Router", function="swap", args={"amountIn": 1})
    step = tx(to=ROUTER, decoded=decoded)
    good = case(step, expected=[{"to": ROUTER, "contract": "Router", "function": "swap"}])
    assert (await run("onchain.intent_match", good, {"compare_args": "ignore"})).score == 1.0
    wrong_contract = case(step, expected=[{"contract": "Pool", "function": "swap"}])
    assert (await run("onchain.intent_match", wrong_contract)).score == 0.0
    wrong_fn = case(step, expected=[{"function": "swapExact(uint256)"}])
    assert (await run("onchain.intent_match", wrong_fn)).score == 0.0
    no_call = case(tx(1, to=AGENT), expected=[{"contract": "Router"}])
    assert (await run("onchain.intent_match", no_call)).score == 0.0


async def test_intent_match_any_order_tolerance_is_a_one_to_one_assignment() -> None:
    # 1000 is within 1.1% of both expected amounts, 1012 only of 1010: a first-match scan gives
    # 1000 to the 1010 call and leaves nothing for 990.
    expected = [{"function": "swap", "args": {"amount_in": a}} for a in (1010, 990)]
    steps = [
        tx(i, decoded=DecodedCall(function="swap", args={"amount_in": a}))
        for i, a in enumerate((1000, 1012))
    ]
    params = {"match_type": "any_order", "compare_args": "subset", "arg_tolerance": 0.011}
    res = await run("onchain.intent_match", case(*steps, expected=expected), params)
    assert res.score == 1.0 and res.details["missing"] == []
    res = await run("onchain.intent_match", case(steps[1], expected=expected), params)
    assert res.score == 0.0 and res.details["missing"] == [1]
