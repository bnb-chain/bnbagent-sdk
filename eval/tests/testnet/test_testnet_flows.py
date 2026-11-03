"""Opt-in BSC-testnet flows. NOT run by default; never in CI.

Run (once you have a funded testnet wallet; see docs/TESTNET.md):
    EVAL_TESTNET=1 NETWORK=bsc-testnet PRIVATE_KEY=... WALLET_PASSWORD=... \
    EVAL_TESTNET_AGENT_ID=<your agent id> pytest -m testnet tests/testnet
Secrets only come from the environment (or a git-ignored .env); nothing is written to the repo.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from bnbagent_eval import EvalCase, EvalClient, EvalRequest, MetricConfig, Suite
from bnbagent_eval.types import AgentSubject

pytestmark = [
    pytest.mark.testnet,
    pytest.mark.skipif(
        os.environ.get("EVAL_TESTNET") != "1",
        reason="set EVAL_TESTNET=1 (and wallet env vars) to run testnet flows",
    ),
]


@pytest.fixture(scope="module")
def ports():
    pytest.importorskip("bnbagent", reason="needs the [bnbagent] extra")
    from bnbagent_eval.integrations.bnbagent.env import ports_from_env

    return ports_from_env()


@pytest.fixture(autouse=True)
def allow_network(monkeypatch):
    import socket

    from tests.conftest import _real_connect

    monkeypatch.setattr(socket.socket, "connect", _real_connect)


def _storage(ports):
    """The negotiated suite is fetched from storage: the S2 flows need ``STORAGE_PROVIDER``."""
    if ports.storage is None:
        pytest.skip("set STORAGE_PROVIDER (ipfs | local) to fetch the negotiated suite")
    return ports.storage


def _client() -> EvalClient:
    """Unsigned: an env-built SDK wallet keeps the SDK's strict signing policy, which refuses the
    attestation domain (docs/TESTNET.md, "Attesting with SDK wallets"). Attestations are covered
    offline by the chain unit tests."""
    return EvalClient()


def _env_int(name: str) -> int:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"set {name}")
    return int(value)


def test_s1_evaluate_publish_read_back(ports) -> None:
    """S1: evaluate an agent you own, publish metrics to ERC-8004, read them back."""
    from bnbagent_eval.chain.erc8004 import MetricsPublisher

    agent_id = _env_int("EVAL_TESTNET_AGENT_ID")
    registry = ports.registry
    suite = Suite(
        name="testnet-smoke",
        version="1",
        metrics=(MetricConfig(id="text.contains", required=True),),
    )
    subject = AgentSubject(
        chain_id=registry.chain_id,
        registry=registry.registry_address,
        agent_id=agent_id,
        agent_version="testnet-smoke",
    )
    request = EvalRequest(
        stage="registration",
        suite=suite,
        subject=subject,
        cases=(EvalCase(id="1", output="BNB gas is cheap", expected="gas"),),
    )
    response = asyncio.run(_client().evaluate(request))
    assert response.verdict.status == "pass"
    publisher = MetricsPublisher(registry, ports.storage)
    result = asyncio.run(publisher.publish(agent_id, response))
    assert result.success, result.error
    entries = asyncio.run(publisher.read(agent_id))
    assert any(e["suite_hash"] == response.suite_hash for e in entries)


def test_s2_client_review_dry_run(ports) -> None:
    """S2 (client): dispute advice for a submitted job; read-only, sends no transaction."""
    from bnbagent_eval.chain.erc8183 import DisputeAdvisor

    job_id = _env_int("EVAL_TESTNET_JOB_ID")
    advice = asyncio.run(
        DisputeAdvisor(ports.commerce, ports.policy, _storage(ports), EvalClient()).advise(job_id)
    )
    assert advice.action in {"dispute", "no_action", "too_late", "not_submitted"}


def test_s2_provider_gate_on_funded_job(ports) -> None:
    """S2 (provider): EvalGate on a funded job whose description pins an eval_suite criterion."""
    from bnbagent_eval.chain.erc8183 import EvalGate

    job_id = _env_int("EVAL_TESTNET_PROVIDER_JOB_ID")
    content = os.environ.get("EVAL_TESTNET_DELIVERABLE", "testnet deliverable")
    storage = _storage(ports)
    gate = EvalGate(
        ports.commerce,
        storage,
        _client(),
        lambda job: content,
        report_storage=storage,
    )

    async def run() -> None:
        for _ in range(10):
            if await gate(job_id) is None:
                return
            await gate.wait()
        raise AssertionError("gate did not finish")

    asyncio.run(run())
    record = gate.state.get(job_id)
    assert record is not None and record.status == "submitted", record
