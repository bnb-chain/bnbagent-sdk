"""No-retention mode (sqlite inspection + caplog) and the store's atomic operations."""

from __future__ import annotations

import json
import logging
import threading
import time

import pytest

pytest.importorskip("fastapi")

from bnbagent_eval import EvalClient  # noqa: E402
from bnbagent_eval.judges.providers.fake import FakeJudge  # noqa: E402
from bnbagent_eval.server.store import Store  # noqa: E402

from ._fakes import JUDGE_METRICS, FakeGate, payment_for, request_body  # noqa: E402

MARKER = "ZEBRA-MARKER-7f3a"


def _body() -> dict:
    return request_body(
        metrics=JUDGE_METRICS,
        cases=[{"id": "c1", "input": f"q {MARKER}", "output": f"a {MARKER}", "expected": "x"}],
        options={"privacy": {"report_contents": "full"}},
    )


def _echo_client() -> EvalClient:
    """A judge whose reasons quote the prompt, so case contents reach the response."""
    return EvalClient(judge=FakeJudge(lambda req: {"reasoning": req.user, "score": "Good"}))


def _db_text(tmp_path) -> str:
    return "".join(
        p.read_bytes().decode("latin-1") for p in tmp_path.iterdir() if p.name.startswith("svc.db")
    )


def _exercise(c) -> dict:
    body = _body()
    quote = c.post("/v1/evaluate", content=json.dumps(body)).json()["quote"]
    hdr = {"X-PAYMENT": payment_for(quote), "Idempotency-Key": "nr-1"}
    sync = c.post("/v1/evaluate", content=json.dumps(body), headers=hdr)
    assert sync.status_code == 200
    quote = c.post("/v1/evaluations", content=json.dumps(body)).json()["quote"]
    job = c.post(
        "/v1/evaluations", content=json.dumps(body), headers={"X-PAYMENT": payment_for(quote)}
    ).json()
    auth = {"Authorization": f"Bearer {job['access_token']}"}
    deadline = time.time() + 10
    while time.time() < deadline:
        status = c.get(f"/v1/evaluations/{job['job_id']}", headers=auth).json()
        if status["status"] == "done":
            return {"sync": sync.json(), "job": status}
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_no_retention_keeps_contents_out_of_store_and_logs(make_app, tmp_path, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    with make_app(gate=FakeGate(), no_retention=True, client=_echo_client()) as c:
        out = _exercise(c)
        # The caller still gets the full results (the job's from process memory only).
        assert MARKER in json.dumps(out["sync"])
        assert MARKER in json.dumps(out["job"]["result"])
        # A replay returns the stored summary: hashes, aggregate, verdict.
        body = _body()
        quote = c.post("/v1/evaluate", content=json.dumps(body)).json()["quote"]
        replay = c.post(
            "/v1/evaluate",
            content=json.dumps(body),
            headers={"Idempotency-Key": "nr-1", "X-PAYMENT": payment_for(quote)},  # same payer
        ).json()
        assert replay["retained"] is False
        assert replay["result_hash"] == out["sync"]["result_hash"]
        assert "results" not in replay
    assert MARKER not in _db_text(tmp_path)
    assert out["sync"]["result_hash"] in _db_text(tmp_path)
    assert MARKER not in caplog.text


def test_retention_mode_control(make_app, tmp_path) -> None:
    with make_app(gate=FakeGate(), client=_echo_client()) as c:
        _exercise(c)
    assert MARKER in _db_text(tmp_path)  # the inspection above is meaningful


def test_no_retention_hands_out_a_full_async_result_once(tmp_path) -> None:
    store = Store(tmp_path / "nr.db", no_retention=True)
    job_id, token = store.create_job("0xreq", 60)
    full = {"result_hash": "0xr", "verdict": {"status": "pass"}, "results": [{"case_id": MARKER}]}
    store.set_job(job_id, "done", result=full)
    assert store.get_job(job_id, token).result == full
    later = store.get_job(job_id, token).result
    assert later["retained"] is False and later["result_hash"] == "0xr" and "results" not in later
    assert MARKER not in json.dumps(store.get_job(job_id, token).result)
    store.close()


def test_reserve_is_atomic_across_threads(tmp_path) -> None:
    # Repeated, so fetches that run on the shared connection after the lock is released interleave
    # (two winners in ~1 of 6 trials).
    for trial in range(50):
        store = Store(tmp_path / f"s{trial}.db")
        results: list = []
        barrier = threading.Barrier(8)

        def worker(store=store, results=results, barrier=barrier) -> None:
            barrier.wait()
            results.append(store.reserve("k", "h", 60))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sum(r is None for r in results) == 1, f"trial {trial}"
        store.close()


def test_credit_redeem_once_concurrently_and_survives_restart(tmp_path) -> None:
    path = tmp_path / "s.db"
    credit = Store(path).issue_credit("0xabc", 5, asset="0xdef", quote_id="q")
    store = Store(path)  # restart
    assert store.get_credit(credit.credit_id).amount == 5
    wins: list[bool] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()
        wins.append(store.redeem_credit(credit.credit_id))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert wins.count(True) == 1
    assert store.get_credit(credit.credit_id).used
    store.restore_credit(credit.credit_id)
    assert store.redeem_credit(credit.credit_id)


def test_job_tokens_hashed_and_expiry(tmp_path) -> None:
    now = [1000.0]
    store = Store(tmp_path / "s.db", clock=lambda: now[0])
    job_id, token = store.create_job("0xreq", ttl_s=10)
    assert token not in _all(tmp_path)
    assert store.get_job(job_id, token).status == "queued"
    assert store.get_job(job_id, None) is None
    assert store.get_job(job_id, token + "x") is None
    now[0] += 11
    assert store.get_job(job_id, token) is None
    store.purge_expired()


def _all(tmp_path) -> str:
    return "".join(p.read_bytes().decode("latin-1") for p in tmp_path.iterdir())
