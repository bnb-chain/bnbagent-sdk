"""X402Gate: eip3009 + permit2-exact verification, nonces, quotes."""

from __future__ import annotations

import asyncio
import base64
import json
import secrets
import time
from typing import Any

import httpx
import pytest

pytest.importorskip("eth_account")

from eth_account import Account  # noqa: E402

from bnbagent_eval.ports import AssetSpec  # noqa: E402
from bnbagent_eval.server.payment import (  # noqa: E402
    PERMIT2_ADDRESS,
    PERMIT2_WITNESS_TYPES,
    TRANSFER_WITH_AUTHORIZATION_FIELDS,
    X402Gate,
)
from bnbagent_eval.server.payment_base import PaymentError, PaymentReceipt, Quote  # noqa: E402
from bnbagent_eval.server.settlement import FacilitatorSettlement  # noqa: E402
from bnbagent_eval.testing.fakes import FakeSettlement, StaticAssetCatalog  # noqa: E402

# Publicly known Anvil/Hardhat dev account #0 (not a secret).
DEV_KEY = (
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"  # secret-scan: allow
)
PAYER = Account.from_key(DEV_KEY)
OTHER = Account.create()

CHAIN = 97
NETWORK = "eip155:97"
U = "0x330949Aed7d00FCe0558C64ED6FeC9792616cC39"  # SDK catalogue TEST_U (b402 address)
USDC = "0xEC1C60D64a06896Df296438c12edD14E974FDE47"  # SDK catalogue TEST_USDC (b402 address)
UNOFFERED = "0x" + "77" * 20
PAY_TO = "0x" + "be" * 20
PROXY = "0x" + "44" * 20
NOW = 1_800_000_000

ASSETS = [
    AssetSpec(CHAIN, U, "U", 6, ("eip3009",), {"name": "U", "version": "1"}),
    AssetSpec(CHAIN, USDC, "USDC", 6, ("permit2-exact",), {"name": "USD Coin", "version": "1"}),
]
REQUEST = {"suite": {"id": "s"}, "cases": [{"input": "hi"}]}
EXPENSIVE = {"suite": {"id": "s"}, "cases": [{"input": "hi"}, {"input": "more"}]}


class Clock:
    def __init__(self, t: float = NOW):
        self.t = t

    def __call__(self) -> float:
        return self.t


def make_gate(tmp_path, *, settlement=None, clock=None, **kw) -> X402Gate:
    return X402Gate(
        pay_to=PAY_TO,
        chain_id=CHAIN,
        assets=StaticAssetCatalog(ASSETS),
        settlement=settlement or FakeSettlement(),
        store_path=tmp_path / "x402.sqlite",
        clock=clock or Clock(),
        permit2_spender=PROXY,
        **kw,
    )


def entry(gate: X402Gate, quote: Quote, method: str) -> dict[str, Any]:
    body = gate.challenge(quote)
    return next(a for a in body["accepts"] if a["extra"]["assetTransferMethod"] == method)


def envelope(accept: dict[str, Any], payload: dict[str, Any], **extra: Any) -> str:
    # Exactly the SDK buyer_demo ``_build_payment_envelope`` shape.
    env = {
        "x402Version": 2,
        "scheme": accept["scheme"],
        "network": accept["network"],
        "payload": payload,
        **extra,
    }
    return base64.b64encode(json.dumps(env).encode()).decode()


def sig_hex(signed: Any) -> str:
    raw = signed.signature
    out = raw.hex() if not isinstance(raw, str) else raw
    return out if out.startswith("0x") else "0x" + out


def pay_eip3009(
    accept: dict[str, Any],
    *,
    account=PAYER,
    now: int = NOW,
    value: int | None = None,
    to: str | None = None,
    valid_after: int | None = None,
    valid_before: int | None = None,
    nonce: str | None = None,
    domain_override: dict[str, Any] | None = None,
    from_addr: str | None = None,
    **env_extra: Any,
) -> str:
    """Sign like ``buyer_demo`` + ``EVMWalletProvider._raw_sign_typed_data``."""
    msg = {
        "from": from_addr or account.address,
        "to": to or accept["payTo"],
        "value": int(accept["amount"]) if value is None else value,
        "validAfter": now - 60 if valid_after is None else valid_after,
        "validBefore": now + int(accept["maxTimeoutSeconds"])
        if valid_before is None
        else valid_before,
        "nonce": nonce or "0x" + secrets.token_hex(32),
    }
    domain = {
        "name": accept["extra"]["name"],
        "version": accept["extra"]["version"],
        "chainId": CHAIN,
        "verifyingContract": accept["asset"],
        **(domain_override or {}),
    }
    signed = account.sign_typed_data(
        domain_data=domain,
        message_types={"TransferWithAuthorization": TRANSFER_WITH_AUTHORIZATION_FIELDS},
        message_data=msg,
    )
    payload = {
        "authorization": {
            "from": msg["from"],
            "to": msg["to"],
            "value": str(msg["value"]),
            "validAfter": str(msg["validAfter"]),
            "validBefore": str(msg["validBefore"]),
            "nonce": msg["nonce"],
        },
        "signature": sig_hex(signed),
    }
    return envelope(accept, payload, **env_extra)


def pay_permit2(
    accept: dict[str, Any],
    *,
    account=PAYER,
    now: int = NOW,
    token: str | None = None,
    amount: int | None = None,
    to: str | None = None,
    spender: str | None = None,
    nonce: int | None = None,
    deadline: int | None = None,
    valid_after: int | None = None,
    legacy: bool = False,
) -> str:
    """The b402 ``permit2Authorization`` dialect (SDK ``normalizeX402PaymentHeader``)."""
    auth = {
        "permitted": {
            "token": token or accept["asset"],
            "amount": str(int(accept["amount"]) if amount is None else amount),
        },
        "from": account.address,
        "spender": spender or accept["extra"]["spenderAddress"],
        "nonce": str(secrets.randbits(128) if nonce is None else nonce),
        "deadline": str(now + 300 if deadline is None else deadline),
        "witness": {
            "to": to or accept["payTo"],
            "validAfter": str(now - 60 if valid_after is None else valid_after),
            "extra": "0x",
        },
    }
    message = {
        "permitted": {
            "token": auth["permitted"]["token"],
            "amount": int(auth["permitted"]["amount"]),
        },
        "spender": auth["spender"],
        "nonce": int(auth["nonce"]),
        "deadline": int(auth["deadline"]),
        "witness": {
            "to": auth["witness"]["to"],
            "validAfter": int(auth["witness"]["validAfter"]),
            "extra": b"",
        },
    }
    signed = account.sign_typed_data(
        domain_data={"name": "Permit2", "chainId": CHAIN, "verifyingContract": PERMIT2_ADDRESS},
        message_types=PERMIT2_WITNESS_TYPES,
        message_data=message,
    )
    if legacy:
        permit = {k: v for k, v in auth.items() if k != "from"}
        payload = {"signature": sig_hex(signed), "from": account.address, "permit": permit}
    else:
        payload = {"signature": sig_hex(signed), "permit2Authorization": auth}
    return envelope(accept, payload)


async def expect_code(gate: X402Gate, header: str, quote: Quote, code: str) -> PaymentError:
    with pytest.raises(PaymentError) as info:
        await gate.verify_and_settle(header, quote)
    assert info.value.code == code, str(info.value)
    return info.value


# --------------------------------------------------------------------------- quotes / challenge


async def test_quote_price_and_binding(tmp_path):
    gate = make_gate(tmp_path, base_fee=100, per_call_fee=10)
    q = await gate.quote(REQUEST, judge_calls=3)
    assert q is not None and q.amount == 130
    assert q.asset == U and q.pay_to.lower() == PAY_TO and q.chain_id == CHAIN
    assert q.expires_at == NOW + 300
    from bnbagent_eval.hashing import hash_obj

    assert q.request_hash == hash_obj(REQUEST)
    q2 = await gate.quote(REQUEST, judge_calls=3)
    assert q2.amount == q.amount and q2.request_hash == q.request_hash
    assert q2.quote_id != q.quote_id
    assert gate.load_quote(q.quote_id) == q
    zero = make_gate(tmp_path, price_fn=lambda req, calls: 0)
    assert await zero.quote(REQUEST, judge_calls=1) is None


async def test_challenge_shape(tmp_path):
    gate = make_gate(tmp_path)
    q = await gate.quote(REQUEST, judge_calls=2)
    body = gate.challenge(q)
    assert body["x402Version"] == 2
    accepts = body["accepts"]
    assert {(a["asset"], a["extra"]["assetTransferMethod"]) for a in accepts} == {
        (U, "eip3009"),
        (USDC, "permit2-exact"),
    }
    for a in accepts:
        assert a["scheme"] == "exact" and a["network"] == NETWORK
        assert a["payTo"].lower() == PAY_TO and a["amount"] == str(q.amount)
        assert a["resource"] == "/v1/evaluate"
        assert a["extra"]["quoteId"] == q.quote_id
        assert a["extra"]["name"] and a["extra"]["version"]
    e3009 = next(a for a in accepts if a["asset"] == U)
    assert e3009["maxAmountRequired"] == str(q.amount)
    assert e3009["extra"] == {
        "name": "U",
        "version": "1",
        "assetTransferMethod": "eip3009",
        "quoteId": q.quote_id,
    }
    p2 = next(a for a in accepts if a["asset"] == USDC)
    assert "maxAmountRequired" not in p2  # the SDK's TS permit2 matcher rejects both spellings
    assert p2["extra"]["spenderAddress"].lower() == PROXY
    # Without a configured Permit2 proxy, permit2-exact is not offered.
    plain = X402Gate(
        pay_to=PAY_TO,
        chain_id=CHAIN,
        assets=StaticAssetCatalog(ASSETS),
        settlement=FakeSettlement(),
        store_path=tmp_path / "plain.sqlite",
    )
    q3 = await plain.quote(REQUEST, judge_calls=0)
    assert [a["asset"] for a in plain.challenge(q3)["accepts"]] == [U]


async def test_decimals_scaling_rounds_up(tmp_path):
    assets = [
        AssetSpec(CHAIN, U, "U", 18, ("eip3009",), {"name": "U", "version": "1"}),
        AssetSpec(
            CHAIN, USDC, "USDC", 6, ("permit2-exact",), {"name": "USD Coin", "version": "1"}
        ),
    ]
    gate = X402Gate(
        pay_to=PAY_TO,
        chain_id=CHAIN,
        assets=StaticAssetCatalog(assets),
        settlement=FakeSettlement(),
        store_path=tmp_path / "d.sqlite",
        price_fn=lambda r, c: 10**12 + 1,
        permit2_spender=PROXY,
    )
    q = await gate.quote(REQUEST, judge_calls=0)
    amounts = {a["asset"]: int(a["amount"]) for a in gate.challenge(q)["accepts"]}
    assert amounts == {U: 10**12 + 1, USDC: 2}


# --------------------------------------------------------------------------- valid payments


async def test_valid_eip3009_accepted(tmp_path):
    settlement = FakeSettlement()
    gate = make_gate(tmp_path, settlement=settlement)
    q = await gate.quote(REQUEST, judge_calls=1)
    receipt = await gate.verify_and_settle(pay_eip3009(entry(gate, q, "eip3009")), q)
    assert isinstance(receipt, PaymentReceipt)
    assert receipt.payer == PAYER.address and receipt.amount == q.amount
    assert receipt.quote_id == q.quote_id and receipt.tx_hash
    assert receipt.extras["method"] == "eip3009" and receipt.extras["asset"] == U
    (settled,) = settlement.settled
    assert settled["asset"] == U and settled["authorization"]["from"] == PAYER.address
    assert settled["paymentRequirements"]["asset"] == U
    assert settled["paymentPayload"]["payload"]["signature"] == settled["signature"]
    assert json.loads(base64.b64decode(receipt.header()))["success"] is True


async def test_valid_eip3009_overpay_and_explicit_asset(tmp_path):
    gate = make_gate(tmp_path)
    q = await gate.quote(REQUEST, judge_calls=1)
    acc = entry(gate, q, "eip3009")
    header = pay_eip3009(acc, value=q.amount + 5, accepted=acc)
    receipt = await gate.verify_and_settle(header, q)
    assert receipt.amount == q.amount + 5


async def test_valid_permit2_accepted(tmp_path):
    settlement = FakeSettlement()
    gate = make_gate(tmp_path, settlement=settlement)
    q = await gate.quote(REQUEST, judge_calls=1)
    acc = entry(gate, q, "permit2-exact")
    receipt = await gate.verify_and_settle(pay_permit2(acc, nonce=7), q)
    assert receipt.extras["method"] == "permit2-exact" and receipt.payer == PAYER.address
    assert receipt.nonce == "0x" + f"{7:064x}"
    assert settlement.settled[0]["permit2Authorization"]["spender"].lower() == PROXY
    # Legacy ``payload.permit`` + ``payload.from`` dialect.
    receipt2 = await gate.verify_and_settle(pay_permit2(acc, legacy=True), q)
    assert receipt2.payer == PAYER.address


# --------------------------------------------------------------------------- rejections


async def test_rejection_codes_eip3009(tmp_path):
    gate = make_gate(tmp_path)
    q = await gate.quote(REQUEST, judge_calls=1)
    acc = entry(gate, q, "eip3009")
    good = json.loads(base64.b64decode(pay_eip3009(acc)))

    def mutate(**top: Any) -> str:
        return base64.b64encode(json.dumps({**good, **top}).encode()).decode()

    await expect_code(gate, "!!!not base64!!!", q, "malformed")
    await expect_code(gate, base64.b64encode(b"[1]").decode(), q, "malformed")
    await expect_code(gate, base64.b64encode(b"not json").decode(), q, "malformed")
    await expect_code(gate, mutate(payload={"signature": "0x00"}), q, "malformed")
    await expect_code(gate, mutate(x402Version=9), q, "malformed")
    await expect_code(gate, mutate(scheme="upto"), q, "wrong_scheme")
    await expect_code(gate, mutate(network="eip155:56"), q, "wrong_network")
    await expect_code(gate, mutate(accepted={"asset": UNOFFERED}), q, "wrong_asset")
    await expect_code(gate, mutate(accepted={"asset": USDC}), q, "wrong_asset")
    await expect_code(gate, pay_eip3009(acc, to="0x" + "11" * 20), q, "wrong_recipient")
    await expect_code(gate, pay_eip3009(acc, value=q.amount - 1), q, "insufficient_amount")
    await expect_code(gate, pay_eip3009(acc, valid_before=NOW), q, "expired")
    await expect_code(gate, pay_eip3009(acc, valid_after=NOW), q, "not_yet_valid")
    # Signed by another key but claiming PAYER, or under the wrong domain / chain.
    await expect_code(gate, pay_eip3009(acc, account=OTHER, from_addr=PAYER.address), q,
                      "bad_signature")  # fmt: skip
    await expect_code(gate, pay_eip3009(acc, domain_override={"chainId": 56}), q, "bad_signature")
    await expect_code(gate, pay_eip3009(acc, domain_override={"name": "X"}), q, "bad_signature")
    # Tampered value after signing.
    tampered = json.loads(base64.b64decode(pay_eip3009(acc)))
    tampered["payload"]["authorization"]["value"] = str(q.amount * 10)
    await expect_code(gate, base64.b64encode(json.dumps(tampered).encode()).decode(), q,
                      "bad_signature")  # fmt: skip


async def test_rejection_codes_permit2(tmp_path):
    gate = make_gate(tmp_path)
    q = await gate.quote(REQUEST, judge_calls=1)
    acc = entry(gate, q, "permit2-exact")
    await expect_code(gate, pay_permit2(acc, token=UNOFFERED), q, "wrong_asset")
    await expect_code(gate, pay_permit2(acc, token=U), q, "wrong_asset")  # eip3009-only asset
    await expect_code(gate, pay_permit2(acc, to="0x" + "11" * 20), q, "wrong_recipient")
    await expect_code(gate, pay_permit2(acc, spender="0x" + "22" * 20), q, "wrong_recipient")
    await expect_code(gate, pay_permit2(acc, amount=q.amount - 1), q, "insufficient_amount")
    await expect_code(gate, pay_permit2(acc, deadline=NOW - 1), q, "expired")
    await expect_code(gate, pay_permit2(acc, valid_after=NOW + 10), q, "not_yet_valid")
    tampered = json.loads(base64.b64decode(pay_permit2(acc)))
    tampered["payload"]["permit2Authorization"]["from"] = OTHER.address
    await expect_code(gate, base64.b64encode(json.dumps(tampered).encode()).decode(), q,
                      "bad_signature")  # fmt: skip


async def test_quote_expired(tmp_path):
    clock = Clock()
    gate = make_gate(tmp_path, clock=clock, quote_ttl_s=60)
    q = await gate.quote(REQUEST, judge_calls=1)
    acc = entry(gate, q, "eip3009")
    clock.t = NOW + 61
    await expect_code(gate, pay_eip3009(acc, now=NOW + 61), q, "expired")


async def test_unknown_or_altered_quote(tmp_path):
    gate = make_gate(tmp_path)
    q = await gate.quote(REQUEST, judge_calls=1)
    acc = entry(gate, q, "eip3009")
    forged = Quote(**{**q.__dict__, "quote_id": "forged"})
    await expect_code(gate, pay_eip3009(acc), forged, "quote_mismatch")
    cheaper = Quote(**{**q.__dict__, "amount": 1})
    await expect_code(gate, pay_eip3009(acc, value=1), cheaper, "quote_mismatch")


async def test_cheap_quote_expensive_request(tmp_path):
    gate = make_gate(tmp_path)
    cheap = await gate.quote(REQUEST, judge_calls=0)
    acc = entry(gate, cheap, "eip3009")
    expensive = await gate.quote(EXPENSIVE, judge_calls=20)
    assert expensive.amount > cheap.amount
    # Header names the cheap quote (v2 ``accepted`` echo): bound to another request.
    await expect_code(gate, pay_eip3009(acc, accepted=acc), expensive, "quote_mismatch")
    await expect_code(gate, pay_eip3009(acc, quoteId=cheap.quote_id), expensive, "quote_mismatch")
    # No quote reference (SDK buyer_demo envelope): the recomputed price wins.
    await expect_code(gate, pay_eip3009(acc), expensive, "insufficient_amount")


async def test_referenced_quote_expired(tmp_path):
    clock = Clock()
    gate = make_gate(tmp_path, clock=clock, quote_ttl_s=60)
    old = await gate.quote(REQUEST, judge_calls=1)
    acc = entry(gate, old, "eip3009")
    clock.t = NOW + 120
    fresh = await gate.quote(REQUEST, judge_calls=1)
    await expect_code(gate, pay_eip3009(acc, now=NOW + 120, accepted=acc), fresh, "expired")
    receipt = await gate.verify_and_settle(pay_eip3009(acc, now=NOW + 120), fresh)
    assert receipt.quote_id == fresh.quote_id


# --------------------------------------------------------------------------- nonces


async def test_replay_rejected_sequentially(tmp_path):
    gate = make_gate(tmp_path)
    q = await gate.quote(REQUEST, judge_calls=1)
    header = pay_eip3009(entry(gate, q, "eip3009"))
    await gate.verify_and_settle(header, q)
    await expect_code(gate, header, q, "nonce_reused")
    p2 = pay_permit2(entry(gate, q, "permit2-exact"), nonce=42)
    await gate.verify_and_settle(p2, q)
    await expect_code(gate, p2, q, "nonce_reused")


async def test_concurrent_replay_one_winner(tmp_path):
    settlement = FakeSettlement()
    gate = make_gate(tmp_path, settlement=settlement)
    q = await gate.quote(REQUEST, judge_calls=1)
    header = pay_eip3009(entry(gate, q, "eip3009"))
    # Separate gate instances on one file emulate several workers.
    gates = [gate] + [make_gate(tmp_path, settlement=settlement) for _ in range(9)]
    results = await asyncio.gather(
        *(g.verify_and_settle(header, q) for g in gates), return_exceptions=True
    )
    wins = [r for r in results if isinstance(r, PaymentReceipt)]
    losses = [r for r in results if isinstance(r, PaymentError)]
    assert len(wins) == 1 and len(losses) == 9
    assert {e.code for e in losses} == {"nonce_reused"}
    assert len(settlement.settled) == 1


async def test_concurrent_replay_threads(tmp_path):
    """Real thread-level concurrency: one event loop per thread, same sqlite file."""
    import threading

    settlement = FakeSettlement()
    gate = make_gate(tmp_path, settlement=settlement)
    q = await gate.quote(REQUEST, judge_calls=1)
    header = pay_eip3009(entry(gate, q, "eip3009"))
    barrier = threading.Barrier(10)
    outcomes: list[str] = []

    def worker() -> None:
        g = make_gate(tmp_path, settlement=settlement)
        barrier.wait()
        try:
            asyncio.run(g.verify_and_settle(header, q))
            outcomes.append("ok")
        except PaymentError as exc:
            outcomes.append(exc.code)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["nonce_reused"] * 9 + ["ok"]


async def test_nonce_store_survives_restart(tmp_path):
    gate = make_gate(tmp_path, settlement=FakeSettlement())
    q = await gate.quote(REQUEST, judge_calls=1)
    header = pay_eip3009(entry(gate, q, "eip3009"))
    await gate.verify_and_settle(header, q)
    del gate
    # Fresh settlement (no on-chain memory): only the sqlite store can stop the replay.
    restarted = make_gate(tmp_path, settlement=FakeSettlement())
    assert restarted.load_quote(q.quote_id) == q
    await expect_code(restarted, header, q, "nonce_reused")


async def test_authorization_state_used_rejected(tmp_path):
    settlement = FakeSettlement()
    gate = make_gate(tmp_path, settlement=settlement)
    q = await gate.quote(REQUEST, judge_calls=1)
    nonce = "0x" + "ab" * 32
    settlement.used.add((U.lower(), PAYER.address.lower(), nonce))
    await expect_code(gate, pay_eip3009(entry(gate, q, "eip3009"), nonce=nonce), q,
                      "nonce_reused")  # fmt: skip
    assert settlement.settled == []


class FailingSettlement(FakeSettlement):
    def __init__(self, *, mark_used: bool = False):
        super().__init__()
        self.fail = True
        self.mark_used = mark_used

    async def settle(self, payment):
        if self.fail:
            if self.mark_used:
                auth = payment["authorization"]
                self.used.add((payment["asset"].lower(), auth["from"].lower(), auth["nonce"]))
            raise RuntimeError("rpc down")
        return await super().settle(payment)


async def test_settlement_failure_releases_nonce(tmp_path):
    settlement = FailingSettlement()
    gate = make_gate(tmp_path, settlement=settlement)
    q = await gate.quote(REQUEST, judge_calls=1)
    header = pay_eip3009(entry(gate, q, "eip3009"))
    err = await expect_code(gate, header, q, "settlement_failed")
    assert err.status == 502
    settlement.fail = False
    receipt = await gate.verify_and_settle(header, q)  # the same payment can be retried
    assert receipt.tx_hash


async def test_settlement_failure_used_onchain_keeps_nonce(tmp_path):
    settlement = FailingSettlement(mark_used=True)
    gate = make_gate(tmp_path, settlement=settlement)
    q = await gate.quote(REQUEST, judge_calls=1)
    header = pay_eip3009(entry(gate, q, "eip3009"))
    await expect_code(gate, header, q, "settlement_failed")
    settlement.fail = False
    settlement.used.clear()  # even if the chain view is lost, the store keeps the nonce
    await expect_code(gate, header, q, "nonce_reused")


# --------------------------------------------------------------------------- facilitator


async def test_facilitator_settlement(tmp_path):
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append((request.url.path, body))
        if request.url.path == "/verify":
            return httpx.Response(200, json={"isValid": True, "payer": PAYER.address})
        return httpx.Response(200, json={"success": True, "transaction": "0x" + "cd" * 32})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gate = make_gate(tmp_path, settlement=FacilitatorSettlement("https://fac.example/", client))
    q = await gate.quote(REQUEST, judge_calls=1)
    acc = entry(gate, q, "eip3009")
    receipt = await gate.verify_and_settle(pay_eip3009(acc), q)
    assert receipt.tx_hash == "0x" + "cd" * 32
    assert [c[0] for c in calls] == ["/verify", "/settle"]
    body = calls[0][1]
    assert body["x402Version"] == 2 and body["paymentRequirements"]["asset"] == U
    assert body["paymentPayload"]["payload"]["authorization"]["from"] == PAYER.address
    await client.aclose()


async def test_facilitator_rejection_releases_nonce(tmp_path):
    state = {"valid": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/verify":
            return httpx.Response(200, json={"isValid": state["valid"], "invalidReason": "x"})
        return httpx.Response(200, json={"success": True, "transaction": "0x" + "ef" * 32})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gate = make_gate(tmp_path, settlement=FacilitatorSettlement("https://fac.example", client))
    q = await gate.quote(REQUEST, judge_calls=1)
    header = pay_eip3009(entry(gate, q, "eip3009"))
    await expect_code(gate, header, q, "settlement_failed")
    state["valid"] = True
    assert (await gate.verify_and_settle(header, q)).tx_hash == "0x" + "ef" * 32
    await client.aclose()


async def test_facilitator_http_error():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    fac = FacilitatorSettlement("https://fac.example", client)
    assert await fac.authorization_state(U, PAYER.address, "0x00") is False
    with pytest.raises(Exception, match="HTTP 500"):
        await fac.settle({"paymentPayload": {"x402Version": 2}, "paymentRequirements": {}})
    with pytest.raises(ValueError):
        await fac.settle({})
    await client.aclose()


# --------------------------------------------------------------------------- † SDK cross-check


async def test_sdk_x402_signer_cross_check(tmp_path):
    bnbagent = pytest.importorskip("bnbagent")
    from bnbagent.x402 import resolve_expected_eip3009_route

    route = resolve_expected_eip3009_route(NETWORK, "TEST_U")
    assert route.address == U and (route.name, route.version) == ("U", "1")

    gate = make_gate(tmp_path, clock=time.time)
    q = await gate.quote(REQUEST, judge_calls=2)
    accept = entry(gate, q, "eip3009")

    wallet = bnbagent.EVMWalletProvider(password="pw", private_key=DEV_KEY, persist=False)
    signer = bnbagent.X402Signer(wallet, max_value_per_call={U: q.amount})
    now = int(time.time())
    message = {  # buyer_demo._build_twa_message
        "from": wallet.address,
        "to": accept["payTo"],
        "value": int(accept["amount"]),
        "validAfter": now - 60,
        "validBefore": now + int(accept["maxTimeoutSeconds"]),
        "nonce": "0x" + secrets.token_hex(32),
    }
    signed = signer.sign_payment(
        domain={
            "name": accept["extra"]["name"],
            "version": accept["extra"]["version"],
            "chainId": CHAIN,
            "verifyingContract": accept["asset"],
        },
        types={
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "TransferWithAuthorization": TRANSFER_WITH_AUTHORIZATION_FIELDS,
        },
        message=message,
        expected_route=route,
        expected_to=PAY_TO,
    )
    raw = signed["signature"]
    sig = raw.hex() if not isinstance(raw, str) else raw
    sig = sig if sig.startswith("0x") else "0x" + sig
    payload = {
        "authorization": {
            "from": message["from"],
            "to": message["to"],
            "value": str(message["value"]),
            "validAfter": str(message["validAfter"]),
            "validBefore": str(message["validBefore"]),
            "nonce": message["nonce"],
        },
        "signature": sig,
    }
    receipt = await gate.verify_and_settle(envelope(accept, payload), q)
    assert receipt.payer == wallet.address and receipt.amount == q.amount


async def test_identify_recovers_the_signer_without_charging(tmp_path):
    """Idempotency-Key is scoped to the verified payer."""
    settlement = FakeSettlement()
    gate = make_gate(tmp_path, settlement=settlement)
    q = await gate.quote(REQUEST, judge_calls=1)
    header = pay_eip3009(entry(gate, q, "eip3009"))
    assert await gate.identify(header) == PAYER.address.lower()
    permit = pay_permit2(entry(gate, q, "permit2-exact"))
    assert await gate.identify(permit) == PAYER.address.lower()
    # A header claiming PAYER but signed by someone else does not identify as PAYER.
    forged = pay_eip3009(entry(gate, q, "eip3009"), account=OTHER, from_addr=PAYER.address)
    assert await gate.identify(forged) is None
    assert await gate.identify("not base64 json") is None
    assert not settlement.settled  # nothing reserved or settled
    receipt = await gate.verify_and_settle(header, q)  # the nonce is still unused
    assert receipt.payer == PAYER.address
