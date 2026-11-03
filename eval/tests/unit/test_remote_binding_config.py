"""``RemoteBackend`` binding checks that a re-sealed (hash-consistent) response cannot get
past, and ``EvalConfig.from_env``."""

from __future__ import annotations

import dataclasses

import pytest

from bnbagent_eval import EvalClient, EvalRequest, MetricConfig, Suite
from bnbagent_eval.client import RemoteBackend
from bnbagent_eval.config import EvalConfig
from bnbagent_eval.engine.seal import seal_hashes, verify_hashes
from bnbagent_eval.exceptions import AttestationError
from bnbagent_eval.types import AgentSubject, EvalCase

SUBJECT = AgentSubject(chain_id=97, registry="0x" + "80" * 20, agent_id=5)


def _reseal(resp, **changes):
    """A forged response whose hashes are recomputed, so only the binding checks remain."""
    draft = dataclasses.replace(resp, result_hash="", response_hash="", **changes)
    result_hash, response_hash = seal_hashes(draft)
    out = dataclasses.replace(draft, result_hash=result_hash, response_hash=response_hash)
    assert verify_hashes(out)
    return out


@pytest.fixture
def request_and_response():
    suite = Suite(name="b", version="1", metrics=(MetricConfig(id="text.exact_match"),))
    request = EvalRequest(
        stage="registration",
        suite=suite,
        cases=(EvalCase(id="1", output="a", expected="a"),),
        subject=SUBJECT,
    )
    return request, EvalClient().evaluate_sync(request)


def test_verify_accepts_the_genuine_response(request_and_response) -> None:
    request, resp = request_and_response
    RemoteBackend("http://eval.test").verify(request, resp)


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"stage": "dev"}, "stage"),
        ({"subject": dataclasses.replace(SUBJECT, agent_id=6)}, "subject"),
        ({"subject": None}, "subject"),
    ],
)
def test_resealed_binding_mismatch_raises(request_and_response, changes, match) -> None:
    request, resp = request_and_response
    with pytest.raises(AttestationError, match=match):
        RemoteBackend("http://eval.test").verify(request, _reseal(resp, **changes))


def test_response_for_another_request_raises(request_and_response) -> None:
    request, resp = request_and_response
    other = dataclasses.replace(request, cases=(EvalCase(id="1", output="b", expected="a"),))
    with pytest.raises(AttestationError, match="request_hash"):
        RemoteBackend("http://eval.test").verify(other, resp)


# ------------------------------------------------------------------ config
def test_from_env_reads_every_key(monkeypatch) -> None:
    monkeypatch.delenv("EVAL_CONFIG", raising=False)
    env = {
        "EVAL_JUDGE_PROVIDER": "bedrock",
        "EVAL_JUDGE_MODEL": "m",
        "EVAL_JUDGE_BASE_URL": "https://judge.example.com",
        "EVAL_JUDGE_REGION": "eu-west-1",
        "EVAL_JUDGE_EMBEDDING_MODEL": "e",
        "EVAL_JUDGE_TEMPERATURE": "0.3",
        "EVAL_JUDGE_MAX_TOKENS": "512",
        "EVAL_JUDGE_CACHE": "sqlite:///tmp/j.db",
        "EVAL_MAX_CONCURRENCY": "3",
        "EVAL_TIMEOUT_S": "7.5",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    cfg = EvalConfig.from_env()
    assert cfg == EvalConfig(
        judge_provider="bedrock",
        judge_model="m",
        judge_base_url="https://judge.example.com",
        judge_region="eu-west-1",
        judge_embedding_model="e",
        judge_temperature=0.3,
        judge_max_tokens=512,
        judge_cache="sqlite:///tmp/j.db",
        max_concurrency=3,
        timeout_s=7.5,
    )
    # every setting of EvalConfig has an EVAL_* key (``source`` records the config file)
    assert {f.name for f in dataclasses.fields(EvalConfig)} - {"source"} == {
        k.removeprefix("EVAL_").lower() for k in env
    }
    for k in env:
        monkeypatch.setenv(k, "")  # empty counts as unset
    assert EvalConfig.from_env() == EvalConfig()


def test_explicit_judge_overrides_env(monkeypatch) -> None:
    from bnbagent_eval.judges.providers.fake import FakeJudge

    monkeypatch.setenv("EVAL_JUDGE_PROVIDER", "openai")
    monkeypatch.setenv("EVAL_JUDGE_MODEL", "from-env")
    judge = FakeJudge({"reasoning": "r", "score": "Good"})
    suite = Suite(name="j", version="1", metrics=(MetricConfig(id="quality.fluency"),))
    resp = EvalClient(judge=judge).evaluate_sync(suite=suite, cases=[EvalCase(id="1", output="x")])
    assert resp.results[0].metrics[0].provenance.judge["model"] == "fake"
    assert len(judge.requests) == 1
