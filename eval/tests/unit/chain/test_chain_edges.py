"""Edge and failure paths of chain/*, for the coverage gate."""

from __future__ import annotations

import dataclasses

import pytest

from bnbagent_eval import EvalCase, EvalRequest, MetricConfig, Suite
from bnbagent_eval.chain import attestation, evidence
from bnbagent_eval.chain.erc8004 import SCHEMA, MetricsPublisher, public_metrics
from bnbagent_eval.chain.erc8183 import (
    DisputeAdvisor,
    EvalGate,
    VoterAssistant,
    _job_id,
    check_provider_evidence,
    job_task,
    parse_suite_criterion,
)
from bnbagent_eval.chain.manifest import MANIFEST_VERSION, parse_manifest
from bnbagent_eval.chain.onchain import abi_decode, enrich
from bnbagent_eval.engine import Runner
from bnbagent_eval.exceptions import AttestationError, ChainIntegrationError
from bnbagent_eval.ports import JobView
from bnbagent_eval.testing.fakes import FakeChainReader, FakePolicy, FakeRegistry, MemoryStorage
from bnbagent_eval.types import JobSubject, Session, Trace, TxStep

from .test_workflows import (
    CLIENT,
    PROVIDER,
    REGISTRATION,
    _gate,
    _registration_response,
    _submitted,
    _world,
)

pytest.importorskip("eth_account", reason="needs the [evm] extra")


# --------------------------------------------------------------------------- attestation


async def test_malformed_signature_is_an_attestation_error(response, signer) -> None:
    signed = attestation.sign(response, signer, now=1_000)
    bad = dataclasses.replace(
        signed, attestation=dataclasses.replace(signed.attestation, signature="0x1234")
    )
    with pytest.raises(AttestationError, match="invalid signature"):
        attestation.verify(bad, now=1_000)


# --------------------------------------------------------------------------- evidence / manifest


async def test_evidence_grade_without_score(response) -> None:
    unscored = dataclasses.replace(
        response, aggregate=dataclasses.replace(response.aggregate, score=None)
    )
    assert evidence.voter_summary(unscored)["grade"] == "N/A"


def test_manifest_requires_job_fields() -> None:
    with pytest.raises(ValueError, match="'chain_id'"):
        parse_manifest(
            {
                "version": MANIFEST_VERSION,
                "job_id": 1,
                "contracts": {},
                "response": {"content": "x"},
            }
        )


# --------------------------------------------------------------------------- onchain.enrich


async def test_abi_decode_short_calldata_and_from_address_from_transaction() -> None:
    assert abi_decode("0x1234", [{"type": "function", "name": "f", "inputs": []}]) is None
    h = "0x" + "03" * 32
    reader = FakeChainReader(block=200)
    reader.receipts[h] = {"status": 1, "gasUsed": 21_000, "blockNumber": 10}  # no "from"
    reader.transactions[h] = {"from": "0x" + "AB" * 20}
    step = TxStep(id="a", chain_id=97, to="0x" + "70" * 20, calldata="0x", tx_hash=h)
    out = await enrich(Session(id="s", traces=(Trace(id="t", steps=(step,)),)), reader)
    assert out.tx_steps()[0].from_address == "0x" + "ab" * 20


# --------------------------------------------------------------------------- ERC-8004


async def test_public_metrics_safety_average() -> None:
    suite = Suite(
        name="s",
        version="1",
        metrics=(
            MetricConfig(id="custom.python", name="safety_a", params={"fn": "score_from_meta"}),
        ),
    )
    resp = await Runner().run(
        EvalRequest(suite=suite, cases=(EvalCase(id="1", metadata={"score": 0.5}),))
    )
    agg = resp.aggregate
    renamed = dataclasses.replace(
        agg,
        metrics=(
            dataclasses.replace(agg.metrics[0], metric_id="safety.toxicity", mean=0.5),
            dataclasses.replace(agg.metrics[0], metric_id="safety.bias", mean=1.0),
        ),
    )
    assert public_metrics(dataclasses.replace(resp, aggregate=renamed))["safety"] == 0.75


async def test_publish_rejects_non_agent_subject_and_encrypts_reports(response) -> None:
    pytest.importorskip("nacl", reason="needs the [crypto] extra")
    from bnbagent_eval.privacy import decrypt_report, generate_keypair

    storage = MemoryStorage()
    registry = FakeRegistry(storage=storage)
    registry.register(5, REGISTRATION, owner=CLIENT)
    publisher = MetricsPublisher(registry, storage, clock=lambda: 1_000)
    job_resp = dataclasses.replace(response, stage="registration")  # JobSubject, not agent
    result = await publisher.publish(5, job_resp, force=True)
    assert not result.success and "subject_mismatch" in result.error

    private, public = generate_keypair()
    sealed = MetricsPublisher(registry, storage, encrypt_to=(public,), clock=lambda: 1_000)
    ok = await sealed.publish(5, await _registration_response(registry))
    assert ok.success
    stored = storage.docs[ok.report_uri]
    assert "results" not in stored  # only the envelope is stored
    assert decrypt_report(stored, private)["result_hash"] == ok.entry["result_hash"]


async def test_read_skips_foreign_entries_and_flags_unreachable_reports() -> None:
    storage = MemoryStorage()
    registry = FakeRegistry(storage=storage)
    publisher = MetricsPublisher(registry, storage, clock=lambda: 1_000)
    assert await publisher.read(404) == []  # unknown agent
    doc = {
        **REGISTRATION,
        "metrics": [
            "not-a-mapping",
            {"schema": "someone-else/v1"},
            {"schema": SCHEMA, "report": {"uri": "ipfs://gone", "hash": "0x00"}},
        ],
    }
    registry.register(6, doc, owner=CLIENT)
    entries = await publisher.read(6, verify_reports=True)
    assert len(entries) == 1 and entries[0]["report_ok"] is False


# --------------------------------------------------------------------------- ERC-8183 helpers


def test_job_helpers() -> None:
    assert parse_suite_criterion(None) is None
    job = JobView(job_id=3, client=CLIENT, provider=PROVIDER, status="funded")
    assert job_task(job) is None
    assert job_task(dataclasses.replace(job, description="plain text task")) == "plain text task"
    assert job_task(dataclasses.replace(job, description="[1, 2]")) == "[1, 2]"
    assert _job_id(job) == 3 and _job_id(4) == 4 and _job_id({"id": "5"}) == 5
    with pytest.raises(ValueError, match="job id"):
        _job_id(True)
    subject = JobSubject(97, "0x" + "81" * 20, 1, PROVIDER, "0x" + "00" * 32)
    assert check_provider_evidence(None, negotiated_suite_hash="0x1", subject=subject) == {
        "present": False
    }


# --------------------------------------------------------------------------- EvalGate


async def test_gate_returns_retry_while_running_and_rereads_state_when_done() -> None:
    storage, commerce, _, _, content = await _world()
    gate = _gate(commerce, storage, content)
    assert await gate(1) == {"retry": True}
    assert await gate(1) == {"retry": True}  # task still running
    await gate.wait()

    class StaleFirstRead:  # e.g. another process finished the job between two reads
        def __init__(self, inner):
            self.inner, self.reads = inner, 0

        def get(self, job_id):
            self.reads += 1
            record = self.inner.get(job_id)
            return dataclasses.replace(record, status="running") if self.reads == 1 else record

        def set(self, job_id, record):
            self.inner.set(job_id, record)

    gate.state = StaleFirstRead(gate.state)
    assert await gate(1) is None and gate.state.reads == 2
    assert len(commerce.submissions) == 1


@pytest.mark.parametrize(
    "work",
    [
        "async",
        "awaitable",
        "mapping",
    ],
)
async def test_gate_work_return_shapes(work) -> None:
    storage, commerce, _, _, content = await _world()
    traj = Session(id="w", traces=()).to_dict()

    async def async_work(job):
        return content

    async def coro_value():
        return {"content": content, "trajectory": traj, "metadata": {"model": "m"}}

    fns = {
        "async": async_work,
        "awaitable": lambda job: coro_value(),
        "mapping": lambda job: {"content": content, "trajectory": traj},
    }
    gate = _gate(commerce, storage, content)
    gate.work = fns[work]
    await gate(1)
    await gate.wait()
    assert gate.state.get(1).status == "submitted"
    if work == "awaitable":
        assert commerce.submissions[0]["metadata"]["model"] == "m"


async def test_gate_bad_work_result_is_retried() -> None:
    storage, commerce, _, _, content = await _world()
    gate = _gate(commerce, storage, content)
    gate.work = lambda job: 42
    await gate(1)
    await gate.wait()
    record = gate.state.get(1)
    assert record.status == "retry" and "TypeError" in record.reason


async def test_gate_abandons_when_suite_cannot_be_resolved() -> None:
    storage, commerce, _, _, content = await _world()

    async def no_suite(view, storage):
        raise ChainIntegrationError("gone", code="no_suite")

    gate = _gate(commerce, storage, content, suite_resolver=no_suite)
    await gate(1)
    await gate.wait()
    record = gate.state.get(1)
    assert record.status == "abandoned" and record.reason.startswith("suite:")


@pytest.mark.parametrize(
    "error",
    [
        ChainIntegrationError("gateway timed out", code="fetch_failed", retryable=True),
        OSError("connection reset"),
    ],
)
async def test_gate_retries_a_transient_suite_fetch_failure(error) -> None:
    storage, commerce, _, _, content = await _world()

    async def flaky(view, storage):
        raise error

    gate = _gate(commerce, storage, content, suite_resolver=flaky)
    await gate(1)
    await gate.wait()
    record = gate.state.get(1)
    assert record.status == "retry" and record.attempts == 1 and record.reason.startswith("suite:")


async def test_gate_records_a_report_upload_failure_as_a_retry() -> None:
    storage, commerce, _, _, content = await _world()

    class BrokenStorage:
        async def upload(self, data, name):
            raise OSError("storage down")

    gate = _gate(commerce, storage, content, report_storage=BrokenStorage())
    await gate(1)
    await gate.wait()
    record = gate.state.get(1)
    assert record.status == "retry" and "OSError: storage down" in record.reason
    assert record.result_hash and commerce.submissions == []
    assert all(t.done() and t.exception() is None for t in gate._tasks.values())


async def test_gate_decline_verdict_abandons() -> None:
    storage, commerce, _, _, content = await _world()

    class Declining:
        async def evaluate(self, request):
            resp = await Runner().run(
                dataclasses.replace(
                    request, cases=(dataclasses.replace(request.cases[0], expected="fees"),)
                )
            )
            verdict = dataclasses.replace(resp.verdict, action="decline", reasons=("nope",))
            return dataclasses.replace(resp, verdict=verdict)

    gate = EvalGate(commerce, storage, Declining(), lambda job: content)
    await gate(1)
    await gate.wait()
    record = gate.state.get(1)
    assert record.status == "abandoned" and record.reason == "declined: nope"
    assert record.result_hash


# --------------------------------------------------------------------------- advisors


async def test_advisors_without_deliverable() -> None:
    storage, commerce, _, _, _ = await _world()
    commerce.jobs[1] = dataclasses.replace(commerce.jobs[1], status="submitted", submitted_at=50)
    policy = FakePolicy(dispute_window=3600)
    advice = await DisputeAdvisor(commerce, policy, storage, None, clock=lambda: 100).advise(1)
    assert advice.action == "dispute" and "no retrievable deliverable" in advice.reasons


async def test_voter_rejects_missing_deliverable_and_flags_suite_mismatch(signer) -> None:
    storage, commerce, _, clock, client = await _submitted("BNB fees are low", signer=signer)
    policy = FakePolicy(voters=[signer.address], caller=signer.address)
    await policy.dispute(1)
    url = commerce.deliverable_urls[1]
    ev = storage.docs[url]["metadata"]["eval"]
    ev["suite_hash"] = "0x" + "99" * 32  # evidence for some other suite
    from bnbagent_eval.chain.manifest import manifest_hash

    commerce.jobs[1] = dataclasses.replace(
        commerce.jobs[1], deliverable=manifest_hash(storage.docs[url])
    )
    voter = VoterAssistant(commerce, policy, storage, client, signer, clock=lambda: clock["now"])
    advice = await voter.review(1)
    assert not advice.provider_evidence["suite_match"]
    assert any("different suite" in r for r in advice.reasons)
    commerce.deliverable_urls.pop(1)
    assert (await voter.review(1)).action == "vote_reject"
