"""Amazon FMEval methods (``method=fmeval``, extra ``[fmeval]``).

Port of ETK ``FMEvalScorer``: each case becomes a one-row temp JSONL dataset (``model_input``,
``model_output``, ``target_output``) and ``get_eval_algorithm(name, config).evaluate(
dataset_config=DataConfig(...))`` runs in a worker thread. ETK passed ``score_params`` as the
config; we build the algorithm's real config object (``fmeval.eval_algorithms.<module>.<Config>``)
from the metric's params, plus the pass-through param ``fmeval_config`` (config kwargs).

Mapping (also in docs/design/metric-catalog.md). The primary score becomes the canonical score;
every FMEval score is kept in ``details.fmeval_scores``:

- ``factual_knowledge`` -> ``text.contains``: ``factual_knowledge`` (``quasi``:
  ``factual_knowledge_quasi_exact``);
- ``toxicity`` / ``qa_toxicity`` / ``summarization_toxicity`` -> ``safety.toxicity`` (inverted):
  ``toxicity``; the sub-scores (severe_toxicity, obscene, ...) are also top-level details;
- ``qa_accuracy`` -> ``text.exact_match`` (``exact_match_score``), ``text.quasi_exact_match``
  (``quasi_exact_match_score``), ``text.token_f1`` (``f1_score``; details precision / recall
  from ``precision_over_words`` / ``recall_over_words``);
- ``summarization_accuracy`` -> ``text.rouge`` / ``text.meteor`` / ``text.bertscore``
  (``rouge`` / ``meteor`` / ``bertscore``);
- ``classification_accuracy`` -> ``text.classification_accuracy``
  (``classification_accuracy_score``; balanced_accuracy / precision / recall in details).

Params: ``text.contains`` ``delimiter`` / ``mode`` (``all`` -> ``logical_operator="AND"``) /
``quasi``; ``text.rouge`` ``variant`` (-> ``rouge_type``, default rougeL as the rule method) and
``use_stemmer``; ``text.bertscore`` ``model`` (-> ``model_type_for_bertscore``);
``safety.toxicity`` ``variant`` (toxicity / qa_toxicity / summarization_toxicity) and
``model_type`` (detoxify / toxigen); ``text.classification_accuracy`` ``valid_labels``.

Aliases: ``fmeval.<algorithm>`` and ``fmeval.<algorithm>.<score>`` -> (canonical, ``fmeval``).
The alias used selects the algorithm (toxicity variants) and the primary score, e.g.
``fmeval.qa_accuracy.recall_over_words`` scores ``text.token_f1`` by recall and
``fmeval.toxicity.insult`` scores ``safety.toxicity`` by the insult sub-score.

``text.bertscore`` is created by a separate module: the method and its alias are registered only
when that metric is registered when this module loads.
"""

from __future__ import annotations

import asyncio
import json
import math
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..._optional import require
from ...types.case import EvalCase
from ...types.result import ErrorInfo
from ..base import Kind, MethodSpec, MetricContext, Outcome
from ..registry import _METRICS, add_alias, load_builtins, register_method
from ..text._common import as_text, output_text, references

OR = "<OR>"
TOXICITY_VARIANTS = ("toxicity", "qa_toxicity", "summarization_toxicity")
TOXICITY_SCORES = (
    "toxicity",
    "severe_toxicity",
    "obscene",
    "threat",
    "insult",
    "identity_attack",
    "sexual_explicit",
)
QA_SCORES = (
    "f1_score",
    "exact_match_score",
    "quasi_exact_match_score",
    "precision_over_words",
    "recall_over_words",
)
CLASSIFICATION_SCORES = (
    "classification_accuracy_score",
    "balanced_accuracy_score",
    "precision_score",
    "recall_score",
)
# Algorithm -> (config module under fmeval.eval_algorithms, config class).
CONFIGS: dict[str, tuple[str, str]] = {
    "factual_knowledge": ("factual_knowledge", "FactualKnowledgeConfig"),
    "toxicity": ("toxicity", "ToxicityConfig"),
    "qa_toxicity": ("toxicity", "ToxicityConfig"),
    "summarization_toxicity": ("toxicity", "ToxicityConfig"),
    "qa_accuracy": ("qa_accuracy", "QAAccuracyConfig"),
    "summarization_accuracy": ("summarization_accuracy", "SummarizationAccuracyConfig"),
    "classification_accuracy": ("classification_accuracy", "ClassificationAccuracyConfig"),
}

Params = Mapping[str, Any]


# --------------------------------------------------------------------------- running fmeval


def build_config(algorithm: str, kwargs: Mapping[str, Any]) -> Any:
    """The algorithm's config object, or None (fmeval defaults) when there are no kwargs."""
    if not kwargs:
        return None
    module_name, class_name = CONFIGS[algorithm]
    module = require(f"fmeval.eval_algorithms.{module_name}", "fmeval")
    return getattr(module, class_name)(**kwargs)


def run_algorithm(
    algorithm: str, config_kwargs: Mapping[str, Any], row: Mapping[str, Any]
) -> dict[str, float | None]:
    """Run one FMEval algorithm on a one-row JSONL dataset; ``{score name: value}``."""
    fm_eval = require("fmeval.eval", "fmeval")
    data_config = require("fmeval.data_loaders.data_config", "fmeval")
    config = build_config(algorithm, config_kwargs)
    evaluator = (
        fm_eval.get_eval_algorithm(algorithm)
        if config is None
        else fm_eval.get_eval_algorithm(algorithm, config)
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "dataset.jsonl"
        path.write_text(json.dumps(dict(row)) + "\n")
        outputs = evaluator.evaluate(
            dataset_config=data_config.DataConfig(
                dataset_name="single_testcase",
                dataset_uri=str(path),
                dataset_mime_type="application/jsonlines",
                model_input_location="model_input",
                model_output_location="model_output",
                target_output_location="target_output",
            )
        )
    scores: dict[str, float | None] = {}
    for output in outputs or ():
        for score in getattr(output, "dataset_scores", None) or ():
            value = score.value
            scores[str(score.name)] = None if value is None else float(value)
    return scores


def record(case: EvalCase, target: str | None) -> dict[str, Any]:
    """The JSONL row, as ETK builds it (``target_output`` only when there is one)."""
    row: dict[str, Any] = {"model_input": as_text(case.input), "model_output": output_text(case)}
    if target is not None:
        row["target_output"] = target
    return row


# --------------------------------------------------------------------------- targets


def _contains_target(case: EvalCase, params: Params) -> str:
    if isinstance(case.expected, str):
        return case.expected
    return str(params.get("delimiter", OR)).join(references(case.expected))


def _qa_target(case: EvalCase, params: Params) -> str:
    return OR.join(references(case.expected))


def _first_target(case: EvalCase, params: Params) -> str:
    return references(case.expected)[0]


def _contains_config(params: Params) -> dict[str, Any]:
    kw: dict[str, Any] = {}
    if "delimiter" in params:
        kw["target_output_delimiter"] = params["delimiter"]
    if params.get("mode") == "all":
        kw["logical_operator"] = "AND"
    return kw


def _rouge_config(params: Params) -> dict[str, Any]:
    return {
        "rouge_type": params.get("variant", "rougeL"),
        "use_stemmer_for_rouge": params.get("use_stemmer", True),
    }


def _bertscore_config(params: Params) -> dict[str, Any]:
    model = params.get("model")
    if not model:
        return {}
    if model == "deberta-xlarge-mnli":
        model = "microsoft/deberta-xlarge-mnli"
    return {"model_type_for_bertscore": model}


def _toxicity_config(params: Params) -> dict[str, Any]:
    return {"model_type": params["model_type"]} if params.get("model_type") else {}


def _classification_config(params: Params) -> dict[str, Any]:
    labels = params.get("valid_labels")
    return {"valid_labels": list(labels)} if labels else {}


def _no_config(params: Params) -> dict[str, Any]:
    return {}


@dataclass(frozen=True)
class Target:
    """How one canonical metric runs on FMEval."""

    metric_id: str
    algorithms: tuple[str, ...]  # the first one is the default
    scores: tuple[str, ...]  # the first one is the default primary score
    target: Callable[[EvalCase, Params], str] | None = _qa_target
    config: Callable[[Params], dict[str, Any]] = _no_config
    primary: Callable[[Params], str | None] = lambda params: None
    details: Mapping[str, str] = field(default_factory=dict)  # details key -> fmeval score
    inverted: bool = False

    def method(self) -> MethodSpec:
        required = {"output"} if self.target is None else {"output", "expected"}
        return MethodSpec(
            "fmeval",
            Kind.VENDOR,
            required_inputs=frozenset(required),
            optional_inputs=frozenset({"input"}),
            inverted=self.inverted,
            extra="fmeval",
            extra_module="fmeval",
        )


TARGETS: tuple[Target, ...] = (
    Target(
        "text.contains",
        ("factual_knowledge",),
        ("factual_knowledge", "factual_knowledge_quasi_exact"),
        target=_contains_target,
        config=_contains_config,
        primary=lambda p: "factual_knowledge_quasi_exact" if p.get("quasi") else None,
    ),
    Target(
        "safety.toxicity",
        TOXICITY_VARIANTS,
        TOXICITY_SCORES,
        target=None,
        config=_toxicity_config,
        inverted=True,
    ),
    Target("text.exact_match", ("qa_accuracy",), ("exact_match_score",)),
    Target("text.quasi_exact_match", ("qa_accuracy",), ("quasi_exact_match_score",)),
    Target(
        "text.token_f1",
        ("qa_accuracy",),
        ("f1_score", "precision_over_words", "recall_over_words"),
        details={"precision": "precision_over_words", "recall": "recall_over_words"},
    ),
    Target("text.rouge", ("summarization_accuracy",), ("rouge",), _first_target, _rouge_config),
    Target("text.meteor", ("summarization_accuracy",), ("meteor",), _first_target),
    Target(
        "text.bertscore",
        ("summarization_accuracy",),
        ("bertscore",),
        _first_target,
        _bertscore_config,
    ),
    Target(
        "text.classification_accuracy",
        ("classification_accuracy",),
        CLASSIFICATION_SCORES,
        _first_target,
        _classification_config,
        details={
            "balanced_accuracy": "balanced_accuracy_score",
            "precision": "precision_score",
            "recall": "recall_score",
        },
    ),
)


# --------------------------------------------------------------------------- handler


def select(target: Target, ctx: MetricContext) -> tuple[str, str]:
    """``(algorithm, primary score)`` from the alias used, then the params, then defaults."""
    params = ctx.params
    algorithm = target.algorithms[0]
    if target.metric_id == "safety.toxicity" and params.get("variant") in TOXICITY_VARIANTS:
        algorithm = params["variant"]
    primary = target.primary(params) or target.scores[0]
    parts = ctx.config.id.split(".")
    if parts[0] == "fmeval" and len(parts) >= 2 and parts[1] in target.algorithms:
        algorithm = parts[1]
        if len(parts) == 3 and parts[2] in target.scores:
            primary = parts[2]
    return algorithm, primary


def _fail(code: str, message: str, **details: Any) -> Outcome:
    return Outcome(None, status="error", error=ErrorInfo(code, message), details=details)


def make_handler(target: Target) -> Callable[..., Any]:
    async def handler(metric: Any, case: EvalCase, ctx: MetricContext) -> Outcome:
        algorithm, primary = select(target, ctx)
        row = record(case, None if target.target is None else target.target(case, ctx.params))
        extra = ctx.params.get("fmeval_config") or {}
        config = {**target.config(ctx.params), **dict(extra)}
        scores = await asyncio.to_thread(run_algorithm, algorithm, config, row)
        details: dict[str, Any] = {
            "fmeval_algorithm": algorithm,
            "score_name": primary,
            "fmeval_scores": scores,
        }
        if target.metric_id == "safety.toxicity":
            details.update({k: v for k, v in scores.items() if k != primary})
        for key, name in target.details.items():
            if name in scores:
                details[key] = scores[name]
        if not scores:
            return _fail("metric_error", f"FMEval {algorithm!r} returned no scores", **details)
        if primary not in scores:
            return _fail(
                "metric_error",
                f"FMEval {algorithm!r} returned no {primary!r} score (got {sorted(scores)})",
                **details,
            )
        value = scores[primary]
        if value is None or math.isnan(value) or math.isinf(value):
            return _fail("vendor_nan", f"FMEval {algorithm}.{primary} is {value}", **details)
        return Outcome(score=value, details=details)

    handler.__name__ = f"fmeval_{target.metric_id.replace('.', '_')}"
    return handler


# --------------------------------------------------------------------------- registration


def _attach(metric_id: str, method: MethodSpec, handler: Callable[..., Any]) -> None:
    register_method(metric_id, method, handler, params=FMEVAL_PARAMS)


load_builtins()  # canonical metrics first (re-entrant during auto-discovery)

# Alias -> canonical metric, for the algorithm-level aliases (``fmeval.<algorithm>``).
ALGORITHM_ALIASES: dict[str, str] = {
    "factual_knowledge": "text.contains",
    **dict.fromkeys(TOXICITY_VARIANTS, "safety.toxicity"),
    "qa_accuracy": "text.token_f1",
    "summarization_accuracy": "text.rouge",
    "classification_accuracy": "text.classification_accuracy",
}

# Method-specific params (merged into each target metric's schema; native params win).
FMEVAL_PARAMS: dict[str, Any] = {
    "quasi": {"type": "boolean"},
    "variant": {"type": "string"},
    "model_type": {"enum": ["detoxify", "toxigen"]},
    "model": {"type": "string"},
    "delimiter": {"type": "string"},
    "use_stemmer": {"type": "boolean"},
    "valid_labels": {"type": "array", "items": {"type": "string"}},
    "fmeval_config": {"type": "object"},
}

REGISTERED: list[str] = []
for _target in TARGETS:
    if _target.metric_id not in _METRICS:  # text.bertscore: only when its module exists
        continue
    _attach(_target.metric_id, _target.method(), make_handler(_target))
    REGISTERED.append(_target.metric_id)
    for _algorithm in _target.algorithms:
        for _score in _target.scores:
            add_alias(f"fmeval.{_algorithm}.{_score}", _target.metric_id, "fmeval")
for _algorithm, _metric_id in ALGORITHM_ALIASES.items():
    add_alias(f"fmeval.{_algorithm}", _metric_id, "fmeval")

__all__ = [
    "FMEVAL_PARAMS",
    "REGISTERED",
    "TARGETS",
    "Target",
    "build_config",
    "record",
    "run_algorithm",
]
