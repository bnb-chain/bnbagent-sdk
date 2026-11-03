"""ERC-8004 publisher and ERC-8183 gate / advisors against in-memory ports."""

from __future__ import annotations

import dataclasses
import inspect
import json
import sys

import pytest

from bnbagent_eval import EvalClient, EvalRequest, MetricConfig, Suite
from bnbagent_eval.chain.erc8004 import (
    METADATA_KEY_PREFIX,
    MetricsPublisher,
    public_metrics,
)
from bnbagent_eval.chain.erc8183 import (
    DisputeAdvisor,
    EvalGate,
    SqliteGateState,
    VoterAssistant,
    parse_suite_criterion,
    required_watcher_attempts,
    suite_criterion,
    suite_from_job,
)
from bnbagent_eval.chain.manifest import content_hash
from bnbagent_eval.engine import Runner, resolve_suite
from bnbagent_eval.exceptions import ChainIntegrationError
from bnbagent_eval.ports import JobView, SubmitResult
from bnbagent_eval.testing.fakes import (
    FakeCommerce,
    FakePolicy,
    FakeRegistry,
    MemoryStorage,
    decode_data_uri,
)
from bnbagent_eval.types import AgentSubject, EvalCase

pytest.importorskip("eth_account", reason="needs the [evm] extra")

CLIENT = "0x" + "c1" * 20
PROVIDER = "0x" + "d2" * 20
SUITE_DOC = {
    "name": "job-suite",
    "version": "1",
    "pass_rule": {"min_aggregate": 0.5, "min_pass_rate": 1.0},
    "metrics": [
        {"id": "text.contains", "required": True, "params": {"mode": "all"}},
        {"id": "text.length", "params": {"max_words": 30}},
    ],
}
REGISTRATION = {
    "type": "https://eips.ethereum.org/EIPS/eip-8004#registration-v1",
    "name": "summarizer",
    "services": [
        {"name": "A2A", "endpoint": "https://agent.example.com/a2a", "capabilities": ["summarize"]}
    ],
    "registrations": [{"agentId": 5, "agentRegistry": "eip155:97:0x8004"}],
    "custom_extension": {"keep": True},
}


# --------------------------------------------------------------------------- ERC-8004


async def _registration_response(
    registry, *, suite_name="reg", output="BNB fees are low", stage="registration"
):
    suite = Suite(
        name=suite_name,
        version="1",
        metrics=(
            MetricConfig(id="text.contains", required=True),
            MetricConfig(id="ops.latency", value_max=2000),
        ),
    )
    subject = AgentSubject(
        chain_id=registry.chain_id,
        registry=registry.registry_address,
        agent_id=5,
        agent_version="v1.2",
    )
    from bnbagent_eval.types import AppMetrics

    cases = tuple(
        EvalCase(
            id=str(i),
            output=output,
            expected="fees",
            app_metrics=AppMetrics(latency_ms=100 * (i + 1)),
        )
        for i in range(3)
    )
    return await Runner().run(EvalRequest(stage=stage, suite=suite, cases=cases, subject=subject))


async def test_publish_preserves_registration_and_upserts_by_suite(signer) -> None:
    storage = MemoryStorage()
    registry = FakeRegistry(storage=storage)
    registry.register(5, REGISTRATION, owner=CLIENT)
    publisher = MetricsPublisher(registry, storage, clock=lambda: 1_000)
    from bnbagent_eval.chain.attestation import sign

    first = sign(await _registration_response(registry), signer, now=1_000)
    result = await publisher.publish(5, first)
    assert result.success and result.agent_uri.startswith("data:")
    doc = decode_data_uri(registry.uris[5])
    assert doc["services"] == REGISTRATION["services"]  # capabilities kept
    assert doc["custom_extension"] == {"keep": True} and doc["registrations"]
    entry = doc["metrics"][0]
    assert entry["suite_hash"] == first.suite_hash and entry["agent_version"] == "v1.2"
    assert entry["metrics"]["overall"] == 1.0 and entry["metrics"]["latency_p95_ms"] == 290.0
    assert entry["evaluator"] == signer.address and entry["expires_at"] == 1_000 + 90 * 86400
    key = METADATA_KEY_PREFIX + first.suite_hash[2:12]
    assert registry.metadata[(5, key)] == f"{result.report_uri}#{result.report_hash}"
    assert result.report_uri in storage.docs

    second = await _registration_response(registry, suite_name="other")
    await publisher.publish(5, second)
    again = await _registration_response(registry)  # same suite as `first`: replaced
    await publisher.publish(5, again)
    entries = decode_data_uri(registry.uris[5])["metrics"]
    assert sorted(e["suite_hash"] for e in entries) == sorted(
        {first.suite_hash, second.suite_hash}
    )
    read = await publisher.read(5, verify_reports=True)
    assert len(read) == 2 and all(e["report_ok"] and not e["stale"] for e in read)


async def test_publish_guards_and_failures() -> None:
    storage = MemoryStorage()
    registry = FakeRegistry(storage=storage)
    registry.register(5, REGISTRATION)
    publisher = MetricsPublisher(registry, storage)
    failing = await _registration_response(registry, output="no match")
    assert not (await publisher.publish(5, failing)).success  # verdict fail
    dev = await _registration_response(registry, stage="dev")
    assert "not publishable" in (await publisher.publish(5, dev)).error
    assert (await publisher.publish(5, dev, force=True)).success
    wrong = dataclasses.replace(dev, subject=dataclasses.replace(dev.subject, agent_id=6))
    assert "subject_mismatch" in (await publisher.publish(5, wrong, force=True)).error
    assert (
        "not found"
        in (
            await publisher.publish(
                404,
                dataclasses.replace(dev, subject=dataclasses.replace(dev.subject, agent_id=404)),
                force=True,
            )
        ).error
    )

    before = registry.uris[5]

    async def broken(agent_id, uri):
        raise ChainIntegrationError("rpc down", code="chain_unavailable", retryable=True)

    registry.set_agent_uri = broken
    result = await publisher.publish(5, dev, force=True)
    assert not result.success and "chain_unavailable" in result.error
    assert registry.uris[5] == before  # old URI intact


async def test_publish_rehosts_non_data_uri_and_flags_stale() -> None:
    storage = MemoryStorage()
    registry = FakeRegistry(storage=storage)
    hosted = await storage.upload(REGISTRATION, "agent-5.json")
    registry.uris[5] = hosted
    publisher = MetricsPublisher(registry, storage, clock=lambda: 0)
    resp = await _registration_response(registry)
    result = await publisher.publish(5, resp, ttl_days=1)
    assert result.agent_uri.startswith("memory://") and result.agent_uri != hosted
    late = MetricsPublisher(registry, storage, clock=lambda: 2 * 86400)
    assert (await late.read(5))[0]["stale"]
    storage.docs[result.report_uri]["verdict"]["status"] = "fail"  # tamper with the report
    assert not (await late.read(5, verify_reports=True))[0]["report_ok"]


def test_public_metric_names() -> None:
    import asyncio

    resp = asyncio.run(_registration_response(FakeRegistry()))
    names = public_metrics(resp)
    assert names["overall"] == 1.0 and names["pass_rate"] == 1.0 and "latency_p95_ms" in names
    assert "safety" not in names and "accuracy" not in names


# --------------------------------------------------------------------------- ERC-8183


async def _world(
    *, content_ok: bool = True, expired_at: int = 4_000_000_000, clock_value: float = 0
):
    storage = MemoryStorage()
    suite_uri = await storage.upload(SUITE_DOC, "suite.json")
    suite_hash = resolve_suite(Suite.load(SUITE_DOC)).suite_hash
    description = json.dumps(
        {
            "task": "Summarize BNB fees",
            "terms": {"success_criteria": ["short", suite_criterion(suite_uri, suite_hash)]},
        }
    )
    clock = {"now": clock_value}
    commerce = FakeCommerce(storage=storage, clock=lambda: clock["now"])
    commerce.add_job(
        JobView(
            job_id=1,
            client=CLIENT,
            provider=PROVIDER,
            status="funded",
            expired_at=expired_at,
            description=description,
        )
    )
    good = "BNB fees are low"
    content = good if content_ok else "Ethereum is a blockchain"
    return storage, commerce, suite_hash, clock, content


def test_suite_criterion_roundtrip() -> None:
    crit = suite_criterion("ipfs://bafy/suite.json", "0x" + "ab" * 32)
    desc = {"terms": {"success_criteria": ["other", crit]}}
    assert parse_suite_criterion(desc) == ("ipfs://bafy/suite.json", "0x" + "ab" * 32)
    assert parse_suite_criterion(json.dumps(desc))[0] == "ipfs://bafy/suite.json"
    assert (
        parse_suite_criterion({"terms": {}}) is None and parse_suite_criterion("not json") is None
    )
    with pytest.raises(ValueError):
        suite_criterion("uri#frag", "0x1")


async def test_suite_from_job_rejects_hash_mismatch() -> None:
    storage, commerce, suite_hash, _, _ = await _world()
    job = await commerce.get_job(1)
    assert (await suite_from_job(job, storage)).name == "job-suite"
    uri = parse_suite_criterion(job.description)[0]
    storage.docs[uri]["metrics"][1]["params"]["max_words"] = 9999  # lenient suite swap
    with pytest.raises(ChainIntegrationError) as exc:
        await suite_from_job(job, storage)
    assert exc.value.code == "suite_hash_mismatch"
    no_suite = dataclasses.replace(job, description='{"task": "x"}')
    with pytest.raises(ChainIntegrationError) as exc:
        await suite_from_job(no_suite, storage)
    assert exc.value.code == "no_suite"


def _gate(commerce, storage, content, **kw):
    expected = {"text.contains": "fees"}

    class Client:  # evaluates with the expected answer injected (the suite's reference)
        async def evaluate(self, request):
            case = dataclasses.replace(request.cases[0], expected=expected["text.contains"])
            return await EvalClient().evaluate(dataclasses.replace(request, cases=(case,)))

    return EvalGate(commerce, storage, Client(), lambda job: content, **kw)


async def test_gate_submits_once_with_bound_evidence(signer) -> None:
    storage, commerce, suite_hash, _, content = await _world()
    gate = _gate(commerce, storage, content, signer=signer, report_storage=storage)
    assert await gate({"jobId": 1}) == {"retry": True}
    await gate.wait()
    assert await gate({"jobId": 1}) is None
    assert await gate({"jobId": 1}) is None  # fire-once: never resubmits
    assert len(commerce.submissions) == 1
    ev = commerce.submissions[0]["metadata"]["eval"]
    assert ev["suite_hash"] == suite_hash and ev["verdict"]["status"] == "pass"
    assert ev["subject"]["output_hash"] == content_hash(content)
    assert (
        ev["subject"]["provider"] == PROVIDER and ev["attestation"]["evaluator"] == signer.address
    )
    assert ev["report"]["uri"] in storage.docs
    job = await commerce.get_job(1)
    assert job.status == "submitted" and (await commerce.get_deliverable(1)).verified


async def test_gate_retries_then_abandons_on_failing_output() -> None:
    storage, commerce, _, _, content = await _world(content_ok=False)
    gate = _gate(commerce, storage, content, max_attempts=2)
    for _ in range(2):
        assert await gate(1) == {"retry": True}
        await gate.wait()
    assert await gate(1) is None
    assert commerce.submissions == [] and gate.state.get(1).status == "abandoned"


@pytest.mark.parametrize(
    ("outputs", "submitted"),
    [(["BNB fees are low"] * 3, True), (["BNB fees are low", "x", "y"], False)],
)
async def test_gate_runs_score_consistency_before_submit(outputs, submitted) -> None:
    """The prototype's N-sample pre-submit check: work() runs N times, ops.consistency gates
    the verdict, and the first deliverable is the one submitted."""
    storage, commerce, _, _, _ = await _world()
    suite = Suite(
        name="consistent",
        version="1",
        metrics=(
            MetricConfig(id="text.contains"),
            MetricConfig(id="ops.consistency", required=True, threshold=0.9, weight=0),
        ),
    )

    async def resolver(job, storage):
        return suite

    calls = iter(outputs)
    gate = _gate(commerce, storage, None, runs=3, suite_resolver=resolver)
    gate.work = lambda job: next(calls)
    assert await gate(1) == {"retry": True}
    await gate.wait()
    response = gate.last_response[1]
    assert len(response.results[0].runs) == 3
    assert [s["content"] for s in commerce.submissions] == (
        ["BNB fees are low"] if submitted else []
    )
    from bnbagent_eval.engine.targets import _TARGETS

    assert not [name for name in _TARGETS if name.startswith("evalgate-replay")]
    with pytest.raises(ValueError):
        _gate(commerce, storage, "x", runs=0)


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        (SubmitResult(success=False, error_code="chain_unavailable", retryable=True), "resubmit"),
        (SubmitResult(success=False, error_code="payload_too_large"), "abandoned"),
    ],
)
async def test_gate_maps_submit_errors(failure, status) -> None:
    storage, commerce, _, _, content = await _world()
    commerce.fail_next_submit = failure
    gate = _gate(commerce, storage, content)
    await gate(1)
    await gate.wait()
    assert gate.state.get(1).status == status


async def test_gate_resubmits_without_redoing_the_work(tmp_path) -> None:
    """A retryable submit failure resends the evaluated deliverable, also after a
    restart; it never re-runs the work or the evaluation, and does not use up an attempt."""
    storage, commerce, _, _, content = await _world()
    commerce.fail_next_submit = SubmitResult(success=False, error_code="nonce", retryable=True)
    calls = []

    def work(job):
        calls.append(job.job_id)
        return content

    state = SqliteGateState(tmp_path / "gate.db")
    gate = _gate(commerce, storage, content, max_attempts=1, state=state)
    gate.work = work
    assert await gate(1) == {"retry": True}
    await gate.wait()
    assert gate.state.get(1).status == "resubmit"
    restarted = _gate(commerce, storage, content, max_attempts=1, state=state)
    restarted.work = work
    assert await restarted(1) == {"retry": True}
    await restarted.wait()
    assert await restarted(1) is None
    assert calls == [1] and len(commerce.submissions) == 1
    assert commerce.submissions[0]["metadata"]["eval"]["result_hash"]
    record = state.get(1)
    assert record.status == "submitted" and record.attempts == 1 and record.pending is None


@pytest.mark.skipif(sys.version_info < (3, 12), reason="inspect.markcoroutinefunction")
async def test_gate_instance_is_a_coroutine_function_for_the_sdk_watcher() -> None:
    """The SDK watcher awaits on_funded only if inspect.iscoroutinefunction(on_funded)."""
    storage, commerce, _, _, content = await _world()
    assert inspect.iscoroutinefunction(_gate(commerce, storage, content))


async def test_gate_deadline_and_status_guards() -> None:
    storage, commerce, _, _, content = await _world(expired_at=1_000)
    gate = _gate(commerce, storage, content, submit_margin_s=300, clock=lambda: 800)
    assert await gate(1) is None and "deadline" in gate.state.get(1).reason
    storage, commerce, _, _, content = await _world()
    commerce.jobs[1] = dataclasses.replace(commerce.jobs[1], status="open")
    gate = _gate(commerce, storage, content)
    assert await gate(1) is None and gate.state.get(1).status == "abandoned"


async def test_gate_state_survives_restart(tmp_path) -> None:
    storage, commerce, _, _, content = await _world()
    db = tmp_path / "gate.db"
    gate = _gate(commerce, storage, content, state=SqliteGateState(db))
    await gate(1)
    await gate.wait()
    restarted = _gate(commerce, storage, content, state=SqliteGateState(db))
    assert await restarted(1) is None and len(commerce.submissions) == 1


def test_required_watcher_attempts() -> None:
    assert required_watcher_attempts(2, work_seconds=50, interval=30) == 7
    assert required_watcher_attempts(1, work_seconds=0, interval=30) == 3


async def _submitted(content: str, *, signer=None, clock_value=100):
    storage, commerce, suite_hash, clock, _ = await _world(clock_value=clock_value)
    gate = _gate(commerce, storage, content, signer=signer)
    # Submit directly (bypasses the gate's pass check) so the advisors see `content`.
    from bnbagent_eval.chain import evidence

    resp = await gate.client.evaluate(
        EvalRequest(
            stage="pre_submit",
            suite=Suite.load(SUITE_DOC),
            cases=(EvalCase(id="job-1", output=content),),
            subject=__import__(
                "bnbagent_eval.chain.erc8183", fromlist=["job_subject"]
            ).job_subject(commerce, await commerce.get_job(1), content),
        )
    )
    if signer is not None:
        from bnbagent_eval.chain.attestation import sign

        resp = sign(resp, signer, now=clock_value)
    await commerce.submit_result(1, content, {"eval": evidence.build(resp)})
    return storage, commerce, suite_hash, clock, gate.client


async def test_dispute_advisor() -> None:
    storage, commerce, _, clock, client = await _submitted("BNB fees are low")
    policy = FakePolicy(dispute_window=3600)
    advisor = DisputeAdvisor(commerce, policy, storage, client, clock=lambda: clock["now"])
    good = await advisor.advise(1)
    assert good.action == "no_action" and good.deadline == 100 + 3600
    assert good.provider_evidence["binding_ok"] and good.provider_evidence["suite_match"]
    commerce.tamper(1, "something else entirely")
    assert "tampered_deliverable" in (await advisor.advise(1)).reasons[0]
    clock["now"] = 10_000
    assert (await advisor.advise(1)).action == "too_late"
    storage, commerce, _, clock, client = await _submitted("Ethereum is a blockchain")
    bad = await DisputeAdvisor(
        commerce, policy, storage, client, clock=lambda: clock["now"]
    ).advise(1)
    assert bad.action == "dispute"
    commerce.jobs[1] = dataclasses.replace(commerce.jobs[1], status="funded")
    assert (
        await DisputeAdvisor(commerce, policy, storage, client).advise(1)
    ).action == "not_submitted"


async def test_voter_assistant(signer, other_signer) -> None:
    storage, commerce, _, clock, client = await _submitted(
        "Ethereum is a blockchain", signer=other_signer
    )
    policy = FakePolicy(voters=[signer.address], caller=signer.address)
    voter = VoterAssistant(commerce, policy, storage, client, signer, clock=lambda: clock["now"])
    assert (await voter.review(1)).action == "not_eligible"  # not disputed yet
    await policy.dispute(1)
    advice = await voter.review(1)
    assert advice.action == "vote_reject"
    assert advice.provider_evidence["attestation_ok"]
    assert advice.provider_evidence["evaluator"] == other_signer.address
    await policy.vote_reject(1)
    assert "already voted" in (await voter.review(1)).reasons
    outsider = VoterAssistant(commerce, policy, storage, client, other_signer)
    assert "not a whitelisted voter" in " ".join((await outsider.review(1)).reasons)


async def test_voter_deadline_from_the_vote_window(signer, other_signer) -> None:
    storage, commerce, _, clock, client = await _submitted(
        "Ethereum is a blockchain", signer=other_signer
    )
    submitted_at = commerce.jobs[1].submitted_at
    policy = FakePolicy(
        voters=[signer.address], caller=signer.address, dispute_window=3600, vote_window=600
    )
    await policy.dispute(1)
    voter = VoterAssistant(commerce, policy, storage, client, signer, clock=lambda: clock["now"])
    advice = await voter.review(1)
    assert advice.action == "vote_reject" and advice.deadline == submitted_at + 3600 + 600
    clock["now"] = submitted_at + 3600 + 601
    late = await voter.review(1)
    assert late.action == "too_late" and late.response is None  # no judge call spent
    assert late.deadline == submitted_at + 4200
    unlimited = FakePolicy(voters=[signer.address], caller=signer.address, vote_window=None)
    await unlimited.dispute(1)
    open_ended = VoterAssistant(
        commerce, unlimited, storage, client, signer, clock=lambda: clock["now"]
    )
    advice = await open_ended.review(1)
    assert advice.action == "vote_reject" and advice.deadline is None


async def test_voter_flags_replayed_evidence(signer) -> None:
    storage, commerce, _, clock, client = await _submitted("BNB fees are low", signer=signer)
    url = commerce.deliverable_urls[1]
    ev = storage.docs[url]["metadata"]["eval"]
    ev["subject"]["job_id"] = 99  # evidence lifted from another job
    from bnbagent_eval.chain.manifest import manifest_hash

    commerce.jobs[1] = dataclasses.replace(
        commerce.jobs[1], deliverable=manifest_hash(storage.docs[url])
    )
    policy = FakePolicy(voters=[signer.address], caller=signer.address)
    await policy.dispute(1)
    voter = VoterAssistant(commerce, policy, storage, client, signer, clock=lambda: clock["now"])
    advice = await voter.review(1)
    assert advice.action == "abstain"  # the deliverable itself is good
    assert not advice.provider_evidence["binding_ok"]
    assert "job_id" in advice.provider_evidence["binding_mismatch"]
    assert any("different job" in r for r in advice.reasons)
    assert not advice.provider_evidence[
        "attestation_ok"
    ]  # signature does not cover the edited response


async def test_client_enriches_tx_steps_from_reader() -> None:
    from bnbagent_eval.ports import ChainPorts
    from bnbagent_eval.testing.fakes import FakeChainReader
    from bnbagent_eval.types import Session, Trace, TxStep

    reader = FakeChainReader()
    tx = "0x" + "ee" * 32
    reader.receipts[tx] = {"status": 1, "gasUsed": 21000, "blockNumber": 7, "logs": []}
    session = Session(
        id="s",
        traces=(
            Trace(id="t", steps=(TxStep(id="1", chain_id=97, to="0x" + "12" * 20, tx_hash=tx),)),
        ),
    )
    suite = Suite(
        name="o",
        version="1",
        metrics=(
            MetricConfig(id="onchain.tx_success"),
            MetricConfig(id="onchain.gas_efficiency", value_max=50_000),
        ),
    )
    resp = await EvalClient(ports=ChainPorts(reader=reader)).evaluate(
        suite=suite, cases=[EvalCase(id="1", trajectory=session)]
    )
    by = {m.metric_id: m for m in resp.results[0].metrics}
    assert by["onchain.tx_success"].score == 1.0
    assert by["onchain.gas_efficiency"].value == 21000
