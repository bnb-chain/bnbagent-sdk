"""``seal_hashes`` (one serialization) equals the two-step result / response hashing."""

from __future__ import annotations

import dataclasses

from bnbagent_eval import EvalCase, EvalClient, MetricConfig, Suite
from bnbagent_eval.engine.seal import (
    compute_response_hash,
    compute_result_hash,
    seal_hashes,
    verify_hashes,
)


def test_seal_hashes_matches_two_step_hashing() -> None:
    suite = Suite(
        name="s",
        version="1",
        metrics=(MetricConfig(id="text.exact_match"), MetricConfig(id="text.token_f1")),
    )
    resp = EvalClient().evaluate_sync(
        suite=suite,
        cases=[EvalCase(id="1", output="BNB 费用", expected="BNB"), EvalCase(id="2", output="x")],
    )
    assert verify_hashes(resp)
    draft = dataclasses.replace(resp, result_hash="", response_hash="")
    result_hash = compute_result_hash(draft)
    response_hash = compute_response_hash(dataclasses.replace(draft, result_hash=result_hash))
    assert (
        seal_hashes(draft)
        == (result_hash, response_hash)
        == (
            resp.result_hash,
            resp.response_hash,
        )
    )
