"""Determinism and reproducibility, the dispute-critical properties:

- identical ``result_hash`` over 10 runs and against the golden hashes pinned on Python
  3.10 / 3.11 / 3.12 by ``eval/scripts/check_hash_stability.py``;
- identical ``result_hash`` from the sqlite judge cache after a restart (new Runner, new
  process), with ``judge_source=cache:*``;
- local ↔ remote parity lives in ``tests/integration/test_local_remote_parity.py``;
- every hash-relevant suite input changes both ``result_hash`` and ``suite_hash``;
- a voter's replay of the provider's evaluation gives the same ``result_hash``.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bnbagent_eval import EvalClient
from bnbagent_eval.engine import Runner
from bnbagent_eval.judges.cache import SqliteCache
from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.metrics import registry
from bnbagent_eval.types import EvalCase, EvalRequest, Suite

from . import _fixture as fx

PACKAGE_DIR = Path(__file__).resolve().parents[2]  # eval/
# Deterministic, publicly known test key (the first Hardhat/Anvil dev account). Not a secret.
TEST_KEY = (
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"  # secret-scan: allow
)


def _subprocess_hashes(*args: str) -> dict:
    """Run the fixture in a fresh interpreter (a real restart: no shared module state)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("BNBAGENT_EVAL")}
    out = subprocess.run(
        [sys.executable, "-m", "tests.determinism._fixture", *args],
        cwd=PACKAGE_DIR,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


# ------------------------------------------------------------ repeat runs and golden hashes


async def test_same_request_same_result_hash_over_10_runs() -> None:
    responses = [await fx.evaluate() for _ in range(10)]
    assert len({r.result_hash for r in responses}) == 1
    assert len({r.suite_hash for r in responses}) == 1
    first = responses[0]
    # The fixture really mixes rule, judge (k=3), conversation and agent metrics over 3 runs.
    metrics = {m.metric_id: m for m in first.results[0].metrics}
    assert {"text.exact_match", "conversation.coherence", "agent.task_completion"} <= set(metrics)
    assert metrics["quality.relevance"].provenance.samples == fx.SAMPLES
    assert all(len(c.runs) == fx.RUNS for c in first.results)
    assert all(m.error is None for c in first.results for m in c.metrics)
    # response_hash covers timestamps, so it is not part of the determinism contract.
    assert len({r.response_hash for r in responses}) > 1


async def test_result_hash_matches_golden_pinned_across_python_versions() -> None:
    golden = json.loads(fx.GOLDEN.read_text(encoding="utf-8"))
    resp = await fx.evaluate()
    assert fx.hashes(resp) == {k: golden[k] for k in ("result_hash", "suite_hash")}, (
        "the determinism fixture's hashes changed; if intended (new metric or prompt version), "
        "re-pin with `python3 eval/scripts/check_hash_stability.py --write-golden`"
    )
    assert len(golden["checked_on"]) == 3


def test_fresh_process_gives_the_golden_hash() -> None:
    golden = json.loads(fx.GOLDEN.read_text(encoding="utf-8"))
    out = _subprocess_hashes()
    assert out["result_hash"] == golden["result_hash"]
    assert out["judge_sources"] == ["live"]


async def test_scoring_order_does_not_matter() -> None:
    # Concurrency 1 vs. the default: the scripted judge depends only on (metric, sample).
    serial = await Runner(judge=fx.make_judge()).run(
        dataclasses.replace(
            fx.build_request(),
            options=dataclasses.replace(fx.build_request().options, max_concurrency=1),
        )
    )
    parallel = await fx.evaluate()
    # max_concurrency is a request option, not part of the result.
    assert serial.result_hash == parallel.result_hash


# ---------------------------------------------------------------- judge cache after restart


async def test_sqlite_cache_same_hash_after_new_runner(tmp_path) -> None:
    db = tmp_path / "judge.db"
    live = await fx.evaluate(cache=SqliteCache(db))
    offline = FakeJudge(RuntimeError("must not be called"), model="fake-judge-v1", samples=3)
    cached = await fx.evaluate(cache=SqliteCache(db), judge=offline)
    assert offline.requests == []
    assert cached.result_hash == live.result_hash
    sources = fx.judge_sources(cached)
    assert sources and all(s.startswith("cache:") for s in sources)
    assert fx.judge_sources(live) == ["live"]


def test_sqlite_cache_same_hash_after_process_restart(tmp_path) -> None:
    db = str(tmp_path / "judge.db")
    first = _subprocess_hashes("--cache", db)
    second = _subprocess_hashes("--cache", db, "--offline")  # any judge call would error
    assert first["judge_sources"] == ["live"]
    assert len(second["judge_sources"]) == 1 and second["judge_sources"][0].startswith("cache:")
    assert second["result_hash"] == first["result_hash"]
    assert second["suite_hash"] == first["suite_hash"]


# -------------------------------------------------------------------- local / remote parity


def test_local_remote_parity_is_covered() -> None:
    parity = PACKAGE_DIR / "tests" / "integration" / "test_local_remote_parity.py"
    assert "def test_local_remote_parity" in parity.read_text(encoding="utf-8")


# ------------------------------------------------------------------------- hash sensitivity

REPLY = {"reasoning": "fine", "score": "Good"}


def _relevance_cls():
    return registry.resolve("quality.relevance")[0]


@pytest.fixture
def v2_prompt(monkeypatch):
    """Give quality.relevance a second prompt version (only the version id differs)."""
    cls = _relevance_cls()
    v2 = dataclasses.replace(cls.templates["v1"], version="v2")
    monkeypatch.setattr(cls, "templates", {**cls.templates, "v2": v2})


def _write_suite(
    tmp_path: Path,
    *,
    prompt_version: str = "v1",
    model: str = "judge-m1",
    temperature: float = 0.0,
    samples: int = 1,
    rubric: str = "Prefer answers that cite the fee level.",
) -> Suite:
    """A suite whose judge params come from a ``$ref`` file next to it."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "relevance_params.json").write_text(
        json.dumps({"prompt_version": prompt_version, "domain_rubric": rubric}),
        encoding="utf-8",
    )
    doc = {
        "name": "replay",
        "version": "1",
        "judge": {
            "provider": "fake",
            "model": model,
            "temperature": temperature,
            "samples": samples,
        },
        "metrics": [
            {"id": "text.exact_match"},
            {"id": "quality.relevance", "params": {"$ref": "relevance_params.json"}},
        ],
    }
    path = tmp_path / "suite.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return Suite.load(path)


async def _run(suite: Suite):
    judge = FakeJudge(REPLY)
    judge.config = suite.judge  # the suite pins the judge; the fake plays that model
    request = EvalRequest(
        stage="review",
        suite=suite,
        cases=(EvalCase(id="1", input="Are fees low?", output="Yes", expected="Yes"),),
    )
    return await Runner(judge=judge).run(request)


def _patch_metric_version(monkeypatch) -> None:
    cls = registry.resolve("text.exact_match")[0]
    monkeypatch.setattr(
        cls, "spec", dataclasses.replace(cls.spec, version=cls.spec.version + "+1")
    )


def _patch_prompt_text(monkeypatch) -> None:
    cls = _relevance_cls()
    v1 = cls.templates["v1"]
    edited = dataclasses.replace(v1, system=v1.system + "\nBe brief.")
    monkeypatch.setattr(cls, "templates", {**cls.templates, "v1": edited})


CHANGES = {
    "prompt_version": ({"prompt_version": "v2"}, None),
    "prompt_text_same_version": ({}, _patch_prompt_text),
    "judge_model": ({"model": "judge-m2"}, None),
    "temperature": ({"temperature": 0.7}, None),
    "samples": ({"samples": 3}, None),
    "metric_version": ({}, _patch_metric_version),
    "ref_file_content": ({"rubric": "Prefer answers that cite the exact fee."}, None),
}


@pytest.mark.parametrize("change", sorted(CHANGES))
async def test_any_hash_relevant_change_changes_both_hashes(
    change, tmp_path, monkeypatch, v2_prompt
) -> None:
    base = await _run(_write_suite(tmp_path / "base"))
    again = await _run(_write_suite(tmp_path / "again"))
    assert (again.result_hash, again.suite_hash) == (base.result_hash, base.suite_hash)
    overrides, patch = CHANGES[change]
    if patch is not None:
        patch(monkeypatch)
    changed = await _run(_write_suite(tmp_path / "changed", **overrides))
    assert changed.suite_hash != base.suite_hash
    assert changed.result_hash != base.result_hash


def test_ref_is_inlined_so_the_file_path_does_not_matter(tmp_path) -> None:
    # Two directories, same $ref content: identical suite (only the content is hashed).
    a = _write_suite(tmp_path / "a")
    b = _write_suite(tmp_path / "b")
    assert a == b and a.metrics[1].params["domain_rubric"].startswith("Prefer")


# ----------------------------------------------------------------------------- voter replay


async def test_voter_replay_of_provider_evaluation_gives_same_result_hash() -> None:
    pytest.importorskip("eth_account", reason="needs the [evm] extra")
    from bnbagent_eval.chain.attestation import LocalKeySigner

    signer = LocalKeySigner(TEST_KEY)
    from bnbagent_eval.chain import evidence
    from bnbagent_eval.chain.erc8183 import EvalGate, VoterAssistant, suite_criterion
    from bnbagent_eval.engine import resolve_suite
    from bnbagent_eval.ports import JobView
    from bnbagent_eval.testing.fakes import FakeCommerce, FakePolicy, MemoryStorage

    suite_doc = {
        "name": "job-suite",
        "version": "1",
        "pass_rule": {"min_aggregate": 0.5, "min_pass_rate": 1.0},
        "metrics": [
            {"id": "text.contains", "required": True, "params": {"mode": "any"}},
            {"id": "text.length", "params": {"max_words": 30}},
            {"id": "quality.relevance", "params": {"samples": 3}},
        ],
    }
    storage = MemoryStorage()
    suite_uri = await storage.upload(suite_doc, "suite.json")
    suite_hash = resolve_suite(Suite.load(suite_doc)).suite_hash
    description = json.dumps(
        {
            "task": "Summarize BNB fees",
            "terms": {"success_criteria": [suite_criterion(suite_uri, suite_hash)]},
        }
    )
    commerce = FakeCommerce(storage=storage, clock=lambda: 100)
    commerce.add_job(
        JobView(
            job_id=1,
            client="0x" + "c1" * 20,
            provider="0x" + "d2" * 20,
            status="funded",
            description=description,
        )
    )

    class Recording:
        """Evaluates with the suite's reference answer injected; records each request."""

        def __init__(self) -> None:
            self.client = EvalClient(judge=fx.make_judge())  # same scripted judge outputs
            self.requests: list[EvalRequest] = []

        async def evaluate(self, request: EvalRequest):
            case = dataclasses.replace(request.cases[0], expected="fees")
            request = dataclasses.replace(request, cases=(case,))
            self.requests.append(request)
            return await self.client.evaluate(request)

    provider = Recording()
    gate = EvalGate(commerce, storage, provider, lambda job: "BNB fees are low", signer=signer)
    assert await gate(1) == {"retry": True}
    await gate.wait()
    provider_resp = gate.last_response[1]
    submitted_ev = commerce.submissions[0]["metadata"]["eval"]
    assert submitted_ev["result_hash"] == provider_resp.result_hash

    policy = FakePolicy(voters=[signer.address], caller=signer.address)
    await policy.dispute(1)
    voter = Recording()
    # the job was submitted at t=100 (the commerce clock): vote inside the vote window
    voter_assistant = VoterAssistant(commerce, policy, storage, voter, signer, clock=lambda: 100)
    advice = await voter_assistant.review(1)
    assert advice.provider_evidence["attestation_ok"] and advice.provider_evidence["binding_ok"]
    vote_resp = advice.response

    # 1. Replaying the provider's exact request (as recorded in the evidence) is bit-identical.
    replay = await voter.client.evaluate(provider.requests[0])
    assert replay.result_hash == provider_resp.result_hash == submitted_ev["result_hash"]
    assert evidence.build(replay)["result_hash"] == submitted_ev["result_hash"]

    # 2. The voter's own evaluation differs only in stage: re-staged, it hashes identically.
    voter_request = voter.requests[0]
    assert voter_request.stage == "vote" and vote_resp.suite_hash == provider_resp.suite_hash
    restaged = await voter.client.evaluate(dataclasses.replace(voter_request, stage="pre_submit"))
    assert restaged.result_hash == provider_resp.result_hash

    # 3. compare(): every metric agrees exactly (same judge outputs).
    comparison = voter.client.compare(provider_resp, vote_resp)
    assert comparison.agree, comparison
