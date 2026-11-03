"""MLflow: params, metrics and the JSON report artifact (``[exporters]``)."""

from __future__ import annotations

import json
from typing import Any

from ..._optional import require
from ...types.request import EvalResponse
from ._common import metric_stats, report_dict, thresholds

ARTIFACT_FILE = "bnbagent_eval/report.json"


def params(response: EvalResponse) -> dict[str, str]:
    p = {
        "stage": response.stage,
        "suite_hash": response.suite_hash,
        "result_hash": response.result_hash,
        "response_hash": response.response_hash,
        "package_version": response.provenance.package_version,
        "schema_version": response.schema_version,
        "verdict": response.verdict.status,
    }
    return {k: str(v) for k, v in p.items()}


def metrics(response: EvalResponse) -> dict[str, float]:
    """Run-level metrics plus ``<metric_id>_<stat>`` (AgentSight names: ``_avg_score`` ...)."""
    agg = response.aggregate
    out: dict[str, float | None] = {
        "score": agg.score,
        "pass_rate": agg.pass_rate,
        "error_rate": agg.error_rate,
        "cases": float(agg.cases),
        "judge_calls": float(response.cost.judge_calls),
        "tokens": float(response.cost.tokens),
    }
    th = thresholds(response)
    for m in agg.metrics:
        for key, val in metric_stats(m, th.get(m.metric_id)).items():
            out[f"{m.metric_id}_{key}"] = val
    return {k: float(v) for k, v in out.items() if v is not None}


def _log(mlflow: Any, response: EvalResponse, artifact: bool, include_text: bool) -> None:
    mlflow.log_params(params(response))
    mlflow.log_metrics(metrics(response))
    if artifact:
        doc = report_dict(response, include_text=include_text)
        mlflow.log_text(json.dumps(doc, indent=2, ensure_ascii=False), ARTIFACT_FILE)


def export(
    response: EvalResponse,
    *,
    run_id: str | None = None,
    experiment: str | None = None,
    run_name: str | None = None,
    tracking_uri: str | None = None,
    artifact: bool = True,
    include_text: bool = False,
) -> str:
    """Log ``response`` into ``run_id``, the active run, or a new run (in ``experiment``).

    Returns the MLflow run id. The JSON artifact drops metric reasons and content detail keys
    unless ``include_text=True``.
    """
    mlflow = require("mlflow", "exporters")
    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)
    active = mlflow.active_run()
    if run_id is None and experiment is None and active is not None:
        _log(mlflow, response, artifact, include_text)
        return active.info.run_id
    if experiment is not None and run_id is None:
        mlflow.set_experiment(experiment)
    kwargs: dict[str, Any] = {"run_id": run_id} if run_id else {"run_name": run_name}
    with mlflow.start_run(nested=active is not None, **kwargs) as run:
        _log(mlflow, response, artifact, include_text)
        return run.info.run_id
