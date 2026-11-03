"""Every metric's ``result_hash`` survives a JSON round trip.

A remote client or a voter re-hydrates an :class:`EvalResponse` from JSON and re-computes its
``result_hash``. The fixture in ``test_determinism.py`` exercises a handful of
metrics; this runs every non-vendor (metric, method) of the registry through ``EvalClient`` and a
JSON round trip, so a metric whose output changes type or value on serialization (an ``int``
that becomes a ``float``, a tuple that becomes a list, a non-quantized float) is caught.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from bnbagent_eval import EvalClient, MetricConfig, Suite
from bnbagent_eval.engine.seal import compute_result_hash, verify_hashes
from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.metrics.base import Kind
from bnbagent_eval.types.request import EvalResponse

from ..contract.catalog import CONTRACT
from ..contract.test_metric_contract import IDS, PAIRS, _params


@pytest.mark.parametrize(("spec", "method"), PAIRS, ids=IDS)
async def test_result_hash_survives_json_round_trip(spec, method) -> None:
    cc = CONTRACT[spec.id]
    params = _params(spec, method)  # same skips as the contract suite (extras, vendors)
    if cc.best_runs or cc.worst_runs:
        pytest.skip("multi-run metric: needs a target (covered by the determinism fixture)")
    cfg = MetricConfig(id=spec.id, method=method.name, params=params, **cc.config)
    suite = Suite(name="rt", version="1", metrics=(cfg,))
    script = cc.judge_best if method.kind is Kind.JUDGE else None
    client = EvalClient(judge=FakeJudge(script) if script is not None else None)
    cases = [cc.best(), dataclasses.replace(cc.worst(), id="worst-case")]
    resp = await client.evaluate(suite=suite, cases=cases)
    assert any(m.status == "scored" for c in resp.results for m in c.metrics), resp.results
    wire = json.loads(json.dumps(resp.to_dict(), ensure_ascii=False))
    again = EvalResponse.from_dict(wire)
    assert compute_result_hash(again) == resp.result_hash
    assert verify_hashes(again)
