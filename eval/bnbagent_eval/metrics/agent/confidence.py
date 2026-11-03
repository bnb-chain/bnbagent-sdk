"""``agent.confidence``: LLM-free trajectory confidence.

Wraps :class:`bnbagent_eval.analysis.confidence.ConfidenceScorer` (a port of AgentSight
``ConfidenceScorer``): score = confidence, details carry the risk level, the 6 derived scores,
the 6 risk flags, the explanation and the trace insights. When no ``AgentStep`` in the
trajectory carries a prompt / response, the case input / output stand in for them.
"""

from __future__ import annotations

from typing import Any

from ...analysis.confidence import PROFILES, RISK_FLAGS, SCORE_NAMES, ConfidenceScorer
from ...types.case import EvalCase, content_text
from ..base import BaseMetric, Kind, MethodSpec, MetricContext, MetricSpec, Outcome
from ..registry import register

__all__ = ["PROFILES", "RISK_FLAGS", "SCORE_NAMES", "Confidence"]

_CONFIDENCE_LIMITS = ("max_repeated_calls", "max_depth", "max_error_rate")


def _confidence_scorer(params: dict[str, Any]) -> Any:
    return ConfidenceScorer(
        profile=params.get("profile", "general"),
        weight_overrides=params.get("weight_overrides"),
        risk_exponent=params.get("risk_exponent"),
        limits={k: params[k] for k in _CONFIDENCE_LIMITS if k in params},
    )


@register
class Confidence(BaseMetric):
    spec = MetricSpec(
        id="agent.confidence",
        category="agent",
        description="Deterministic trajectory confidence from 6 weighted risk scores "
        "(errors, loops, depth, relevance, hedging, deflection) with risk flags and level.",
        methods=(
            MethodSpec(
                "rule",
                Kind.RULE,
                required_inputs=frozenset({"trajectory"}),
                optional_inputs=frozenset({"input", "output"}),
            ),
        ),
        params_schema={
            "type": "object",
            "properties": {
                "profile": {"enum": sorted(PROFILES)},
                "weight_overrides": {
                    "type": "object",
                    "properties": {k: {"type": "number", "minimum": 0} for k in SCORE_NAMES},
                    "additionalProperties": False,
                },
                "risk_exponent": {"type": "number", "exclusiveMinimum": 0},
                "max_repeated_calls": {"type": "integer", "minimum": 0},
                "max_depth": {"type": "integer", "minimum": 0},
                "max_error_rate": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "additionalProperties": False,
        },
        default_threshold=0.6,
        sources=(
            "agentsight:ConfidenceScorer",
            "agentsight:SignalExtractor",
            "agentsight:ScoreDeriver",
            "agentsight:ScoreAggregator",
            "agentsight:ConfidenceSummary",
            "agentsight:insights",
            "agentsight:configs/scoring/scoring_config.yaml",
            "agentsight:configs/scoring/domain_scoring_profiles.yaml",
        ),
    )

    def _evaluate_rule(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        scorer = _confidence_scorer(dict(ctx.params))
        result = scorer.score(
            case.trajectory,
            user_prompt=content_text(case.input),
            response=content_text(case.output),
        )
        flags = result.flags
        return Outcome(
            score=result.confidence,
            reason=result.explanation.summary,
            details={
                "risk_level": result.risk_level,
                "derived_scores": result.derived.core(),
                "components": {
                    "error_rate": result.derived.error_rate,
                    "empty_result_rate": result.derived.empty_result_rate,
                    "inferred_error_rate": result.derived.inferred_error_rate,
                    "hedging_density": result.derived.hedging_density,
                    "apology_density": result.derived.apology_density,
                    "refusal_detected": result.derived.refusal_detected,
                },
                "flags": {f: flags[f] for f in RISK_FLAGS},
                "risks": list(result.risks),
                "weights": scorer.weights,
                "risk_exponent": result.explanation.risk_exponent,
                "contributions": {
                    c.score_name: c.weighted_contribution for c in result.explanation.contributions
                },
                "insights": result.insights,
            },
        )
