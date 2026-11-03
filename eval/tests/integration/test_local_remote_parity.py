"""Local ↔ remote parity over a live loopback server."""

from __future__ import annotations

import base64
import json
import threading
import time
from collections.abc import Callable, Iterator

import pytest

pytest.importorskip("fastapi")
uvicorn = pytest.importorskip("uvicorn")

import httpx  # noqa: E402

from bnbagent_eval import EvalClient  # noqa: E402
from bnbagent_eval.chain.attestation import LocalKeySigner  # noqa: E402
from bnbagent_eval.client import RemoteBackend, build_request  # noqa: E402
from bnbagent_eval.exceptions import (  # noqa: E402
    AttestationError,
    PaymentRequired,
    PolicyError,
)
from bnbagent_eval.judges.providers.fake import FakeJudge  # noqa: E402
from bnbagent_eval.server.app import create_app  # noqa: E402
from bnbagent_eval.server.config import ServiceConfig  # noqa: E402
from bnbagent_eval.server.store import Store  # noqa: E402

from ..unit.server._fakes import (  # noqa: E402
    JUDGE_METRICS,
    JUDGE_REPLY,
    PAYER,
    FakeGate,
    request_body,
)

pytestmark = pytest.mark.integration

TEST_KEY = (
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"  # secret-scan: allow
)
OTHER_ADDRESS = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"  # second Anvil dev account


class _Server:
    def __init__(self, app):
        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("uvicorn did not start")
            time.sleep(0.01)
        port = self.server.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    def __exit__(self, *exc) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


@pytest.fixture
def serve() -> Iterator[Callable[..., str]]:
    stack: list[_Server] = []

    def start(**kw) -> str:
        kw.setdefault("store", Store(":memory:"))
        kw.setdefault("config", ServiceConfig(store_path=":memory:", rate_per_s=0))
        client = kw.pop("client", None) or EvalClient(judge=FakeJudge(JUDGE_REPLY))
        srv = _Server(create_app(client, **kw))
        stack.append(srv)
        return srv.__enter__()

    yield start
    for srv in stack:
        srv.__exit__()


def _request(**kw):
    return build_request(**request_body(metrics=JUDGE_METRICS, **kw))


async def test_local_remote_parity(serve) -> None:
    url = serve()
    cases = [
        {"id": "1", "output": "Paris", "expected": "Paris"},
        {"id": "2", "output": "Lyon", "expected": "Paris"},
    ]
    request = _request(stage="review", cases=cases)
    local = await EvalClient(judge=FakeJudge(JUDGE_REPLY)).evaluate(request)
    remote = await EvalClient.remote(url).evaluate(request)
    assert remote.result_hash == local.result_hash
    assert remote.stage == "review"
    assert {s.id for s in await EvalClient.remote(url).metrics()} >= {"quality.fluency"}


async def test_remote_402_without_payer_and_with_payer(serve) -> None:
    gate = FakeGate()
    url = serve(gate=gate)
    request = _request()
    with pytest.raises(PaymentRequired) as info:
        await EvalClient.remote(url).evaluate(request)
    assert int(info.value.quote["amount"]) == 1010
    assert info.value.challenge["accepts"]

    class MockPayer:
        calls: list[dict] = []

        def request(self, url, *, max_payment, method="GET", body=None):
            self.calls.append({"url": url, "max_payment": max_payment})
            headers = {"Content-Type": "application/json"}
            with httpx.Client(trust_env=False) as http:
                r = http.request(method, url, content=body, headers=headers)
                if r.status_code == 402:
                    quote = r.json()["quote"]
                    assert int(quote["amount"]) <= max_payment
                    pay = {"nonce": "n-1", "payer": PAYER, "quote_id": quote["quote_id"]}
                    r = http.request(
                        method,
                        url,
                        content=body,
                        headers={**headers, "X-PAYMENT": json.dumps(pay)},
                    )
                receipt = json.loads(base64.b64decode(r.headers["X-PAYMENT-RESPONSE"]))
                assert receipt["payer"] == PAYER
                return r

    payer = MockPayer()
    resp = await EvalClient.remote(url, payer=payer, max_payment=5000).evaluate(request)
    assert resp.result_hash and len(gate.settled) == 1
    assert payer.calls[0]["max_payment"] == 5000
    assert "idempotency_key=" in payer.calls[0]["url"]
    with pytest.raises(ValueError, match="max_payment"):
        EvalClient.remote(url, payer=payer)


async def test_remote_payer_returning_the_sdk_payment_result(serve) -> None:
    """The SDK's ``X402Payer.request`` returns ``X402PaymentResult`` (the body verbatim in
    ``.response``, no status code), not an HTTP response; a paid call must still succeed."""
    from dataclasses import dataclass
    from typing import Any

    @dataclass(frozen=True)
    class PaymentResult:  # shape of bnbagent.x402.payer.X402PaymentResult
        success: bool
        response: Any
        amount: int | None = None

    gate = FakeGate()
    url = serve(gate=gate)
    request = _request()

    class SdkShapedPayer:
        def __init__(self, pay: bool = True):
            self.pay = pay

        def request(self, url, *, max_payment, method="GET", body=None):
            headers = {"Content-Type": "application/json"}
            with httpx.Client(trust_env=False) as http:
                r = http.request(method, url, content=body, headers=headers)
                if r.status_code == 402 and self.pay:
                    quote = r.json()["quote"]
                    pay = {"nonce": "n-sdk", "payer": PAYER, "quote_id": quote["quote_id"]}
                    headers["X-PAYMENT"] = json.dumps(pay)
                    r = http.request(method, url, content=body, headers=headers)
                return PaymentResult(success=r.status_code < 400, response=r.json())

    resp = await EvalClient.remote(url, payer=SdkShapedPayer(), max_payment=5000).evaluate(request)
    assert resp.result_hash and len(gate.settled) == 1
    # An error body without a status code still raises the matching error.
    with pytest.raises(PaymentRequired):
        await EvalClient.remote(url, payer=SdkShapedPayer(pay=False), max_payment=5000).evaluate(
            request
        )


async def test_remote_sends_idempotency_key_and_maps_policy_errors(serve) -> None:
    url = serve()
    seen: list[str | None] = []

    async def hook(request: httpx.Request) -> None:
        seen.append(request.headers.get("idempotency-key"))

    async with httpx.AsyncClient(event_hooks={"request": [hook]}, trust_env=False) as http:
        client = EvalClient.remote(url, client=http)
        await client.evaluate(_request())
        with pytest.raises(PolicyError):
            await client.evaluate(
                build_request(
                    **request_body(metrics=[{"id": "custom.python", "params": {"fn": "f"}}])
                )
            )
        quote = await client.quote(_request())
        assert quote["payment_required"] is False
    assert seen[0] and len(seen[0]) >= 16


class _Tamper(httpx.AsyncBaseTransport):
    def __init__(self, edit: Callable[[dict], None]):
        self.inner = httpx.AsyncHTTPTransport()
        self.edit = edit

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        resp = await self.inner.handle_async_request(request)
        body = json.loads(await resp.aread())
        if request.url.path == "/v1/evaluate":
            self.edit(body)
        return httpx.Response(resp.status_code, json=body)


async def _evaluate_through(url: str, edit, **backend_kw):
    async with httpx.AsyncClient(transport=_Tamper(edit)) as http:
        backend = RemoteBackend(url, client=http, **backend_kw)
        return await EvalClient(backend).evaluate(_request(stage="vote"))


def _bump_score(body: dict) -> None:
    body["aggregate"]["score"] = 1.0


def _swap_stage_rehash(body: dict) -> None:
    body["provenance"]["request_hash"] = "0x" + "00" * 32


def _forge_signature(body: dict) -> None:
    # Flip a byte of r (not the tail: v=0 and v=27 are equivalent, so rewriting the last
    # bytes could leave a valid signature).
    sig = body["attestation"]["signature"]
    i = 2 + 20  # inside r
    flipped = f"{int(sig[i : i + 2], 16) ^ 0xFF:02x}"
    body["attestation"]["signature"] = sig[:i] + flipped + sig[i + 2 :]


async def test_remote_hash_and_attestation_verification(serve) -> None:
    pytest.importorskip("eth_account", reason="signing needs the [evm] extra")
    signer = LocalKeySigner(TEST_KEY)
    url = serve(signer=signer)
    ok = await _evaluate_through(url, lambda b: None, expected_evaluator=signer.address)
    assert ok.attestation is not None and ok.attestation.evaluator == signer.address
    with pytest.raises(AttestationError, match="result_hash"):
        await _evaluate_through(url, _bump_score)
    with pytest.raises(AttestationError):
        await _evaluate_through(url, _swap_stage_rehash)
    with pytest.raises(AttestationError):
        await _evaluate_through(url, _forge_signature)
    with pytest.raises(AttestationError, match="unexpected evaluator"):
        await _evaluate_through(url, lambda b: None, expected_evaluator=OTHER_ADDRESS)
    # verify_attestation=False skips only the signature, never the hashes.
    await _evaluate_through(url, _forge_signature, verify_attestation=False)
