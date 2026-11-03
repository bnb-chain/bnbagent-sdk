from __future__ import annotations

import pytest

from bnbagent_eval import EvalClient
from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.server.config import ServiceConfig
from bnbagent_eval.server.policy import PolicyLimits, UntrustedSuitePolicy

from ._fakes import JUDGE_REPLY, fake_resolver


@pytest.fixture
def make_app(tmp_path):
    """``make_app(...) -> TestClient``; the service is at ``client.app.state.service``."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from bnbagent_eval.server.app import create_app

    def make(
        *,
        gate=None,
        client=None,
        no_retention: bool = False,
        limits: PolicyLimits | None = None,
        signer=None,
        rate_per_s: float = 0.0,
        rate_burst: int = 1000,
        **policy_kw,
    ) -> TestClient:
        policy = UntrustedSuitePolicy(
            limits=limits or PolicyLimits(), resolver=fake_resolver, **policy_kw
        )
        config = ServiceConfig(
            store_path=str(tmp_path / "svc.db"),
            no_retention=no_retention,
            rate_per_s=rate_per_s,
            rate_burst=rate_burst,
        )
        client = client or EvalClient(judge=FakeJudge(JUDGE_REPLY))
        app = create_app(client, gate=gate, policy=policy, config=config, signer=signer)
        return TestClient(app)

    return make
