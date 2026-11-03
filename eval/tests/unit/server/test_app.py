"""HTTP service: endpoints, error codes, catalogue, policy denials, x402 flow."""

from __future__ import annotations

import base64
import json
import time

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from bnbagent_eval.engine.seal import verify_hashes  # noqa: E402
from bnbagent_eval.server import policy as policy_mod  # noqa: E402
from bnbagent_eval.server.policy import PolicyLimits  # noqa: E402
from bnbagent_eval.types import EvalResponse  # noqa: E402

from ._fakes import JUDGE_METRICS, PAYER, FakeGate, payment_for, request_body  # noqa: E402


def post(c, path, body, **headers):
    return c.post(path, content=json.dumps(body), headers=headers)


# ------------------------------------------------------------------ discovery
def test_health_catalogue_and_agent_card(make_app) -> None:
    c = make_app()
    assert c.get("/health").json()["status"] == "ok"
    ids = {m["id"] for m in c.get("/v1/metrics").json()["metrics"]}
    assert {"text.exact_match", "quality.fluency", "format.regex"} <= ids
    assert not ids & {"custom.python", "custom.human_review"}
    assert all(
        m["service_allowed"] for s in c.get("/v1/metrics").json()["metrics"] for m in s["methods"]
    )
    assert c.get("/v1/metrics/text.exact_match").json()["id"] == "text.exact_match"
    assert c.get("/v1/metrics/deepeval.pattern_match").json()["id"] == "format.regex"  # alias
    assert c.get("/v1/metrics/custom.python").status_code == 404
    assert c.get("/v1/metrics/nope.nothing").status_code == 404
    card = c.get("/.well-known/agent-card.json").json()
    assert set(card["capabilities"]["metrics"]) == ids
    assert card["url"].startswith("http://testserver")
    assert card["payment"]["x402"] is False


def test_allow_list_restricts_catalogue_and_requests(make_app) -> None:
    c = make_app(allowed_ids={"text.exact_match"})
    assert [m["id"] for m in c.get("/v1/metrics").json()["metrics"]] == ["text.exact_match"]
    r = post(c, "/v1/evaluate", request_body(metrics=[{"id": "text.token_f1"}]))
    assert r.status_code == 422 and r.json()["error"] == "policy_denied"


# ------------------------------------------------------------------- evaluate
def test_free_evaluate_keeps_stage_and_hashes(make_app) -> None:
    c = make_app()
    r = post(c, "/v1/evaluate", request_body(stage="review", metrics=JUDGE_METRICS))
    assert r.status_code == 200, r.text
    resp = EvalResponse.from_dict(r.json())
    assert resp.stage == "review"
    assert resp.verdict.action is not None  # stage-specific action computed for "review"
    assert verify_hashes(resp)
    assert "X-PAYMENT-RESPONSE" not in r.headers


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        (b"{not json", 400, "bad_request"),
        (b"[1, 2]", 400, "bad_request"),
        (json.dumps({**request_body(), "bogus": 1}).encode(), 422, "invalid_request"),
        (json.dumps(request_body(cases=[])).encode(), 422, "invalid_request"),
        (json.dumps({"cases": [{"id": "1"}]}).encode(), 422, "invalid_request"),
        (
            json.dumps(
                request_body(metrics=[{"id": "custom.python", "params": {"fn": "x"}}])
            ).encode(),
            422,
            "policy_denied",
        ),
        (
            json.dumps(request_body(metrics=[{"id": "custom.human_review"}])).encode(),
            422,
            "policy_denied",
        ),
        (json.dumps(request_body(metrics=[{"id": "code.exec"}])).encode(), 422, "policy_denied"),
        (
            json.dumps(request_body(metrics=[{"$ref": "/etc/passwd"}])).encode(),
            422,
            "policy_denied",
        ),
        (
            json.dumps({**request_body(), "suite_ref": "ipfs://x#0x00"}).encode(),
            422,
            "policy_denied",
        ),
    ],
)
def test_error_codes(make_app, body: bytes, status: int, code: str) -> None:
    r = make_app().post("/v1/evaluate", content=body)
    assert r.status_code == status, r.text
    assert r.json()["error"] == code


def test_limits_413(make_app) -> None:
    c = make_app(limits=PolicyLimits(max_request_bytes=400, max_cases=2, max_runs=1))
    big = request_body(cases=[{"id": "1", "output": "x" * 500, "expected": "x"}])
    assert post(c, "/v1/evaluate", big).status_code == 413
    many = request_body(cases=[{"id": str(i), "output": "a"} for i in range(3)])
    c2 = make_app(limits=PolicyLimits(max_cases=2))
    assert post(c2, "/v1/evaluate", many).status_code == 413
    runs = request_body(
        target={"kind": "http", "uri": "https://agent.example.com/run"}, options={"runs": 9}
    )
    assert post(c2, "/v1/evaluate", runs).status_code == 413


def test_too_many_judge_calls_413(make_app) -> None:
    c = make_app(limits=PolicyLimits(max_judge_calls=5))
    cases = [{"id": str(i), "output": "a", "expected": "a"} for i in range(6)]
    r = post(c, "/v1/evaluate", request_body(metrics=JUDGE_METRICS, cases=cases))
    assert r.status_code == 413 and "judge calls" in r.json()["message"]
    q = post(make_app(), "/v1/quote", request_body(metrics=JUDGE_METRICS, cases=cases))
    assert q.json()["judge_calls"] == 6


def test_rate_limit_429(make_app) -> None:
    c = make_app(rate_per_s=0.001, rate_burst=2)
    codes = [post(c, "/v1/quote", request_body()).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


# ---------------------------------------------------------------------- regex
def test_regex_requires_regex_module(make_app, monkeypatch) -> None:
    real = policy_mod._optional.available
    monkeypatch.setattr(policy_mod._optional, "available", lambda m: m != "regex" and real(m))
    body = request_body(metrics=[{"id": "format.regex", "params": {"pattern": "(a+)+$"}}])
    r = post(make_app(), "/v1/evaluate", body)
    assert r.status_code == 422 and "regex" in r.json()["message"]


def test_redos_pattern_times_out_and_timeout_is_capped(make_app) -> None:
    c = make_app()
    params = {"pattern": "(a|aa)+$", "timeout_ms": 50}
    case = {"id": "1", "output": "a" * 40 + "!"}
    r = post(
        c,
        "/v1/evaluate",
        request_body(metrics=[{"id": "format.regex", "params": params}], cases=[case]),
    )
    assert r.status_code == 200
    metric = r.json()["results"][0]["metrics"][0]
    assert metric["status"] == "error" and "timed out" in metric["reason"]
    params["timeout_ms"] = 5000
    r = post(c, "/v1/evaluate", request_body(metrics=[{"id": "format.regex", "params": params}]))
    assert r.status_code == 422


def test_xxe_blocked(make_app) -> None:
    xxe = '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>'
    r = post(
        make_app(),
        "/v1/evaluate",
        request_body(metrics=[{"id": "format.xml"}], cases=[{"id": "1", "output": xxe}]),
    )
    assert r.status_code == 200
    metric = r.json()["results"][0]["metrics"][0]
    assert metric["score"] in (0.0, None)
    assert "passwd" not in json.dumps(metric).replace("file:///etc/passwd", "")


# ----------------------------------------------------------------------- SSRF
@pytest.mark.parametrize(
    "target",
    [
        {"kind": "http", "uri": "http://127.0.0.1:8080/run"},
        {"kind": "http", "uri": "http://localhost/run"},
        {"kind": "http", "uri": "http://10.1.2.3/run"},
        {"kind": "http", "uri": "http://169.254.169.254/latest/meta-data"},
        {"kind": "http", "uri": "http://[::1]/run"},
        {"kind": "http", "uri": "http://evil.example.com/run"},  # DNS → private
        {"kind": "a2a", "uri": "http://rebind.example.com/.well-known/agent-card.json"},
        {"kind": "mcp", "uri": "file:///etc/passwd", "config": {"tool": "t"}},
        {"kind": "callable", "uri": "os:system"},
        {"kind": "erc8004", "uri": "97:0x0000000000000000000000000000000000000001:1"},
    ],
)
def test_ssrf_targets_denied(make_app, target) -> None:
    r = post(
        make_app(), "/v1/evaluate", request_body(target=target, cases=[{"id": "1", "input": "hi"}])
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"] == "policy_denied"


@pytest.mark.parametrize(
    "ref", ["/etc/passwd", "file:///etc/passwd", "http://10.0.0.1/cases.json", "ftp://x/y"]
)
def test_dataset_ref_must_be_public_url(make_app, ref) -> None:
    body = request_body()
    body.pop("cases")
    r = post(make_app(), "/v1/evaluate", {**body, "dataset_ref": ref})
    assert r.status_code == 422 and r.json()["error"] == "policy_denied"


def test_redirect_to_private_address_is_blocked(make_app) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        if request.url.host == "agent.example.com":
            return httpx.Response(302, headers={"location": "http://10.0.0.7/internal"})
        return httpx.Response(200, json={"output": "leaked"})

    c = make_app()
    c.app.state.service.client.backend.runner.transport_factory = lambda: httpx.MockTransport(
        handler
    )
    body = request_body(
        target={"kind": "http", "uri": "http://agent.example.com/run"},
        cases=[{"id": "1", "input": "hi", "expected": "leaked"}],
    )
    r = post(c, "/v1/evaluate", body)
    assert r.status_code == 200, r.text
    run = r.json()["results"][0]["runs"][0]
    assert "blocked" in run["error"]
    assert seen == ["agent.example.com"]  # the private hop never left the guard


def test_public_target_and_dataset_ref_work(make_app) -> None:
    from bnbagent_eval.hashing import keccak_hex

    cases = json.dumps([{"id": "1", "input": "q", "expected": "pong"}]).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "data.example.com":
            return httpx.Response(200, content=cases)
        return httpx.Response(200, json={"output": "pong"})

    c = make_app()
    svc = c.app.state.service
    svc.client.backend.runner.transport_factory = lambda: httpx.MockTransport(handler)
    svc.dataset_transport = policy_mod.GuardedTransport(
        httpx.MockTransport(handler), resolver=svc.policy.resolver
    )
    body = request_body(target={"kind": "http", "uri": "https://agent.example.com/run"})
    body.pop("cases")
    body["dataset_ref"] = f"https://data.example.com/cases.json#{keccak_hex(cases)}"
    r = post(c, "/v1/evaluate", body)
    assert r.status_code == 200, r.text
    resp = EvalResponse.from_dict(r.json())
    assert resp.aggregate.score == 1.0
    from bnbagent_eval.client import build_request
    from bnbagent_eval.hashing import hash_obj

    assert resp.provenance.request_hash == hash_obj(build_request(**body).to_dict())
    assert verify_hashes(resp)


def test_judge_base_url_denied(make_app) -> None:
    body = request_body()
    body["suite"]["judge"] = {"provider": "openai", "model": "m", "base_url": "http://10.0.0.1"}
    r = post(make_app(), "/v1/evaluate", body)
    assert r.status_code == 422 and r.json()["error"] == "policy_denied"


# ---------------------------------------------------------------------- x402
def _quote(c, body):
    r = post(c, "/v1/evaluate", body)
    assert r.status_code == 402, r.text
    return r.json()["quote"]


def test_402_challenge_then_paid_evaluation(make_app) -> None:
    gate = FakeGate()
    c = make_app(gate=gate)
    body = request_body(stage="pre_submit", metrics=JUDGE_METRICS)
    challenge = post(c, "/v1/evaluate", body).json()
    assert challenge["x402Version"] == 2 and challenge["accepts"]
    quote = challenge["quote"]
    assert int(quote["amount"]) == 1000 + 10 * 1  # one judge metric × one case
    assert gate.settled == []

    q = post(c, "/v1/quote", body).json()
    assert q["payment_required"] and q["quote"]["request_hash"] == quote["request_hash"]
    assert q["request_hash"] == quote["request_hash"]

    r = post(c, "/v1/evaluate", body, **{"X-PAYMENT": payment_for(quote)})
    assert r.status_code == 200, r.text
    receipt = json.loads(base64.b64decode(r.headers["X-PAYMENT-RESPONSE"]))
    assert receipt["payer"] == PAYER and receipt["success"] is True
    assert r.json()["stage"] == "pre_submit"
    assert len(gate.settled) == 1


def test_invalid_payment_is_402_with_challenge(make_app) -> None:
    c = make_app(gate=FakeGate())
    r = post(c, "/v1/evaluate", request_body(), **{"X-PAYMENT": payment_for({"quote_id": "q-x"})})
    assert r.status_code == 402
    assert r.json()["error"] == "quote_mismatch" and r.json()["accepts"]


def test_idempotent_retry_does_not_charge_again(make_app) -> None:
    gate = FakeGate()
    c = make_app(gate=gate)
    body = request_body()
    payment = payment_for(_quote(c, body))
    first = post(c, "/v1/evaluate", body, **{"X-PAYMENT": payment})
    again = post(c, "/v1/evaluate", body, **{"X-PAYMENT": payment})
    assert again.status_code == 200 and again.json() == first.json()
    assert again.headers["Idempotent-Replayed"] == "true"
    assert len(gate.settled) == 1
    # The same payment for a different request is a conflict, not a second evaluation.
    other = request_body(cases=[{"id": "2", "output": "b", "expected": "b"}])
    assert post(c, "/v1/evaluate", other, **{"X-PAYMENT": payment}).status_code == 409


def test_idempotency_key(make_app) -> None:
    gate = FakeGate()
    c = make_app(gate=gate)
    body = request_body()
    quote = _quote(c, body)
    key = {"Idempotency-Key": "k-1"}
    first = post(c, "/v1/evaluate", body, **{"X-PAYMENT": payment_for(quote)}, **key)
    assert first.status_code == 200
    retry = post(c, "/v1/evaluate", body, **{"X-PAYMENT": payment_for(quote)}, **key)
    assert retry.status_code == 200 and retry.json() == first.json()
    assert len(gate.settled) == 1
    other = request_body(stage="review")
    other_pay = {"X-PAYMENT": payment_for(_quote(c, other))}
    assert post(c, "/v1/evaluate", other, **other_pay, **key).status_code == 409
    # A 402 does not burn the key.
    assert post(c, "/v1/evaluate", body, **{"Idempotency-Key": "k-2"}).status_code == 402
    paid = post(
        c, "/v1/evaluate", body, **{"X-PAYMENT": payment_for(quote), "Idempotency-Key": "k-2"}
    )
    assert paid.status_code == 200


def test_nonce_replay_rejected(make_app) -> None:
    gate = FakeGate()
    c = make_app(gate=gate)
    body = request_body()
    quote = _quote(c, body)
    assert (
        post(c, "/v1/evaluate", body, **{"X-PAYMENT": payment_for(quote, "n1")}).status_code == 200
    )
    # Same nonce, different header bytes (payer field differs) → the gate rejects it.
    header = json.loads(payment_for(quote, "n1"))
    header["payer"] = "0x" + "22" * 20
    r = post(c, "/v1/evaluate", body, **{"X-PAYMENT": json.dumps(header)})
    assert r.status_code == 409 and r.json()["error"] == "nonce_replay"


class _Exploding:
    def __init__(self, inner):
        self.inner = inner
        self.backend = inner.backend

    async def evaluate(self, request):
        raise RuntimeError("judge backend down")


def test_credit_on_failure_and_redeem_once(make_app) -> None:
    gate = FakeGate()
    c = make_app(gate=gate)
    svc = c.app.state.service
    real = svc.client
    svc.client = _Exploding(real)
    body = request_body()
    r = post(c, "/v1/evaluate", body, **{"X-PAYMENT": payment_for(_quote(c, body))})
    assert r.status_code == 500
    credit = r.json()["credit"]
    assert credit["payer"] == PAYER and int(credit["amount"]) == 1000
    assert "X-PAYMENT-RESPONSE" in r.headers

    redeem = {"credit_id": credit["credit_id"], "request": body}
    failed = post(c, "/v1/credits/redeem", redeem)  # still failing: the credit is kept
    assert (
        failed.status_code == 500 and failed.json()["credit"]["credit_id"] == credit["credit_id"]
    )
    svc.client = real
    ok = post(c, "/v1/credits/redeem", redeem)
    assert ok.status_code == 200 and ok.json()["result_hash"]
    assert ok.headers["X-Credit-Redeemed"] == credit["credit_id"]
    assert post(c, "/v1/credits/redeem", redeem).status_code == 409
    assert post(c, "/v1/credits/redeem", {**redeem, "credit_id": "nope"}).status_code == 404
    assert post(c, "/v1/credits/redeem", {"credit_id": 1}).status_code == 400
    big = {**redeem, "request": request_body(metrics=JUDGE_METRICS)}
    svc.store.restore_credit(credit["credit_id"])
    assert post(c, "/v1/credits/redeem", big).status_code == 402  # costs more than the credit


# ----------------------------------------------------------------------- jobs
def _wait(c, job_id, token, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = c.get(f"/v1/evaluations/{job_id}", headers={"Authorization": f"Bearer {token}"})
        if r.json()["status"] in ("done", "failed"):
            return r
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_async_job_access_control_and_expiry(make_app) -> None:
    with make_app() as c:
        r = post(c, "/v1/evaluations", request_body(stage="vote"))
        assert r.status_code == 202, r.text
        job_id, token = r.json()["job_id"], r.json()["access_token"]
        assert len(job_id) >= 32 and len(token) >= 40
        done = _wait(c, job_id, token)
        assert done.json()["status"] == "done"
        assert done.json()["result"]["stage"] == "vote"
        assert c.get(f"/v1/evaluations/{job_id}").status_code == 404
        bad = {"Authorization": "Bearer " + "x" * 43}
        assert c.get(f"/v1/evaluations/{job_id}", headers=bad).status_code == 404
        assert c.get("/v1/evaluations/unknown", headers=bad).status_code == 404
        svc = c.app.state.service
        svc.store.clock = lambda: time.time() + 10**7
        ok = {"Authorization": f"Bearer {token}"}
        assert c.get(f"/v1/evaluations/{job_id}", headers=ok).status_code == 404  # expired


def test_async_job_paid_idempotent_rotates_token(make_app) -> None:
    gate = FakeGate()
    with make_app(gate=gate) as c:
        body = request_body()
        quote = post(c, "/v1/evaluations", body)
        assert quote.status_code == 402
        hdr = {"X-PAYMENT": payment_for(quote.json()["quote"]), "Idempotency-Key": "job-1"}
        first = post(c, "/v1/evaluations", body, **hdr)
        assert first.status_code == 202 and "X-PAYMENT-RESPONSE" in first.headers
        again = post(c, "/v1/evaluations", body, **hdr)
        assert again.status_code == 202 and again.json()["job_id"] == first.json()["job_id"]
        assert again.json()["access_token"] != first.json()["access_token"]
        assert len(gate.settled) == 1
        job_id = first.json()["job_id"]
        _wait(c, job_id, again.json()["access_token"])
        old = {"Authorization": f"Bearer {first.json()['access_token']}"}
        assert c.get(f"/v1/evaluations/{job_id}", headers=old).status_code == 404
        # The sync and async routes do not share keys.
        assert post(c, "/v1/evaluate", body, **hdr).status_code == 409


def test_async_job_failure_issues_credit(make_app) -> None:
    gate = FakeGate()
    with make_app(gate=gate) as c:
        svc = c.app.state.service
        svc.client = _Exploding(svc.client)
        body = request_body()
        quote = post(c, "/v1/evaluations", body).json()["quote"]
        r = post(c, "/v1/evaluations", body, **{"X-PAYMENT": payment_for(quote)})
        done = _wait(c, r.json()["job_id"], r.json()["access_token"])
        assert done.json()["status"] == "failed"
        assert done.json()["credit"]["payer"] == PAYER
