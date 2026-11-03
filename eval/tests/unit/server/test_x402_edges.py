"""X402Gate input validation and settlement failure edges, for the coverage gate."""

from __future__ import annotations

import base64
import dataclasses
import json
from typing import Any

import pytest

pytest.importorskip("eth_account")

from bnbagent_eval.ports import AssetSpec  # noqa: E402
from bnbagent_eval.server.payment import X402Gate  # noqa: E402
from bnbagent_eval.testing.fakes import FakeSettlement, StaticAssetCatalog  # noqa: E402

from .test_x402_gate import (  # noqa: E402
    ASSETS,
    CHAIN,
    PAY_TO,
    PROXY,
    REQUEST,
    USDC,
    U,
    entry,
    expect_code,
    make_gate,
    pay_eip3009,
)


def _gate_with(tmp_path, assets, **kw) -> X402Gate:
    return X402Gate(
        pay_to=PAY_TO,
        chain_id=CHAIN,
        assets=StaticAssetCatalog(assets),
        settlement=kw.pop("settlement", FakeSettlement()),
        store_path=tmp_path / "edge.sqlite",
        permit2_spender=PROXY,
        **kw,
    )


def _edit(header: str, fn) -> str:
    env = json.loads(base64.b64decode(header))
    fn(env)
    return base64.b64encode(json.dumps(env).encode()).decode()


def _auth(**fields: Any):
    def apply(env: dict) -> None:
        env["payload"]["authorization"].update(fields)

    return apply


def test_constructor_rejects_bad_pay_to(tmp_path) -> None:
    with pytest.raises(ValueError, match="not an address"):
        X402Gate(
            pay_to="nope",
            chain_id=CHAIN,
            assets=StaticAssetCatalog(ASSETS),
            settlement=FakeSettlement(),
            store_path=tmp_path / "x.sqlite",
        )


async def test_quote_needs_a_payable_asset(tmp_path) -> None:
    gate = _gate_with(tmp_path, [])
    with pytest.raises(ValueError, match="no payable asset"):
        await gate.quote(REQUEST, judge_calls=1)


async def test_pinned_asset_and_upscaled_decimals(tmp_path) -> None:
    pinned = _gate_with(tmp_path, ASSETS, asset=USDC)
    q = await pinned.quote(REQUEST, judge_calls=0)
    assert q.asset == USDC and {a["asset"] for a in pinned.challenge(q)["accepts"]} == {USDC}
    assets = [
        AssetSpec(CHAIN, U, "U", 6, ("eip3009",), {"name": "U", "version": "1"}),
        AssetSpec(CHAIN, USDC, "USDC", 18, ("permit2-exact",), {"name": "USD Coin"}),
    ]
    scaled = _gate_with(tmp_path, assets, price_fn=lambda r, c: 7)
    q = await scaled.quote(REQUEST, judge_calls=0)
    amounts = {a["asset"]: int(a["amount"]) for a in scaled.challenge(q)["accepts"]}
    assert amounts == {U: 7, USDC: 7 * 10**12}


async def test_integer_value_is_accepted(tmp_path) -> None:
    gate = make_gate(tmp_path)
    q = await gate.quote(REQUEST, judge_calls=1)
    header = _edit(pay_eip3009(entry(gate, q, "eip3009")), _auth(value=q.amount))
    receipt = await gate.verify_and_settle(header, q)
    assert receipt.amount == q.amount


@pytest.mark.parametrize(
    "edit",
    [
        _auth(value=True),
        _auth(value="12abc"),
        _auth(value=str(2**256)),
        _auth(nonce="ab" * 32),  # no 0x
        _auth(nonce="0xzz"),
        _auth(nonce="0x" + "ab" * 31),  # 31 bytes
        _auth(**{"from": 123}),
        lambda env: env["payload"].update(authorization="not-an-object"),
        lambda env: env.update(quoteId=5),
    ],
)
async def test_malformed_fields(tmp_path, edit) -> None:
    gate = make_gate(tmp_path)
    q = await gate.quote(REQUEST, judge_calls=1)
    header = _edit(pay_eip3009(entry(gate, q, "eip3009")), edit)
    await expect_code(gate, header, q, "malformed")


async def test_header_and_quote_guards(tmp_path) -> None:
    gate = make_gate(tmp_path)
    q = await gate.quote(REQUEST, judge_calls=1)
    header = pay_eip3009(entry(gate, q, "eip3009"))
    await expect_code(gate, "", q, "malformed")
    await expect_code(gate, header, dataclasses.replace(q, chain_id=56), "quote_mismatch")


@pytest.mark.parametrize(
    "signature", ["0x" + "00" * 64, "0x" + "00" * 65], ids=["64-bytes", "unrecoverable"]
)
async def test_bad_signatures(tmp_path, signature) -> None:
    gate = make_gate(tmp_path)
    q = await gate.quote(REQUEST, judge_calls=1)
    header = _edit(
        pay_eip3009(entry(gate, q, "eip3009")),
        lambda env: env["payload"].update(signature=signature),
    )
    await expect_code(gate, header, q, "bad_signature")


async def test_eip3009_payment_when_only_permit2_is_offered(tmp_path) -> None:
    gate = _gate_with(tmp_path, [ASSETS[1]])  # USDC: permit2-exact only
    q = await gate.quote(REQUEST, judge_calls=1)
    header = pay_eip3009(entry(gate, q, "permit2-exact"))  # no explicit asset in the envelope
    await expect_code(gate, header, q, "wrong_asset")


class FlakySettlement(FakeSettlement):
    """authorization_state fails on call ``fail_on`` (1-based); settle may fail too."""

    def __init__(self, *, fail_on: tuple[int, ...], settle_fails: bool = False):
        super().__init__()
        self.calls = 0
        self.fail_on = fail_on
        self.settle_fails = settle_fails

    async def authorization_state(self, asset, payer, nonce):
        self.calls += 1
        if self.calls in self.fail_on:
            raise RuntimeError("rpc down")
        return await super().authorization_state(asset, payer, nonce)

    async def settle(self, payment):
        if self.settle_fails:
            raise RuntimeError("settle down")
        return await super().settle(payment)


async def test_unreadable_authorization_state_releases_the_nonce(tmp_path) -> None:
    settlement = FlakySettlement(fail_on=(1,))
    gate = make_gate(tmp_path, settlement=settlement)
    q = await gate.quote(REQUEST, judge_calls=1)
    header = pay_eip3009(entry(gate, q, "eip3009"))
    err = await expect_code(gate, header, q, "settlement_failed")
    assert err.status == 502
    assert (await gate.verify_and_settle(header, q)).tx_hash  # released: retry works


async def test_settle_failure_with_unknown_state_keeps_the_nonce(tmp_path) -> None:
    settlement = FlakySettlement(fail_on=(2,), settle_fails=True)
    gate = make_gate(tmp_path, settlement=settlement)
    q = await gate.quote(REQUEST, judge_calls=1)
    header = pay_eip3009(entry(gate, q, "eip3009"))
    await expect_code(gate, header, q, "settlement_failed")
    settlement.settle_fails = False
    await expect_code(gate, header, q, "nonce_reused")  # fail closed
