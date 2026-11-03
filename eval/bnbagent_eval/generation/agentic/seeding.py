"""Seeding from production traces (ETK ``agentic.stages.trace_extractor``) via adapters.traces.

ETK reads OTLP JSON from S3 and folds spans into turns itself; here traces are loaded with
:func:`bnbagent_eval.adapters.traces.load_trace` (any registered format) and the normalized
:class:`Session` is folded into turns: a top-level agent step (or, without agent steps, an LLM
step's last user message) opens a turn, its response closes it, and tool calls attach to the
open turn. The outcome comes from step status, not content: any step / tool error or reverted
transaction is a ``failure``; a session with no answer, or with an unfinished agent step, is an
``abandonment``; otherwise ``success``. Sampling, clustering, failure-pattern selection,
per-scenario example matching and coverage are ported from ETK.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from ...adapters.traces import available_tools, load_trace
from ...types.trajectory import AgentStep, LlmStep, Session, ToolCall, ToolStep, TxStep
from .models import (
    ExtractedConversation,
    ExtractedTurn,
    SeedExample,
    TraceCoverage,
    TraceSampling,
)

__all__ = [
    "compute_trace_coverage",
    "conversation_to_seed_example",
    "conversations_to_seed_examples",
    "extract_conversations",
    "find_matching_trace_examples",
    "select_diverse_failure_patterns",
    "session_to_conversation",
    "tools_from_sessions",
]

MAX_FAILURE_PATTERNS_FOR_PROMPT = 10  # ETK
SUCCESS, FAILURE, ABANDONMENT = "success", "failure", "abandonment"
OUTCOMES = (SUCCESS, FAILURE, ABANDONMENT)


def _last_user_message(step: LlmStep) -> str | None:
    for message in reversed(step.messages):
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            return str(message["content"])
    return None


def tag_outcome(session: Session) -> str:
    steps = list(session.steps())
    for step in steps:
        if step.error:
            return FAILURE
        if isinstance(step, ToolStep) and step.result is not None and step.result.error:
            return FAILURE
        if isinstance(step, TxStep) and step.status == 0:
            return FAILURE
    agents = [s for s in steps if isinstance(s, AgentStep) and s.parent_id is None]
    if any(s.start_ms is not None and s.end_ms is None for s in agents):
        return ABANDONMENT
    answered = any(isinstance(s, (AgentStep, LlmStep)) and s.response for s in steps)
    return SUCCESS if answered else ABANDONMENT


def session_to_conversation(session: Session) -> ExtractedConversation | None:
    """Fold a session into turns; None when it has no user message at all."""
    steps = list(session.steps())
    has_agent = any(isinstance(s, AgentStep) and s.user_prompt for s in steps)
    has_tool_steps = any(isinstance(s, ToolStep) for s in steps)
    turns: list[ExtractedTurn] = []
    user: str | None = None
    assistant: str | None = None
    calls: list[ToolCall] = []

    def flush() -> None:
        nonlocal user, assistant
        if user is not None:
            turns.append(ExtractedTurn(user, assistant, tuple(calls)))
        user, assistant = None, None
        calls.clear()

    for step in steps:
        if isinstance(step, AgentStep) and has_agent:
            if step.parent_id is None and step.user_prompt:
                flush()
                user = step.user_prompt
            if step.parent_id is None and step.response:
                assistant = step.response
        elif isinstance(step, LlmStep):
            if not has_agent:
                prompt = _last_user_message(step)
                if prompt is not None and prompt != user:
                    flush()
                    user = prompt
                if step.response:
                    assistant = step.response
            if not has_tool_steps:
                calls.extend(step.tool_calls)
        elif isinstance(step, ToolStep):
            calls.append(step.call)
    flush()
    if not turns:
        return None
    trace_id = session.traces[0].id if session.traces else session.id
    return ExtractedConversation(session.id, trace_id, tuple(turns), tag_outcome(session))


def load_sessions(seed_traces: Iterable[Any], trace_format: str = "auto") -> list[Session]:
    """Sessions from ``Session`` objects, parsed trace data or trace file paths."""
    sessions = []
    for i, item in enumerate(seed_traces):
        if isinstance(item, Session):
            sessions.append(item)
        else:
            data = Path(item) if isinstance(item, str) else item
            sessions.append(load_trace(data, trace_format, session_id=f"seed-{i}"))
    return sessions


def tools_from_sessions(sessions: Sequence[Session]) -> list[Mapping[str, Any]]:
    """Tools declared by the sessions' agent steps (first declaration wins)."""
    seen: dict[str, Mapping[str, Any]] = {}
    for session in sessions:
        for tool in available_tools(session):
            seen.setdefault(str(tool["name"]), tool)
    return list(seen.values())


def _behaviour_key(c: ExtractedConversation) -> tuple[str, ...]:
    return (c.outcome, *c.tool_sequence())


def cluster_and_select_diverse(
    conversations: Sequence[ExtractedConversation], max_per_cluster: int
) -> list[ExtractedConversation]:
    """At most ``max_per_cluster`` per (outcome, tool sequence) cluster, longest first."""
    clusters: dict[tuple[str, ...], list[ExtractedConversation]] = {}
    for c in conversations:
        clusters.setdefault(_behaviour_key(c), []).append(c)
    out: list[ExtractedConversation] = []
    for members in clusters.values():
        out.extend(sorted(members, key=lambda c: -len(c.turns))[:max_per_cluster])
    return out


def _dedupe_by_tool_sequence(
    conversations: Sequence[ExtractedConversation],
) -> list[ExtractedConversation]:
    seen: set[tuple[str, ...]] = set()
    out = []
    for c in conversations:
        key = tuple(c.tool_sequence())  # ETK: aggressive; all tool-less conversations are one
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def extract_conversations(
    sessions: Sequence[Session], sampling: TraceSampling, rng: random.Random
) -> list[ExtractedConversation]:
    """Sample, cluster and cap conversations (failures first, then abandonments, successes)."""
    rates = {
        FAILURE: sampling.error_sample_rate,
        ABANDONMENT: sampling.abandonment_sample_rate,
        SUCCESS: sampling.success_sample_rate,
    }
    retained: dict[str, list[ExtractedConversation]] = {o: [] for o in OUTCOMES}
    for session in sessions:
        conversation = session_to_conversation(session)
        if conversation is not None and rng.random() < rates[conversation.outcome]:
            retained[conversation.outcome].append(conversation)
    clustered = {
        o: cluster_and_select_diverse(retained[o], int(sampling.max_per_cluster.get(o, 3)))
        for o in OUTCOMES
    }
    sampled = [*clustered[FAILURE], *clustered[ABANDONMENT]]
    remaining = sampling.max_conversations - len(sampled)
    if remaining > 0:
        sampled.extend(clustered[SUCCESS][:remaining])
    return _dedupe_by_tool_sequence(sampled)[: sampling.max_conversations]


def select_diverse_failure_patterns(
    failures: Sequence[ExtractedConversation], max_patterns: int = MAX_FAILURE_PATTERNS_FOR_PROMPT
) -> list[ExtractedConversation]:
    """Round-robin across tool combinations so the planner sees the breadth of failures."""
    if len(failures) <= max_patterns:
        return list(failures)
    groups: dict[tuple[str, ...], list[ExtractedConversation]] = {}
    for f in failures:
        groups.setdefault(tuple(sorted(f.tool_sequence())), []).append(f)
    iterators = [iter(g) for g in groups.values()]
    selected: list[ExtractedConversation] = []
    while len(selected) < max_patterns and iterators:
        for index in range(len(iterators) - 1, -1, -1):
            candidate = next(iterators[index], None)
            if candidate is None:
                iterators.pop(index)
                continue
            selected.append(candidate)
            if len(selected) >= max_patterns:
                break
    return selected


def find_matching_trace_examples(
    conversations: Sequence[ExtractedConversation],
    tools_involved: Sequence[str],
    outcome: str,
    max_examples: int = 3,
) -> list[ExtractedConversation]:
    """Real conversations resembling a scenario: refusals match failures, else successes;
    score = 10 x overlap - 2 x unplanned tools."""
    wanted = FAILURE if outcome in ("refusal", "failure") else SUCCESS
    candidates = [c for c in conversations if c.outcome == wanted]
    if not tools_involved:
        tool_less = [c for c in candidates if not c.tool_sequence()]
        return sorted(tool_less, key=lambda c: -len(c.turns))[:max_examples]
    targets = set(tools_involved)
    scored = []
    for c in candidates:
        called = set(c.tool_sequence())
        overlap = len(targets & called)
        if overlap:
            scored.append((overlap * 10 - len(called - targets) * 2, c))
    return [c for _, c in sorted(scored, key=lambda pair: -pair[0])][:max_examples]


def conversation_to_seed_example(conversation: ExtractedConversation) -> SeedExample:
    turns = conversation.turns
    per_turn = tuple(t.tool_calls for t in turns)
    has_calls = any(per_turn)
    response = (turns[-1].assistant_message if turns else None) or ""
    if len(turns) == 1:
        return SeedExample(
            input=turns[0].user_message,
            response=response,
            tool_calls=per_turn[0] if has_calls else None,
        )
    return SeedExample(
        input=tuple(t.user_message for t in turns),
        response=response,
        tool_calls=per_turn if has_calls else None,
    )


def conversations_to_seed_examples(
    conversations: Sequence[ExtractedConversation], max_per_outcome: int = 5
) -> list[SeedExample]:
    """Fallback few-shot examples, balanced across successes and failures (ETK)."""
    selected = []
    for outcome in (SUCCESS, FAILURE):
        selected.extend([c for c in conversations if c.outcome == outcome][:max_per_outcome])
    return [conversation_to_seed_example(c) for c in selected]


def compute_trace_coverage(conversations: Sequence[ExtractedConversation]) -> TraceCoverage:
    tools: set[str] = set()
    combos: list[tuple[str, ...]] = []
    counts = dict.fromkeys(OUTCOMES, 0)
    for c in conversations:
        counts[c.outcome] += 1
        sequence = c.tool_sequence()
        tools.update(sequence)
        combo = tuple(sorted(sequence))
        if combo and combo not in combos:
            combos.append(combo)
    return TraceCoverage(
        observed_tools=tuple(sorted(tools)),
        observed_combinations=tuple(combos),
        success=counts[SUCCESS],
        failure=counts[FAILURE],
        abandonment=counts[ABANDONMENT],
    )
