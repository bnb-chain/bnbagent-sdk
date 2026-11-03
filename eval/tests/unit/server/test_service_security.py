"""Eval service security: payer-scoped idempotency and job tokens, credit units, judge cost
limits, job caps and retention, and egress pinning (threat model: docs/design/security.md)."""

from __future__ import annotations

import dataclasses
import json
import time

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from bnbagent_eval.net import is_public_ip  # noqa: E402
from bnbagent_eval.server.payment_base import PaymentReceipt, Quote  # noqa: E402
from bnbagent_eval.server.policy import GuardedTransport  # noqa: E402

from ._fakes import PAYER, FakeGate, payment_for, request_body  # noqa: E402

MALLORY = "0x" + "66" * 20


def post(c, path, body, **headers):
    return c.post(path, content=json.dumps(body), headers=headers)


def _quote(c, body, path="/v1/evaluate"):
    r = post(c, path, body)
    assert r.status_code == 402, r.text
    return r.json()["quote"]


def _pay(quote, payer=PAYER):
    header = json.loads(payment_for(quote))
    header["payer"] = payer
    return json.dumps(header)


# ---------------------------------------------------------- idempotency scope
def test_idempotency_key_is_scoped_to_the_payer(make_app) -> None:
    gate = FakeGate()
    c = make_app(gate=gate)
    body = request_body()
    key = {"Idempotency-Key": "shared-key"}
    alice = post(c, "/v1/evaluate", body, **{"X-PAYMENT": _pay(_quote(c, body))}, **key)
    assert alice.status_code == 200
    # No payment: no replay of Alice's (paid) response, just the 402 challenge.
    assert post(c, "/v1/evaluate", body, **key).status_code == 402
    # Another payer with the same key gets an evaluation of their own (and pays for it).
    mallory = post(c, "/v1/evaluate", body, **{"X-PAYMENT": _pay(_quote(c, body), MALLORY)}, **key)
    assert mallory.status_code == 200 and "Idempotent-Replayed" not in mallory.headers
    assert len(gate.settled) == 2
    # Alice's own retry (a fresh signature, same payer) still replays without charging.
    again = post(c, "/v1/evaluate", body, **{"X-PAYMENT": _pay(_quote(c, body))}, **key)
    assert again.headers["Idempotent-Replayed"] == "true" and len(gate.settled) == 2


def test_another_payer_cannot_rotate_a_job_token(make_app) -> None:
    gate = FakeGate()
    with make_app(gate=gate) as c:
        body = request_body()
        key = {"Idempotency-Key": "job-key"}
        quote = _quote(c, body, "/v1/evaluations")
        first = post(c, "/v1/evaluations", body, **{"X-PAYMENT": _pay(quote)}, **key).json()
        quote = _quote(c, body, "/v1/evaluations")
        other = post(c, "/v1/evaluations", body, **{"X-PAYMENT": _pay(quote, MALLORY)}, **key)
        assert other.status_code == 202 and other.json()["job_id"] != first["job_id"]
        auth = {"Authorization": f"Bearer {first['access_token']}"}
        deadline = time.time() + 10
        while time.time() < deadline:
            r = c.get(f"/v1/evaluations/{first['job_id']}", headers=auth)
            assert r.status_code == 200  # Alice's token was not revoked
            if r.json()["status"] == "done":
                break
            time.sleep(0.02)
        else:
            raise AssertionError("job did not finish")


# ---------------------------------------------------------- credit units
class _ScaledGate(FakeGate):
    """Pays with an 18-decimal asset while quoting a 6-decimal one (receipt in 18 decimals)."""

    async def verify_and_settle(self, header: str, quote: Quote) -> PaymentReceipt:
        receipt = await super().verify_and_settle(header, quote)
        return dataclasses.replace(receipt, amount=receipt.amount * 10**12)


class _Exploding:
    def __init__(self, inner):
        self.inner, self.backend = inner, inner.backend

    async def evaluate(self, request):
        raise ValueError("case content: ZEBRA-SECRET")


def test_credit_is_in_quote_units_and_asset(make_app) -> None:
    gate = _ScaledGate()
    c = make_app(gate=gate)
    svc = c.app.state.service
    real, svc.client = svc.client, _Exploding(svc.client)
    body = request_body()
    quote = _quote(c, body)
    credit = post(c, "/v1/evaluate", body, **{"X-PAYMENT": _pay(quote)}).json()["credit"]
    assert int(credit["amount"]) == int(quote["amount"])  # not 10**12 times the price
    svc.client = real
    stored = svc.store.get_credit(credit["credit_id"])
    svc.store._exec(
        "UPDATE credits SET asset=? WHERE credit_id=?", ("0x" + "99" * 20, stored.credit_id)
    )
    redeem = {"credit_id": credit["credit_id"], "request": body}
    assert post(c, "/v1/credits/redeem", redeem).status_code == 402  # another asset


def test_no_retention_hides_error_messages(make_app) -> None:
    c = make_app(gate=FakeGate(), no_retention=True)
    svc = c.app.state.service
    svc.client = _Exploding(svc.client)
    body = request_body()
    r = post(c, "/v1/evaluate", body, **{"X-PAYMENT": _pay(_quote(c, body))})
    assert "ZEBRA-SECRET" not in r.text and r.json()["message"] == "ValueError"


# ---------------------------------------------------------- judge cost knobs
@pytest.mark.parametrize(
    "judge, status",
    [
        ({"provider": "openai", "model": "m", "extra": {"retries": 1000}}, 422),
        ({"provider": "openai", "model": "m", "extra": {"max_in_flight": 0}}, 422),
        ({"provider": "openai", "model": "m", "max_tokens": 1_000_000}, 413),
        ({"provider": "my-plugin", "model": "m"}, 422),
    ],
)
def test_caller_judge_config_is_bounded(make_app, judge, status) -> None:
    c = make_app()
    body = request_body(options={"judge_override": judge})
    r = post(c, "/v1/quote", body)
    assert r.status_code == status, r.text


def test_allowed_judge_models(make_app) -> None:
    c = make_app(allowed_judge_models={"openai:cheap"})
    ok = request_body(options={"judge_override": {"provider": "openai", "model": "cheap"}})
    bad = request_body(options={"judge_override": {"provider": "openai", "model": "pricey"}})
    assert post(c, "/v1/quote", ok).status_code == 200
    assert post(c, "/v1/quote", bad).status_code == 422


def test_concurrency_and_redact_limits(make_app) -> None:
    c = make_app()
    assert (
        post(c, "/v1/quote", request_body(options={"max_concurrency": 10_000})).status_code == 413
    )
    many = {"privacy": {"redact": ["a"] * 100}}
    assert post(c, "/v1/quote", request_body(options=many)).status_code == 413
    long = {"privacy": {"redact": ["a" * 1000]}}
    assert post(c, "/v1/quote", request_body(options=long)).status_code == 413
    ok = {"privacy": {"redact": ["sk-[a-z]+"]}}
    assert post(c, "/v1/quote", request_body(options=ok)).status_code == 200


# ---------------------------------------------------------- jobs and growth
def test_async_job_cap_returns_503(make_app) -> None:
    with make_app() as c:
        svc = c.app.state.service
        svc.config = dataclasses.replace(svc.config, max_running_jobs=0)
        r = post(c, "/v1/evaluations", request_body())
        assert r.status_code == 503 and r.headers["Retry-After"] == "5"


def test_expired_records_are_purged(make_app) -> None:
    c = make_app()
    svc = c.app.state.service
    assert post(c, "/v1/evaluate", request_body(), **{"Idempotency-Key": "old"}).status_code == 200
    real = svc.store.clock
    svc.store.clock = lambda: real() + 10**8
    svc._last_purge = float("-inf")
    post(c, "/v1/evaluate", request_body(stage="review"))
    rows = svc.store._exec("SELECT COUNT(*) FROM idempotency").fetchone()[0]
    assert rows == 0


# ---------------------------------------------------------- egress
class _Recorder(httpx.AsyncHTTPTransport):
    """A 'network' transport that records what would be connected to."""

    def __init__(self, body: bytes = b"{}", headers=None):
        super().__init__()
        self.seen: list[httpx.Request] = []
        self.body, self.headers = body, headers or {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        return httpx.Response(200, content=self.body, headers=self.headers)


async def test_connection_is_pinned_to_the_checked_address() -> None:
    answers = iter([["93.184.215.14"], ["127.0.0.1"]])  # a rebinding DNS server

    async def rebinding(host, port):
        return next(answers)

    inner = _Recorder()
    async with httpx.AsyncClient(transport=GuardedTransport(inner, resolver=rebinding)) as http:
        await http.get("https://agent.example.com/x")
    sent = inner.seen[0]
    assert sent.url.host == "93.184.215.14"  # connect to the checked IP, never re-resolve
    assert sent.headers["host"] == "agent.example.com"
    assert sent.extensions["sni_hostname"] == "agent.example.com"  # TLS name check unchanged


async def _public(host, port):
    return ["93.184.215.14"]


@pytest.mark.parametrize("declared", [True, False])
async def test_response_bodies_are_capped(declared) -> None:
    body = b"x" * 5000
    headers = {} if declared else {"transfer-encoding": "chunked"}
    inner = _Recorder(body, headers)
    transport = GuardedTransport(inner, resolver=_public, max_response_bytes=1000)
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(httpx.ReadError, match="1000"):
            await http.get("http://agent.example.com/")


@pytest.mark.parametrize(
    "address",
    [
        "64:ff9b::a00:5",  # NAT64 -> 10.0.0.5
        "64:ff9b::7f00:1",  # NAT64 -> 127.0.0.1
        "::7f00:1",  # IPv4-compatible loopback
        "2002:a00:5::1",  # 6to4 of 10.0.0.5
        "2001:0:4136:e378:8000:63bf:f5ff:fffe",  # Teredo -> 10.0.0.1
        "::ffff:169.254.169.254",  # mapped metadata address
    ],
)
def test_ipv6_forms_of_private_ipv4_are_blocked(address) -> None:
    assert not is_public_ip(address)
    assert is_public_ip("2606:4700:4700::1111") and is_public_ip("93.184.215.14")
