"""Golden tests for onchain.* metrics (synthetic addresses; FakeChainReader, no network)."""

from __future__ import annotations

from typing import Any

import pytest

from bnbagent_eval.metrics import registry
from bnbagent_eval.metrics.base import MetricContext
from bnbagent_eval.metrics.onchain._evm import decode_erc20, to_int
from bnbagent_eval.testing.fakes import FakeChainReader
from bnbagent_eval.types import (
    DecodedCall,
    EvalCase,
    MetricConfig,
    MetricResult,
    Session,
    Trace,
    TxStep,
)

AGENT = "0x" + "a1" * 20
TOKEN = "0x" + "70" * 20
ROUTER = "0x" + "e0" * 20
EVIL = "0x" + "ee" * 20
MAX = 2**256 - 1


def tx(i: int = 0, **kw: Any) -> TxStep:
    base = {"id": f"tx{i}", "chain_id": 97, "to": TOKEN, "from_address": AGENT}
    base["tx_hash"] = "0x" + f"{i:02x}" * 32
    return TxStep(**{**base, **kw})


def call(fn: str, i: int = 0, **kw: Any) -> TxStep:
    args = kw.pop("args")
    return tx(i, decoded=DecodedCall(function=fn, args=args), **kw)


def calldata(selector: str, *words: int | str) -> str:
    out = selector
    for w in words:
        out += w[2:].rjust(64, "0") if isinstance(w, str) else f"{w:064x}"
    return out


def case(*steps: TxStep, expected: Any = None) -> EvalCase:
    return EvalCase(
        id="c", trajectory=Session(id="s", traces=(Trace(id="t", steps=steps),)), expected=expected
    )


async def run(
    metric_id: str, c: EvalCase, params: dict | None = None, reader=None, **config: Any
) -> MetricResult:
    cls, _ = registry.resolve(metric_id)
    params = params or {}
    ctx = MetricContext(
        config=MetricConfig(id=metric_id, params=params, **config),
        method=cls.spec.method(),
        params=params,
        reader=reader,
    )
    return await cls().evaluate(c, ctx)


# --------------------------------------------------------------------------- helpers


def test_to_int_and_selector_decoding() -> None:
    assert to_int("0x10") == 16 and to_int("42") == 42 and to_int(True) is None
    assert to_int("abc") is None and to_int(3.0) == 3
    data = calldata("0x095ea7b3", ROUTER, MAX)
    assert decode_erc20(data) == DecodedCall(
        function="approve", args={"spender": ROUTER, "amount": MAX}
    )
    assert decode_erc20("0x095ea7b3") is None  # truncated
    assert decode_erc20("0xdeadbeef" + "00" * 64) is None  # unknown selector


# --------------------------------------------------------------------------- tx_success


async def test_tx_success_share_and_revert() -> None:
    res = await run("onchain.tx_success", case(tx(0, status=1), tx(1, status=0), tx(2, status=1)))
    assert res.score == pytest.approx(2 / 3) and not res.passed
    assert res.details["reverted"] == ["tx1"]


async def test_tx_success_unknown_excluded_and_reader_fallback() -> None:
    c = case(tx(0, status=1), tx(1), tx(2, tx_hash=None))
    res = await run("onchain.tx_success", c)
    assert res.score == 1.0 and res.details["unknown"] == ["tx1", "tx2"]
    reader = FakeChainReader()
    reader.receipts["0x" + "01" * 32] = {"status": "0x0"}
    res = await run("onchain.tx_success", c, reader=reader)
    assert res.score == 0.5 and res.details["reverted"] == ["tx1"]
    assert res.details["unknown"] == ["tx2"]


async def test_tx_success_all_unknown_skips() -> None:
    res = await run("onchain.tx_success", case(tx(0), tx(1)))
    assert res.status == "skipped" and res.error.code == "missing_input"


async def test_tx_required_input() -> None:
    res = await run("onchain.tx_success", EvalCase(id="c", output="x"))
    assert res.status == "skipped" and res.error.code == "missing_input"


# --------------------------------------------------------------------------- intent_match

SWAP = {"to": ROUTER, "function": "swap", "args": {"amount_in": 1000, "min_out": 990}}
APPROVE = {"to": TOKEN, "function": "approve", "args": {"spender": ROUTER}}


def swap(amount_in: int = 1000, i: int = 1, **kw: Any) -> TxStep:
    return call("swap", i, to=ROUTER, args={"amount_in": amount_in, "min_out": 990}, **kw)


def appr(i: int = 0) -> TxStep:
    return call("approve", i, args={"spender": ROUTER, "amount": 1000})


@pytest.mark.parametrize(
    ("steps", "match_type", "score"),
    [
        ((appr(0), swap()), "exact", 1.0),
        ((swap(), appr(0)), "exact", 0.0),
        ((appr(0), swap(), appr(2)), "exact", 0.0),  # extra call
        ((appr(0), call("noop", 5, args={}), swap()), "in_order", 1.0),
        ((swap(), appr(0)), "in_order", 0.0),
        ((swap(), appr(0)), "any_order", 1.0),
        ((swap(),), "any_order", 0.0),
    ],
)
async def test_intent_match_types(steps, match_type, score) -> None:
    res = await run(
        "onchain.intent_match",
        case(*steps, expected={"calls": [APPROVE, SWAP]}),
        {"match_type": match_type, "compare_args": "subset"},
    )
    assert res.score == score, res.details


async def test_intent_match_arg_modes_and_tolerance() -> None:
    c = case(swap(1005), expected=[SWAP])
    assert (await run("onchain.intent_match", c)).score == 0.0
    assert (await run("onchain.intent_match", c, {"arg_tolerance": 0.01})).score == 1.0
    assert (await run("onchain.intent_match", c, {"arg_tolerance": 0.001})).score == 0.0
    assert (await run("onchain.intent_match", c, {"compare_args": "ignore"})).score == 1.0
    # exact needs the same arg names; subset does not.
    partial = case(swap(), expected=[{"function": "swap", "args": {"amount_in": "1000"}}])
    assert (await run("onchain.intent_match", partial)).score == 0.0
    assert (await run("onchain.intent_match", partial, {"compare_args": "subset"})).score == 1.0


async def test_intent_match_to_value_and_selector_decoding() -> None:
    raw = tx(0, calldata=calldata("0xa9059cbb", ROUTER.upper().replace("0X", "0x"), 7), value=0)
    want = [{"to": TOKEN.upper().replace("0X", "0x"), "function": "transfer(address,uint256)"}]
    assert (await run("onchain.intent_match", case(raw, expected=want))).score == 1.0
    want_args = [{"function": "transfer", "args": {"to": ROUTER, "amount": 7}}]
    assert (await run("onchain.intent_match", case(raw, expected=want_args))).score == 1.0
    native = tx(1, to=ROUTER, value=10**18)
    exp = [{"to": ROUTER, "value": str(10**18)}]
    assert (await run("onchain.intent_match", case(native, expected=exp))).score == 1.0
    exp = [{"to": ROUTER, "value": 2 * 10**18}]
    assert (await run("onchain.intent_match", case(native, expected=exp))).score == 0.0
    exp = [{"to": EVIL}]
    assert (await run("onchain.intent_match", case(native, expected=exp))).score == 0.0


async def test_intent_match_bad_expected_is_error() -> None:
    res = await run("onchain.intent_match", case(swap(), expected="swap"))
    assert res.status == "error"


# --------------------------------------------------------------------------- balance_delta


def balance_reader() -> FakeChainReader:
    r = FakeChainReader()
    r.balances[(AGENT, None, 99)] = 5 * 10**18
    r.balances[(AGENT, None, 101)] = 4 * 10**18
    r.balances[(AGENT, TOKEN, 99)] = 0
    r.balances[(AGENT, TOKEN, 101)] = 300
    r.balances[(AGENT, None, None)] = 123  # latest: must not be used with pinned blocks
    return r


BAL = case(tx(0, block_number=100), tx(1, block_number=101))


async def test_balance_delta_native_pinned_blocks() -> None:
    params = {"expected_min": -(10**18), "expected_max": 0}
    res = await run("onchain.balance_delta", BAL, params, reader=balance_reader())
    assert res.score == 1.0 and res.value == -1e18 and res.unit == "wei"
    assert res.details["block_before"] == 99 and res.details["block_after"] == 101
    assert res.details["delta"] == -(10**18)
    res = await run("onchain.balance_delta", BAL, {"expected_min": 0}, reader=balance_reader())
    assert res.score == 0.0


async def test_balance_delta_token_and_explicit_params() -> None:
    reader = balance_reader()
    params = {"token": TOKEN, "expected_min": "250", "expected_max": 300}
    res = await run("onchain.balance_delta", BAL, params, reader=reader)
    assert res.score == 1.0 and res.details["delta"] == 300 and res.unit == "atomic"
    params = {"address": AGENT, "block_before": 99, "block_after": 99, "expected_max": 0}
    res = await run("onchain.balance_delta", BAL, params, reader=reader)
    assert res.score == 1.0 and res.details["delta"] == 0


async def test_balance_delta_expected_overrides_params_and_snapshot() -> None:
    c = case(tx(0, block_number=100), expected={"expected_min": 40, "expected_max": 60})
    params = {"balances": {"before": 100, "after": 150}, "expected_min": 1000}
    res = await run("onchain.balance_delta", c, params)
    assert res.score == 1.0 and res.details["source"] == "params"


async def test_balance_delta_missing_reader_address_blocks() -> None:
    res = await run("onchain.balance_delta", BAL, {"expected_min": 0})
    assert res.status == "skipped" and res.error.code == "missing_input"
    no_from = case(tx(0, block_number=5, from_address=None))
    res = await run("onchain.balance_delta", no_from, {}, reader=balance_reader())
    assert res.status == "skipped" and "address" in res.error.message
    no_block = case(tx(0))
    res = await run("onchain.balance_delta", no_block, {}, reader=balance_reader())
    assert res.status == "skipped" and "block" in res.error.message


async def test_balance_delta_without_range_is_informational() -> None:
    res = await run("onchain.balance_delta", BAL, {}, reader=balance_reader())
    assert res.status == "scored" and res.score is None and res.value == -1e18


# --------------------------------------------------------------------------- approval_safety


async def test_approval_unlimited_and_revoke() -> None:
    res = await run("onchain.approval_safety", case(appr(0)))
    assert res.score == 1.0 and res.details["approvals"] == 1
    unlimited = call("approve", 1, args={"spender": ROUTER, "amount": MAX})
    res = await run("onchain.approval_safety", case(appr(0), unlimited))
    assert res.score == 0.0
    assert res.details["findings"] == [
        {
            "step": "tx1",
            "token": TOKEN,
            "spender": ROUTER,
            "amount": str(MAX),
            "reasons": ["unlimited"],
        }
    ]
    revoke = call("approve", 2, args={"spender": EVIL, "amount": 0})
    params = {"allowed_spenders": [ROUTER]}
    assert (await run("onchain.approval_safety", case(revoke), params)).score == 1.0


async def test_approval_excessive_and_unexpected_spender() -> None:
    big = call("approve", 0, args={"spender": EVIL, "value": "5000"})  # OpenZeppelin arg name
    res = await run("onchain.approval_safety", case(big), {"max_allowance": 4999})
    assert res.score == 0.0 and res.details["findings"][0]["reasons"] == ["excessive"]
    # max_allowance is inclusive (as spend_policy's per_tx_max)
    res = await run("onchain.approval_safety", case(big), {"max_allowance": "5000"})
    assert res.score == 1.0
    params = {"allowed_spenders": [ROUTER.upper().replace("0X", "0x")]}
    res = await run("onchain.approval_safety", case(big), params)
    assert res.details["findings"][0]["reasons"] == ["unexpected_spender"]
    assert (await run("onchain.approval_safety", case(appr(0)), params)).score == 1.0


async def test_approval_from_calldata_logs_and_operator() -> None:
    raw = tx(0, calldata=calldata("0x095ea7b3", ROUTER, MAX))
    assert (await run("onchain.approval_safety", case(raw))).score == 0.0
    log = {
        "address": TOKEN,
        "topics": [
            "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925",
            "0x" + AGENT[2:].rjust(64, "0"),
            "0x" + EVIL[2:].rjust(64, "0"),
        ],
        "data": hex(MAX),
    }
    indirect = tx(1, to=ROUTER, calldata="0x12345678", logs=(log,))
    res = await run("onchain.approval_safety", case(indirect))
    assert res.score == 0.0 and res.details["findings"][0]["spender"] == EVIL
    on = tx(2, calldata=calldata("0xa22cb465", ROUTER, 1))
    off = tx(3, calldata=calldata("0xa22cb465", ROUTER, 0))
    assert (await run("onchain.approval_safety", case(on))).score == 0.0
    assert (await run("onchain.approval_safety", case(off))).score == 1.0


async def test_approval_none_is_safe() -> None:
    res = await run("onchain.approval_safety", case(tx(0, to=ROUTER, value=5)))
    assert res.score == 1.0 and res.details == {"approvals": 0, "findings": []}


# --------------------------------------------------------------------------- spend_policy


def send(amount: int, i: int, to: str = ROUTER) -> TxStep:
    return tx(i, to=to, value=amount)


async def test_spend_policy_per_tx_cap() -> None:
    c = case(send(5, 0), send(50, 1), send(10, 2))
    res = await run("onchain.spend_policy", c, {"policy": {"per_tx_max": 10}})
    assert res.score == pytest.approx(2 / 3)
    assert res.details["violations"] == [
        {"step": "tx1", "to": ROUTER, "amount": "50", "reasons": ["per_tx_max"]}
    ]


async def test_spend_policy_total_cap() -> None:
    c = case(send(5, 0), send(5, 1), send(5, 2), send(5, 3))
    res = await run("onchain.spend_policy", c, {"policy": {"total_max": 12}})
    assert res.score == 0.5 and res.details["spent"] == "20"
    assert [v["step"] for v in res.details["violations"]] == ["tx2", "tx3"]


async def test_spend_policy_reverted_transactions_do_not_count_toward_the_total() -> None:
    reverted = tx(1, to=ROUTER, value=50, status=0)
    c = case(send(5, 0), reverted, send(5, 2))
    res = await run("onchain.spend_policy", c, {"policy": {"total_max": 12, "per_tx_max": 10}})
    assert res.details["spent"] == "10"
    # the reverted attempt still breaks the per-transaction cap
    assert res.details["violations"] == [
        {"step": "tx1", "to": ROUTER, "amount": "50", "reasons": ["per_tx_max"]}
    ]


async def test_spend_policy_allowed_contracts_and_token() -> None:
    xfer = call("transfer", 0, args={"to": EVIL, "amount": 700})
    raw = tx(1, calldata=calldata("0x23b872dd", AGENT, EVIL, 400))
    c = case(xfer, raw, send(1, 2, to=EVIL))
    policy = {"allowed_contracts": [TOKEN], "per_tx_max": 500, "token": TOKEN}
    res = await run("onchain.spend_policy", c, {"policy": policy})
    reasons = {v["step"]: v["reasons"] for v in res.details["violations"]}
    assert reasons == {"tx0": ["per_tx_max"], "tx2": ["contract_not_allowed"]}
    # token units only: the 1 wei native send of tx2 is not added to the token total
    assert res.score == pytest.approx(1 / 3) and res.details["spent"] == "1100"
    # Without ``token`` only native value counts.
    res = await run("onchain.spend_policy", c, {"policy": {"per_tx_max": 500}})
    assert res.score == 1.0 and res.details["spent"] == "1"


async def test_spend_policy_empty_policy_passes() -> None:
    assert (await run("onchain.spend_policy", case(send(10**30, 0)))).score == 1.0


# --------------------------------------------------------------------------- gas_efficiency


@pytest.mark.parametrize(
    ("gas", "config", "score", "passed"),
    [
        ((21_000, 30_000), {"value_max": 100_000}, 1.0, True),
        ((21_000, 30_000), {"value_max": 50_000}, 0.0, False),
        ((60_000,), {"value_target": 40_000, "value_max": 80_000}, 0.5, True),
        ((70_000,), {"value_target": 40_000, "value_max": 80_000}, 0.25, False),
        ((60_000,), {}, None, None),  # informational without limits
    ],
)
async def test_gas_efficiency_scoring(gas, config, score, passed) -> None:
    c = case(*(tx(i, gas_used=g) for i, g in enumerate(gas)))
    res = await run("onchain.gas_efficiency", c, **config)
    assert res.value == float(sum(gas)) and res.unit == "gas"
    assert res.score == (pytest.approx(score) if score is not None else None)
    assert res.passed is passed and (res.threshold == 0.5)


async def test_gas_efficiency_reader_fallback_and_missing() -> None:
    c = case(tx(0, gas_used=21_000), tx(1), tx(2))
    reader = FakeChainReader()
    reader.receipts["0x" + "01" * 32] = {"gasUsed": "0x5208"}
    res = await run("onchain.gas_efficiency", c, reader=reader, value_max=100_000)
    assert res.value == 42_000 and res.details["unknown"] == ["tx2"]
    res = await run("onchain.gas_efficiency", case(tx(0)))
    assert res.status == "skipped" and res.error.code == "missing_input"
