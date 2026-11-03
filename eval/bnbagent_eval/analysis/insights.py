"""Trace insights: per-tool / agent / model stats, hotspots, retry loops, cycles.

Port of AgentSight ``agent_lens/scoring/insights.py`` (``InsightsExtractor``) and
``types/insights.py``, returning plain JSON-serializable dicts. Adaptations to our model:

- tool -> agent attribution uses ``ToolStep.agent_id``, else the parent :class:`AgentStep`;
- model stats cover every :class:`LlmStep` (usage optional) and add cache-read / cache-write
  tokens and the per-model cache hit rate; cost comes from ``Usage.cost_usd``;
- ``inferred_errors`` per tool is computed (the source never filled it in);
- sets are emitted sorted, so the output is deterministic.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from ..types.trajectory import AgentStep, DelegationStep, HandoffStep, LlmStep, Session, ToolStep
from .confidence import (
    ScoringConfig,
    SignalExtractor,
    TraceSignals,
    by_start,
    load_scoring_config,
    matches,
    step_duration,
    tool_content,
    tool_error,
)

__all__ = ["InsightsExtractor", "insights"]


def _mean(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def _rate(n: float, d: float) -> float:
    return n / d if d > 0 else 0.0


def _lat(step) -> float:
    return step_duration(step) or 0.0


class InsightsExtractor:
    """Actionable insights, detailed stats and a summary for one session."""

    def __init__(self, config: ScoringConfig | None = None):
        self.config = config or load_scoring_config()
        internal = self.config.internal
        self.latency_hotspot_threshold = internal["latency_hotspot_threshold"]
        self.error_hotspot_threshold = internal["error_hotspot_threshold"]
        self.loop_detection_threshold = internal["loop_detection_threshold"]
        self._hedging_warning = internal["hedging_density_warning"]
        self._hedging_high = internal["hedging_density_high"]
        self._relevance_low = internal["relevance_low_threshold"]
        self._relevance_critical = internal["relevance_critical_threshold"]

    def extract(self, session: Session, signals: TraceSignals | None = None) -> dict[str, Any]:
        steps = list(session.steps())
        tools = self._tool_data(steps)
        agents = self._agent_data(steps)
        agent_tools = self._agent_tool_data(steps)
        interactions = self._interaction_data(steps)
        models = self._model_data(steps)
        return {
            "issues": self._issues(tools, agents, agent_tools, interactions, signals),
            "detailed_stats": self._detailed(
                steps, tools, agents, agent_tools, interactions, models, signals
            ),
            "summary": self._summary(steps, tools, agents, models, signals),
        }

    # -- collection -----------------------------------------------------------------------------

    def _tool_data(self, steps) -> dict[str, dict[str, Any]]:
        data: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "latencies": [],
                "errors": [],
                "empty_count": 0,
                "total": 0,
                "result_chars": [],
                "inferred_errors": 0,
            }
        )
        for s in steps:
            if not isinstance(s, ToolStep):
                continue
            d = data[s.call.name]
            content = tool_content(s)
            d["latencies"].append(_lat(s))
            d["total"] += 1
            d["result_chars"].append(len(content))
            err = tool_error(s)
            if err:
                d["errors"].append(err)
            elif content and matches("error", content):
                d["inferred_errors"] += 1
            if not content.strip():
                d["empty_count"] += 1
        return dict(data)

    def _agent_data(self, steps) -> dict[str, dict[str, Any]]:
        data: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "latencies": [],
                "tools_used": set(),
                "delegations_sent": 0,
                "delegations_received": 0,
                "handoffs_initiated": 0,
                "handoffs_received": 0,
                "invocations": 0,
            }
        )
        for s in steps:
            if isinstance(s, AgentStep):
                d = data[s.agent_id or "unknown"]
                d["latencies"].append(_lat(s))
                d["invocations"] += 1
                for tool in s.available_tools:
                    if isinstance(tool, Mapping) and tool.get("name"):
                        d["tools_used"].add(str(tool["name"]))
            elif isinstance(s, DelegationStep):
                data[s.from_agent or "unknown"]["delegations_sent"] += 1
                data[s.to_agent or "unknown"]["delegations_received"] += 1
            elif isinstance(s, HandoffStep):
                data[s.from_agent or "unknown"]["handoffs_initiated"] += 1
                data[s.to_agent or "unknown"]["handoffs_received"] += 1
        return dict(data)

    def _agent_tool_data(self, steps) -> dict[tuple[str, str], dict[str, Any]]:
        step_agent = {s.id: s.agent_id for s in steps if isinstance(s, AgentStep) and s.agent_id}
        data: dict[tuple[str, str], dict[str, Any]] = defaultdict(
            lambda: {"calls": 0, "errors": 0, "latencies": [], "error_types": []}
        )
        for s in steps:
            if not isinstance(s, ToolStep):
                continue
            agent = s.agent_id or (step_agent.get(s.parent_id) if s.parent_id else None)
            d = data[(agent or "unknown", s.call.name)]
            d["calls"] += 1
            d["latencies"].append(_lat(s))
            err = tool_error(s)
            if err:
                d["errors"] += 1
                d["error_types"].append(err)
        return dict(data)

    def _interaction_data(self, steps) -> dict[tuple[str, str, str], dict[str, Any]]:
        data: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "latencies": []}
        )
        for s in steps:
            if isinstance(s, DelegationStep | HandoffStep):
                key = (s.from_agent or "unknown", s.to_agent or "unknown", s.kind)
                data[key]["count"] += 1
                data[key]["latencies"].append(_lat(s))
        return dict(data)

    def _model_data(self, steps) -> dict[str, dict[str, Any]]:
        data: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "cost": 0.0,
                "latencies": [],
            }
        )
        for s in steps:
            if not isinstance(s, LlmStep):
                continue
            d = data[s.model or "unknown"]
            d["calls"] += 1
            d["latencies"].append(_lat(s))
            u = s.usage
            if u is None:
                continue
            d["input_tokens"] += u.input_tokens or 0
            d["output_tokens"] += u.output_tokens or 0
            d["total_tokens"] += (u.input_tokens or 0) + (u.output_tokens or 0)
            d["cache_read_tokens"] += u.cache_read_tokens or 0
            d["cache_write_tokens"] += u.cache_write_tokens or 0
            d["cost"] += u.cost_usd or 0.0
        return dict(data)

    # -- detailed stats -------------------------------------------------------------------------

    def _detailed(self, steps, tools, agents, agent_tools, interactions, models, signals):
        per_tool = []
        for name, d in tools.items():
            lats, chars, n = d["latencies"], d["result_chars"], d["total"]
            per_tool.append(
                {
                    "tool_name": name,
                    "call_count": n,
                    "success_count": n - len(d["errors"]),
                    "error_count": len(d["errors"]),
                    "error_rate": _rate(len(d["errors"]), n),
                    "empty_result_count": d["empty_count"],
                    "total_latency_ms": sum(lats),
                    "avg_latency_ms": _mean(lats),
                    "min_latency_ms": min(lats) if lats else 0.0,
                    "max_latency_ms": max(lats) if lats else 0.0,
                    "latency_std_ms": statistics.stdev(lats) if len(lats) > 1 else 0.0,
                    "avg_result_chars": _mean(chars),
                    "total_result_chars": sum(chars),
                    "error_types": sorted(set(d["errors"])),
                    "inferred_errors": d["inferred_errors"],
                }
            )
        per_agent = []
        for agent_id, d in agents.items():
            calls = sum(v["calls"] for (a, _), v in agent_tools.items() if a == agent_id)
            errors = sum(v["errors"] for (a, _), v in agent_tools.items() if a == agent_id)
            per_agent.append(
                {
                    "agent_id": agent_id,
                    "invocation_count": d["invocations"],
                    "tools_used": sorted(d["tools_used"]),
                    "tool_call_count": calls,
                    "tool_error_count": errors,
                    "tool_error_rate": _rate(errors, calls),
                    "total_latency_ms": sum(d["latencies"]),
                    "avg_latency_ms": _mean(d["latencies"]),
                    "delegations_sent": d["delegations_sent"],
                    "delegations_received": d["delegations_received"],
                    "handoffs_initiated": d["handoffs_initiated"],
                    "handoffs_received": d["handoffs_received"],
                }
            )
        per_model = []
        for model_id, d in models.items():
            cache = d["cache_read_tokens"] + d["cache_write_tokens"]
            per_model.append(
                {
                    "model_id": model_id,
                    "call_count": d["calls"],
                    "total_input_tokens": d["input_tokens"],
                    "total_output_tokens": d["output_tokens"],
                    "total_tokens": d["total_tokens"],
                    "avg_tokens_per_call": _rate(d["total_tokens"], d["calls"]),
                    "cache_read_tokens": d["cache_read_tokens"],
                    "cache_write_tokens": d["cache_write_tokens"],
                    "cache_hit_rate": _rate(d["cache_read_tokens"], cache) if cache else None,
                    "total_cost": d["cost"] if d["cost"] > 0 else None,
                    "avg_latency_ms": _mean(d["latencies"]),
                }
            )
        matrix = [
            {
                "agent_id": agent_id,
                "tool_name": tool_name,
                "call_count": d["calls"],
                "error_count": d["errors"],
                "error_rate": _rate(d["errors"], d["calls"]),
                "avg_latency_ms": _mean(d["latencies"]),
                "total_latency_ms": sum(d["latencies"]),
            }
            for (agent_id, tool_name), d in agent_tools.items()
        ]
        interaction_matrix = [
            {
                "source_agent": src,
                "target_agent": dst,
                "interaction_type": kind,
                "count": d["count"],
                "avg_latency_ms": _mean(d["latencies"]),
            }
            for (src, dst, kind), d in interactions.items()
        ]
        latency_breakdown = token_breakdown = None
        if signals:
            total = signals.temporal.total_latency_ms
            inf = signals.temporal.inference_latency_total_ms
            tool = signals.temporal.tool_execution_latency_total_ms
            other = max(0.0, total - inf - tool)
            latency_breakdown = {
                "total_ms": total,
                "inference_ms": inf,
                "inference_pct": _rate(inf, total),
                "tool_execution_ms": tool,
                "tool_execution_pct": _rate(tool, total),
                "other_ms": other,
                "other_pct": _rate(other, total),
            }
            if signals.tokens:
                t = signals.tokens
                cache = t.total_cache_read_tokens + t.total_cache_write_tokens
                token_breakdown = {
                    "total_input": t.total_input_tokens,
                    "total_output": t.total_output_tokens,
                    "total": t.total_tokens,
                    "cache_read": t.total_cache_read_tokens,
                    "cache_write": t.total_cache_write_tokens,
                    "cache_hit_rate": _rate(t.total_cache_read_tokens, cache),
                    "tokens_per_turn": list(t.tokens_per_turn),
                    "avg_tokens_per_turn": _mean(t.tokens_per_turn),
                }
        dist: dict[str, int] = defaultdict(int)
        for s in steps:
            dist[s.kind] += 1
        agent_seq = by_start(s for s in steps if isinstance(s, AgentStep))
        return {
            "per_tool_stats": per_tool,
            "per_agent_stats": per_agent,
            "per_model_stats": per_model,
            "agent_tool_matrix": matrix,
            "agent_interaction_matrix": interaction_matrix,
            "latency_breakdown": latency_breakdown,
            "token_breakdown": token_breakdown,
            "span_type_distribution": dict(dist),
            "tool_call_sequence": list(signals.structural.tool_call_sequence) if signals else [],
            "agent_invocation_sequence": [s.agent_id or "unknown" for s in agent_seq],
        }

    # -- issues ---------------------------------------------------------------------------------

    def _issues(self, tools, agents, agent_tools, interactions, signals) -> dict[str, Any]:
        latency_hotspots: list[dict[str, Any]] = []
        error_hotspots: list[dict[str, Any]] = []
        loops: list[dict[str, Any]] = []
        failing_pairs: list[list[str]] = []
        for entity, data, count_key in (
            ("tool", tools, "total"),
            ("agent", agents, "invocations"),
        ):
            threshold = (
                _mean([x for d in data.values() for x in d["latencies"]])
                * self.latency_hotspot_threshold
            )
            for name, d in data.items():
                if d["latencies"] and _mean(d["latencies"]) > threshold > 0:
                    latency_hotspots.append(
                        {
                            "entity_type": entity,
                            "entity_name": name,
                            "avg_latency_ms": _mean(d["latencies"]),
                            "max_latency_ms": max(d["latencies"]),
                            "min_latency_ms": min(d["latencies"]),
                            "call_count": d[count_key],
                        }
                    )
        for (agent_id, tool_name), d in agent_tools.items():
            if d["calls"] > 0 and d["errors"] / d["calls"] >= self.error_hotspot_threshold:
                error_hotspots.append(
                    {
                        "tool_name": tool_name,
                        "agent_id": agent_id if agent_id != "unknown" else None,
                        "error_count": d["errors"],
                        "total_calls": d["calls"],
                        "error_rate": d["errors"] / d["calls"],
                        "error_types": sorted(set(d["error_types"])),
                    }
                )
                if agent_id != "unknown":
                    failing_pairs.append([tool_name, agent_id])
        if signals and signals.structural.repeated_tool_calls >= self.loop_detection_threshold:
            seq = list(signals.structural.tool_call_sequence)
            loops.append(
                {
                    "loop_type": "tool_retry",
                    "entities_involved": sorted(set(seq)),
                    "repetition_count": signals.structural.repeated_tool_calls,
                    "sequence": seq[-10:],
                }
            )
        slowest_tool = most_failing = slowest_agent = None
        if tools:
            slowest_tool = max(tools, key=lambda k: _mean(tools[k]["latencies"]))
            rates = {k: _rate(len(d["errors"]), d["total"]) for k, d in tools.items()}
            if max(rates.values()) > 0:
                most_failing = max(rates, key=lambda k: rates[k])
        if agents:
            slowest_agent = max(agents, key=lambda k: _mean(agents[k]["latencies"]))
        return {
            "latency_hotspots": latency_hotspots,
            "error_hotspots": error_hotspots,
            "loop_patterns": loops,
            "agent_interaction_issues": _cycles(interactions),
            "quality_issues": self._quality(signals) if signals else [],
            "slowest_tool": slowest_tool,
            "slowest_agent": slowest_agent,
            "most_failing_tool": most_failing,
            "failing_tool_agent_pairs": failing_pairs,
        }

    def _quality(self, signals: TraceSignals) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        sem, rel = signals.semantic, signals.relevance
        if sem.response_word_count > 0:
            density = sem.hedging_phrase_count / sem.response_word_count * 100
            if density > self._hedging_warning:
                severity = (
                    "high"
                    if density > self._hedging_high
                    else "medium"
                    if density > 3.0
                    else "low"
                )
                out.append(
                    _issue(
                        "high_hedging",
                        severity,
                        f"{sem.hedging_phrase_count} hedging phrases in "
                        f"{sem.response_word_count} words ({density:.1f} per 100 words)",
                    )
                )
        if sem.refusal_phrase_count > 0:
            out.append(
                _issue(
                    "task_refusal", "high", f"{sem.refusal_phrase_count} refusal phrases detected"
                )
            )
        if rel and rel.deflection_detected:
            out.append(
                _issue(
                    "deflection",
                    "high",
                    "Agent deflected from original task after acknowledging failure",
                )
            )
        if rel and not rel.low_signal and rel.query_recall < self._relevance_low:
            severity = "medium" if rel.query_recall < self._relevance_critical else "low"
            out.append(
                _issue(
                    "low_relevance",
                    severity,
                    f"Query recall: {rel.query_recall:.2f}, "
                    f"topic coherence: {rel.topic_coherence:.2f}, "
                    f"composite: {rel.composite_relevance:.2f}",
                )
            )
        return out

    # -- summary --------------------------------------------------------------------------------

    def _summary(self, steps, tools, agents, models, signals) -> dict[str, Any]:
        total_errors = sum(len(d["errors"]) for d in tools.values())
        total_calls = sum(d["total"] for d in tools.values())
        total_empty = sum(d["empty_count"] for d in tools.values())
        total_tokens = cache_hit_rate = None
        relevance, hedging, refusal, deflection = 0.0, 0.0, False, False
        if signals:
            if signals.tokens:
                t = signals.tokens
                total_tokens = t.total_tokens
                cache = t.total_cache_read_tokens + t.total_cache_write_tokens
                cache_hit_rate = t.total_cache_read_tokens / cache if cache > 0 else None
            if signals.relevance:
                relevance = signals.relevance.composite_relevance
                deflection = signals.relevance.deflection_detected
            words = signals.semantic.response_word_count
            if words > 0:
                hedging = signals.semantic.hedging_phrase_count / words * 100
            refusal = signals.semantic.refusal_phrase_count > 0
        kinds = [s.kind for s in steps]
        return {
            "total_spans": len(steps),
            "inference_spans": kinds.count("llm"),
            "tool_spans": kinds.count("tool"),
            "agent_spans": kinds.count("agent"),
            "delegation_spans": kinds.count("delegation"),
            "handoff_spans": kinds.count("handoff"),
            "tx_spans": kinds.count("tx"),
            "unique_tools": len(tools),
            "unique_agents": len(agents),
            "unique_models": len(models),
            "total_latency_ms": signals.temporal.total_latency_ms if signals else 0.0,
            "avg_span_latency_ms": signals.temporal.mean_span_latency_ms if signals else 0.0,
            "overall_error_rate": _rate(total_errors, total_calls),
            "overall_empty_rate": _rate(total_empty, total_calls),
            "tools_with_errors": sum(1 for d in tools.values() if d["errors"]),
            "total_tokens": total_tokens,
            "cache_hit_rate": cache_hit_rate,
            "total_cost_usd": sum(d["cost"] for d in models.values()) or None,
            "relevance_score": relevance,
            "hedging_density": hedging,
            "refusal_detected": refusal,
            "deflection_detected": deflection,
        }


def _issue(kind: str, severity: str, evidence: str) -> dict[str, Any]:
    return {"issue_type": kind, "agent_id": None, "severity": severity, "evidence": evidence}


def _cycles(interactions) -> list[dict[str, Any]]:
    """Circular delegation: an A -> B edge with a B -> A edge (one issue per direction)."""
    edges: dict[str, set[str]] = defaultdict(set)
    for (src, dst, _), d in interactions.items():
        if d["count"] > 0:
            edges[src].add(dst)
    out = []
    for src in edges:
        for dst in sorted(edges[src]):
            if src in edges.get(dst, set()):
                out.append(
                    {
                        "issue_type": "circular_delegation",
                        "source_agent": src,
                        "target_agent": dst,
                        "occurrence_count": 1,
                        "context": f"{src} → {dst} → {src} cycle detected",
                    }
                )
    return out


def insights(
    session: Session,
    *,
    profile: str | None = "general",
    signals: TraceSignals | None = None,
    user_prompt: str | None = None,
    response: str | None = None,
) -> dict[str, Any]:
    """JSON-serializable insights for ``session`` (``issues``, ``detailed_stats``, ``summary``)."""
    config = load_scoring_config(profile=profile)
    if signals is None:
        signals = SignalExtractor(config).extract(session, user_prompt, response)
    return InsightsExtractor(config).extract(session, signals)
