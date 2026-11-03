from __future__ import annotations

from typing import Any

from bnbagent_eval.metrics import registry
from bnbagent_eval.metrics.base import MetricContext
from bnbagent_eval.types import EvalCase, MetricConfig, MetricResult


async def run_metric(
    metric_id: str,
    case: EvalCase,
    params: dict[str, Any] | None = None,
    method: str | None = None,
    runs: tuple[EvalCase, ...] = (),
    **config: Any,
) -> MetricResult:
    cls, alias_method = registry.resolve(metric_id)
    params = params or {}
    spec_method = registry.effective_spec(cls).method(method or alias_method)
    cfg = MetricConfig(id=metric_id, params=params, **config)
    ctx = MetricContext(
        config=cfg, method=spec_method, params=params, runs=runs, language=case.language
    )
    return await cls().evaluate(case, ctx)


def case(output: Any = None, expected: Any = None, **kw: Any) -> EvalCase:
    return EvalCase(id="c", output=output, expected=expected, **kw)
