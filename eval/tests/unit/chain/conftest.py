from __future__ import annotations

import pytest

from bnbagent_eval import EvalCase, JobSubject, MetricConfig, Suite
from bnbagent_eval.chain.attestation import LocalKeySigner
from bnbagent_eval.engine import Runner
from bnbagent_eval.types import EvalRequest

# Deterministic, publicly known test key (the first Hardhat/Anvil dev account). Not a secret.
TEST_KEY = (
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"  # secret-scan: allow
)
OTHER_KEY = (
    "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"  # secret-scan: allow
)


@pytest.fixture
def signer() -> LocalKeySigner:
    return LocalKeySigner(TEST_KEY)


@pytest.fixture
def other_signer() -> LocalKeySigner:
    return LocalKeySigner(OTHER_KEY)


@pytest.fixture
def job_subject() -> JobSubject:
    return JobSubject(
        chain_id=97,
        commerce="0x8181818181818181818181818181818181818181",
        job_id=7,
        provider="0x2222222222222222222222222222222222222222",
        output_hash="0x" + "ab" * 32,
    )


@pytest.fixture
async def response(job_subject):
    suite = Suite(
        name="s", version="1", metrics=(MetricConfig(id="text.exact_match", required=True),)
    )
    req = EvalRequest(
        stage="pre_submit",
        suite=suite,
        subject=job_subject,
        cases=(EvalCase(id="1", output="a", expected="a"),),
    )
    return await Runner().run(req)
