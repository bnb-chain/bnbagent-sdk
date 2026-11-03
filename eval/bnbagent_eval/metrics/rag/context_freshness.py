"""``rag.context_freshness``: retrieved contexts are recent enough for the query.

Native method = AgentSight ContextTimeFreshnessEvaluator: timestamps are parsed and normalized
deterministically (``parse_timestamp`` / ``format_timestamp`` ported as-is), the configured
thresholds and an optional sensitivity override are appended to the system prompt, and the score
is the judge's ``overall_freshness_score`` (0-5) / 5. As extra deterministic evidence,
``details.timestamp_rules`` gives each context's age against the threshold of the effective
sensitivity (override, else the judge's classification); it does not change the score.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from ...judges.prompts import PromptTemplate
from ...judges.prompts.rag import context_freshness as prompts
from ...types.case import EvalCase, content_text
from ..base import Kind, MethodSpec, MetricContext, MetricSpec, Outcome
from ..judge_base import JudgeMetric, judge_params
from ..registry import register
from ..text._common import as_text
from ._common import combine

logger = logging.getLogger(__name__)

ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
SENSITIVITIES = prompts.SENSITIVITIES
DEFAULT_FRESHNESS_THRESHOLDS: dict[str, int | None] = {
    "CRITICAL": 1,
    "HIGH": 30,
    "MODERATE": 365,
    "LOW": None,  # no limit
}
_FORMATS = [
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d %b %Y",
    "%d %B %Y",
    "%Y-%m-%d %H:%M:%S",
]


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def parse_timestamp(value: Any) -> datetime | None:
    """Parse ISO 8601, common date strings, Unix timestamps or datetimes (naive = UTC)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return _utc(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        normalized = value.replace("Z", "+00:00")
        for fmt in _FORMATS:
            for v in (value, normalized):
                try:
                    return _utc(datetime.strptime(v, fmt))
                except ValueError:
                    continue
        try:
            return _utc(datetime.fromisoformat(normalized))
        except ValueError:
            pass
    logger.warning("Could not parse a timestamp (%d chars)", len(str(value)))  # no case data
    return None


def format_timestamp(dt: datetime | None) -> str:
    return dt.strftime(ISO_FORMAT) if dt else "No timestamp available"


def config_section(thresholds: Mapping[str, int | None], override: str | None) -> str:
    """AgentSight ``_build_system_prompt`` threshold / override sections (verbatim)."""
    lines = []
    for level in SENSITIVITIES:
        days = thresholds.get(level)
        lines.append(
            f"- {level}: No age limit" if days is None else f"- {level}: Maximum {days} days old"
        )
    section = (
        "\n\n# Configured Freshness Thresholds\n"
        "Use these specific thresholds when evaluating context age:\n"
        f"{chr(10).join(lines)}\n"
    )
    if override:
        days = thresholds.get(override, "no limit")
        section += (
            "\n# Temporal Sensitivity Override\n"
            f"The temporal sensitivity for this evaluation is pre-determined as: **{override}**\n"
            f"Do NOT classify the query's temporal sensitivity. Use {override} and apply the "
            f"corresponding threshold of {days} days.\n"
        )
    return section


def timestamp_rules(
    case: EvalCase, query_dt: datetime | None, sensitivity: str | None, thresholds: Mapping
) -> dict[str, Any]:
    """Deterministic per-context age check against the threshold of ``sensitivity``."""
    limit = thresholds.get(sensitivity) if sensitivity in SENSITIVITIES else None
    rows = []
    for i, c in enumerate(case.contexts, 1):
        dt = parse_timestamp(c.timestamp)
        age = (query_dt - dt).days if dt and query_dt else None
        if age is None or sensitivity not in SENSITIVITIES:
            within = None
        else:
            within = limit is None or age <= limit
        rows.append(
            {
                "context_id": i,
                "timestamp": format_timestamp(dt) if dt else None,
                "age_in_days": age,
                "within_threshold": within,
            }
        )
    return {
        "sensitivity": sensitivity,
        "max_age_days": limit,
        "contexts": rows,
        "missing_timestamps": sum(1 for r in rows if r["timestamp"] is None),
        "fresh_share": (
            sum(1 for r in rows if r["within_threshold"]) / len(rows) if rows else 0.0
        ),
    }


@register
class ContextFreshness(JudgeMetric):
    templates = prompts.VERSIONS
    spec = MetricSpec(
        id="rag.context_freshness",
        category="rag",
        description="Retrieved contexts are temporally fresh enough for the query (0-5 / 5).",
        methods=(
            MethodSpec(
                "native",
                Kind.JUDGE,
                required_inputs=frozenset({"input", "contexts"}),
                optional_inputs=frozenset({"output"}),
                raw_range=(0.0, 5.0),
            ),
        ),
        params_schema=judge_params(
            {
                "temporal_sensitivity": {"enum": list(SENSITIVITIES)},
                "freshness_thresholds": {
                    "type": "object",
                    "properties": {
                        level: {"type": ["integer", "null"], "minimum": 0}
                        for level in SENSITIVITIES
                    },
                    "additionalProperties": False,
                },
                "query_timestamp": {"type": ["string", "number"]},
            }
        ),
        default_threshold=0.6,
        sources=("agentsight:ContextTimeFreshnessEvaluator",),
    )

    @staticmethod
    def thresholds(params: Mapping[str, Any]) -> dict[str, int | None]:
        return {**DEFAULT_FRESHNESS_THRESHOLDS, **(params.get("freshness_thresholds") or {})}

    def template_for(self, params: Mapping[str, Any]) -> PromptTemplate:
        base = super().template_for(params)
        thresholds = self.thresholds(params)
        override = params.get("temporal_sensitivity")
        return dataclasses.replace(
            base,
            system=base.system + config_section(thresholds, override),
            extra={**base.extra, "freshness_thresholds": thresholds, "override": override},
        )

    def query_time(self, case: EvalCase, ctx: MetricContext) -> tuple[str, datetime | None]:
        raw = ctx.params.get("query_timestamp") or case.metadata.get("query_timestamp")
        if raw:
            parsed = parse_timestamp(raw)
            return (format_timestamp(parsed) if parsed else str(raw)), parsed
        now = datetime.fromtimestamp(ctx.clock(), tz=timezone.utc)
        return format_timestamp(now), now

    def fields(self, case: EvalCase, ctx: MetricContext, mode: str) -> dict[str, str]:
        contexts = "\n".join(
            f"**Context {i}:**\nPublished/Updated: "
            f"{format_timestamp(parse_timestamp(c.timestamp))}\nContent: {c.text}\n"
            for i, c in enumerate(case.contexts, 1)
        )
        answer = (content_text(case.output) or as_text(case.output)).strip()
        return {
            "input": (content_text(case.input) or as_text(case.input)).strip(),
            "query_timestamp": self.query_time(case, ctx)[0],
            "contexts": contexts,
            "answer": answer or "(No answer provided)",
        }

    async def _evaluate_native(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        template = self.template_for(ctx.params)
        mode = self.select_mode(case, ctx, template)
        samples, provenance = await self.ask(
            case, ctx, template, mode, self.fields(case, ctx, mode)
        )
        query_ts, query_dt = self.query_time(case, ctx)
        thresholds = self.thresholds(ctx.params)
        override = ctx.params.get("temporal_sensitivity")
        scored = []
        for s in samples:
            sensitivity = override or str(s.parsed.get("temporal_sensitivity", "")).upper()
            rules = timestamp_rules(case, query_dt, sensitivity, thresholds)
            scored.append((float(s.parsed["overall_freshness_score"]), {"timestamp_rules": rules}))
        return combine(
            samples,
            scored,
            provenance,
            reason_key="score_reasoning",
            label_key="temporal_sensitivity",
            query_timestamp=query_ts,
            num_contexts=len(case.contexts),
            temporal_sensitivity_override=override,
            freshness_thresholds=thresholds,
        )
