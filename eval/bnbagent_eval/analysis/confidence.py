"""LLM-free, deterministic confidence scoring of agent trajectories.

Port of AgentSight ``agent_lens.scoring`` (``scorer.py``, ``extractor.py``, ``deriver.py``,
``aggregator.py``, ``scoring_config.py``, ``scoring_utils.py``, ``types/confidence.py``) and its
YAML configs ``configs/scoring/scoring_config.yaml`` (v1.2.0) and
``domain_scoring_profiles.yaml`` (v1.1.0), adapted to our ``Session -> Trace -> Step`` model:

- AgentInvocationSpan -> :class:`AgentStep` (``user_prompt`` / ``response`` / ``available_tools``);
- InferenceSpan -> :class:`LlmStep`; ToolExecutionSpan -> :class:`ToolStep`;
- AgentDelegationSpan / AgentHandoffSpan -> :class:`DelegationStep` / :class:`HandoffStep`;
- span metrics -> ``Step.usage``; ``tool.status == "error"`` span metadata -> ``Step.error``;
- :class:`TxStep` has no source equivalent: it counts as a span (span count, depth) only.

A session is scored as one trace (all its traces' steps, in order). Pipeline:
signals (:class:`SignalExtractor`) -> 6 derived risk scores (:class:`ScoreDeriver`) ->
``confidence = clamp(1 - sum(weight * score ** risk_exponent))`` plus 6 risk flags
(:class:`ScoreAggregator`) -> risk level (low >= 0.8, medium >= 0.6, high >= 0.4, critical).
Defaults, regex patterns and profiles are copied verbatim from the source.
"""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from ..types.trajectory import (
    AgentStep,
    LlmStep,
    Session,
    Step,
    ToolStep,
)

__all__ = [
    "DEFAULT_CONFIG",
    "PROFILES",
    "RISK_FLAGS",
    "SCORE_NAMES",
    "ConfidenceResult",
    "ConfidenceScorer",
    "ConfidenceSummary",
    "DerivedScores",
    "ScoreAggregator",
    "ScoreDeriver",
    "ScoringConfig",
    "SignalExtractor",
    "TraceSignals",
    "load_scoring_config",
]

RiskLevel = Literal["low", "medium", "high", "critical"]
RISK_LEVELS: tuple[RiskLevel, ...] = ("low", "medium", "high", "critical")
SCORE_NAMES = ("errors", "loops", "depth", "relevance", "hedging", "deflection")
RISK_FLAGS = (
    "infinite_loop",
    "excessive_depth",
    "high_error_rate",
    "hidden_tool_failures",
    "task_refusal",
    "task_deflection",
)

# ---------------------------------------------------------------------------------------------
# Configuration data (agentsight configs/scoring/scoring_config.yaml v1.2.0, verbatim values)
# ---------------------------------------------------------------------------------------------

_PATTERNS: dict[str, list[str]] = {
    # Semantic detection (case-insensitive), from the YAML ``internal.patterns`` block.
    "hedging": [
        r"\bi think\b",
        r"\bmaybe\b",
        r"\bperhaps\b",
        r"\bnot sure\b",
        r"\bmight be\b",
        r"\bcould be\b",
        r"\bpossibly\b",
        r"\bi believe\b",
        r"\bprobably\b",
        r"\bit seems\b",
        r"\bappears to\b",
        r"\blikely\b",
        r"\bunlikely\b",
    ],
    "refusal": [
        r"\bi cannot\b",
        r"\bi'm unable\b",
        r"\bi can't\b",
        r"\bi can not\b",
        r"\bnot able to\b",
        r"\boutside my capabilities\b",
        r"\bi don't have access\b",
        r"\bcannot assist\b",
        r"\bunable to help\b",
        r"\bcould not ful\w+\b",
    ],
    "error": [
        r"\berror(?!-free)\b",
        r"\bexception\b",
        r"\bfailed\b",
        r"\bfailure\b",
        r"\btimeout\b",
        r"\btimed out\b",
        r"\bunavailable\b",
        r"\bconnection refused\b",
        r"\bservice unavailable\b",
        r"\binternal server error\b",
        r"\bmax retries\b",
    ],
    "failure_acknowledgment": [
        r"\bi wasn't able to\b",
        r"\bi couldn't\b",
        r"\bi could not\b",
        r"\bfailed to\b",
        r"\bunable to\b",
        r"\bhaving trouble\b",
        r"\bencountered an? (?:error|issue|problem)\b",
        r"\bsomething went wrong\b",
    ],
    "deflection": [
        r"\bhowever,? i can\b",
        r"\binstead,? (?:i|let me)\b",
        r"\blet me (?:tell|share|help) you (?:about|with) something else\b",
        r"\bin the meantime\b",
        r"\bwhile (?:i|we) (?:work on|figure out)\b",
    ],
    # Defaults from ``scoring_config.PatternsConfig`` (not overridden by the YAML).
    "intent_how_to": [r"\bhow (?:do|can|to|should|would)\b"],
    "intent_action": [
        r"^(?:fix|update|create|delete|remove|add|change|modify|set|write|deploy|install|run"
        r"|execute|build|move|rename|refactor|implement|migrate)\b"
    ],
    "intent_list": [r"^(?:list|show|find|get|fetch|display|what are|which)\b"],
    "intent_explain": [r"^(?:explain|describe|what is|what does|why|tell me about|what\'s)\b"],
    "structural_steps": [r"^\s*\d+[\.\)]\s"],
    "structural_imperatives": [
        r"^(?:Run|Execute|Install|Add|Create|Open|Use|Set|Configure|Enable|Start|Stop|First"
        r"|Then|Next|Finally)\b"
    ],
    "structural_bullets": [r"^\s*[-*]\s"],
    "structural_confirmation": [
        r"\b(?:done|completed|updated|created|fixed|applied|removed|deleted|added|changed"
        r"|deployed|installed|built|moved|renamed)\b"
    ],
    "file_path_pattern": [
        r'(?:^|[\s\'"`(])(/[\w./+-]+\.[\w]+|[\w./+-]+\.(?:py|js|ts|java|go|rs|rb|sh|yaml|yml'
        r"|json|toml|xml|html|css|sql|md|txt|cfg|conf|ini|env))\b"
    ],
    "identifier_pattern": [
        r"\b(?:[A-Z]+-\d+|#\d+|v\d+\.\d+[\.\d]*|https?://\S+|\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b"
    ],
}

# Compile flags per group, as in ``scoring_config._build_compiled_patterns``.
_MULTILINE_GROUPS = {"structural_steps", "structural_imperatives", "structural_bullets"}
_CASE_SENSITIVE_GROUPS = {"file_path_pattern", "identifier_pattern"}

DEFAULT_CONFIG: dict[str, Any] = {
    "version": "1.2.0",
    "weights": {
        "errors": 0.25,
        "loops": 0.20,
        "depth": 0.10,
        "relevance": 0.20,
        "hedging": 0.10,
        "deflection": 0.15,
    },
    "risk_exponent": 1.0,
    "limits": {
        "max_repeated_calls": 5,
        "max_depth": 10,
        "max_error_rate": 0.5,
        "inferred_error_rate": 0.75,
        "deflection": 0.5,
        "relevance_for_deflection": 0.85,
    },
    "risk_levels": {"low": 0.8, "medium": 0.6, "high": 0.4},
    "internal": {
        "expected_depth": 3,
        "hedging_density_max": 10.0,
        "tool_timeout_ms": 30000,
        "relevance_weights": {
            "query_recall": 0.35,
            "substantiveness": 0.20,
            "structural_match": 0.20,
            "topic_coherence": 0.25,
        },
        "low_signal_max_keywords": 1,
        "low_signal_weights": {"substantiveness": 0.45, "structural_match": 0.55},
        "deflection_query_recall_threshold": 0.15,
        "deflection_recall_threshold": 0.3,
        "deflection_pattern_score": 0.6,
        "refusal_score": 0.8,
        "bare_failure_substantiveness_threshold": 0.2,
        "hedging_weight": 0.7,
        "apology_weight": 0.3,
        "error_weights": {"explicit": 0.5, "empty": 0.3, "inferred": 0.2},
        "latency_hotspot_threshold": 3.0,
        "error_hotspot_threshold": 0.3,
        "loop_detection_threshold": 3,
        "hedging_density_warning": 2.0,
        "hedging_density_high": 5.0,
        "relevance_low_threshold": 0.2,
        "relevance_critical_threshold": 0.1,
        "apology_density_warning": 1.0,
        "substantiveness_artifact_thresholds": [1, 3],
        "substantiveness_artifact_base_score": 0.6,
        "substantiveness_artifact_step": 0.2,
        "substantiveness_density_baseline": 0.15,
        "substantiveness_weights": {"artifacts": 0.6, "density": 0.4},
        "explain_min_words": 20,
        "question_min_words": 5,
        "list_min_newlines": 2,
    },
}

# agentsight configs/scoring/domain_scoring_profiles.yaml v1.1.0 (no profile sets risk_exponent).
PROFILES: dict[str, dict[str, Any]] = {
    "general": {
        "description": "Balanced profile for general-purpose agents",
        "tolerance": "moderate",
        "weights": {
            "errors": 0.25,
            "loops": 0.20,
            "depth": 0.10,
            "relevance": 0.20,
            "hedging": 0.10,
            "deflection": 0.15,
        },
    },
    "coding": {
        "description": "Optimized for code generation. Prioritizes error detection.",
        "tolerance": "moderate",
        "weights": {
            "errors": 0.30,
            "loops": 0.20,
            "depth": 0.10,
            "relevance": 0.10,
            "hedging": 0.10,
            "deflection": 0.20,
        },
    },
    "support": {
        "description": "Optimized for customer support. Prioritizes relevance.",
        "tolerance": "relaxed",
        "weights": {
            "errors": 0.15,
            "loops": 0.10,
            "depth": 0.10,
            "relevance": 0.30,
            "hedging": 0.10,
            "deflection": 0.25,
        },
    },
    "analysis": {
        "description": "Optimized for data analysis. Strict on hedging.",
        "tolerance": "strict",
        "weights": {
            "errors": 0.25,
            "loops": 0.15,
            "depth": 0.10,
            "relevance": 0.20,
            "hedging": 0.20,
            "deflection": 0.10,
        },
    },
}

# agentsight scoring_utils.DEFAULT_STOP_WORDS (verbatim).
STOP_WORDS = frozenset(
    """the a an is are was were be been being have has had do does did will would could should
    may might must shall can need dare ought used to of in for on with at by from as into through
    during before after above below between under again further then once here there when where
    why how all each few more most other some such no nor not only own same so than too very just
    and but if or because until while although though this that these those i me my myself we
    our ours ourselves you your yours yourself yourselves he him his himself she her hers herself
    it its itself they them their theirs themselves what which who whom whose please help want
    like""".split()
)


def _compile(group: str, patterns: Iterable[str]) -> tuple[re.Pattern[str], ...]:
    if group in _MULTILINE_GROUPS:
        flags = re.MULTILINE
    elif group in _CASE_SENSITIVE_GROUPS:
        flags = 0
    else:
        flags = re.IGNORECASE
    return tuple(re.compile(p, flags) for p in patterns)


PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    g: _compile(g, ps) for g, ps in _PATTERNS.items()
}


@dataclass(frozen=True)
class ScoringConfig:
    """Effective scoring configuration (weights, exponent, limits, risk levels, internals)."""

    version: str
    weights: Mapping[str, float]
    risk_exponent: float
    limits: Mapping[str, float]
    risk_levels: Mapping[str, float]
    internal: Mapping[str, Any]
    profile: str | None = None


def _merge(base: Mapping[str, Any], overrides: Mapping[str, Any] | None, what: str) -> dict:
    out = dict(base)
    for key, value in (overrides or {}).items():
        if key not in out:
            raise ValueError(f"unknown {what} {key!r} (known: {', '.join(sorted(out))})")
        out[key] = value
    return out


def load_scoring_config(
    profile: str | None = "general",
    weight_overrides: Mapping[str, float] | None = None,
    risk_exponent: float | None = None,
    limits: Mapping[str, float] | None = None,
    internal: Mapping[str, Any] | None = None,
    normalize_weights: bool = True,
) -> ScoringConfig:
    """Build a config like ``scoring_config.load_scoring_config``.

    The profile replaces the base weights, ``weight_overrides`` update them (partial overrides
    allowed), and weights are renormalized to sum to 1 when they are off by more than 0.001.
    """
    raw = DEFAULT_CONFIG
    weights = dict(raw["weights"])
    exponent = float(raw["risk_exponent"])
    if profile:
        if profile not in PROFILES:
            raise ValueError(f"profile {profile!r} not found (available: {', '.join(PROFILES)})")
        weights = dict(PROFILES[profile]["weights"])
        if risk_exponent is None and PROFILES[profile].get("risk_exponent") is not None:
            exponent = float(PROFILES[profile]["risk_exponent"])
    if risk_exponent is not None:
        if not risk_exponent > 0:
            raise ValueError("risk_exponent must be > 0")
        exponent = float(risk_exponent)
    weights = _merge(weights, weight_overrides, "weight")
    if any(v < 0 for v in weights.values()):
        raise ValueError("weights must be >= 0")
    if normalize_weights:
        total = sum(weights.values())
        if total == 0:
            raise ValueError("Weights cannot all be zero")
        if abs(total - 1.0) > 0.001:
            weights = {k: v / total for k, v in weights.items()}
    return ScoringConfig(
        version=raw["version"],
        weights=weights,
        risk_exponent=exponent,
        limits=_merge(raw["limits"], limits, "limit"),
        risk_levels=dict(raw["risk_levels"]),
        internal=_merge(raw["internal"], internal, "internal setting"),
        profile=profile,
    )


# ---------------------------------------------------------------------------------------------
# Small helpers (scoring_utils)
# ---------------------------------------------------------------------------------------------


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    return numerator / denominator if denominator != 0 else default


def compute_cv(values: Sequence[float], max_cv: float = 2.0) -> float:
    """Coefficient of variation (stdev / mean), capped at ``max_cv``."""
    if len(values) < 2:
        return 0.0
    mean_val = statistics.mean(values)
    if mean_val == 0:
        return 0.0
    return min(max_cv, statistics.stdev(values) / mean_val)


def extract_keywords(text: str, min_length: int = 3, max_keywords: int = 50) -> list[str]:
    if not text:
        return []
    words = re.findall(r"\b[a-z]+\b", text.lower())
    keywords = [w for w in words if w not in STOP_WORDS and len(w) >= min_length]
    return [word for word, _ in Counter(keywords).most_common(max_keywords)]


def step_duration(step: Step) -> float | None:
    """Non-negative duration in ms, or None when a timestamp is missing."""
    d = step.duration_ms
    return None if d is None else max(0.0, float(d))


def tool_content(step: ToolStep) -> str:
    """Tool result content as text (non-string content is JSON-encoded)."""
    content = step.result.content if step.result else None
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return json.dumps(content, sort_keys=True, default=str)


def tool_error(step: ToolStep) -> str | None:
    """Explicit result error, else the step error (the source's ``tool.status`` metadata)."""
    if step.result and step.result.error:
        return step.result.error
    return step.error or None


def by_start(steps: Iterable[Step]) -> list[Step]:
    """Stable sort by start time; steps without one keep their order, after timed steps."""
    return sorted(steps, key=lambda s: (s.start_ms is None, s.start_ms or 0.0))


def _params_signature(arguments: Mapping[str, Any]) -> str:
    try:
        return json.dumps(dict(arguments), sort_keys=True)
    except (TypeError, ValueError):
        return str(arguments)


def _param_values(params: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for value in params.values():
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, Mapping):
            values.extend(_param_values(value))
        elif isinstance(value, list | tuple):
            for item in value:
                if isinstance(item, str):
                    values.append(item)
                elif isinstance(item, Mapping):
                    values.extend(_param_values(item))
    return values


def matches(group: str, text: str) -> bool:
    return any(p.search(text) for p in PATTERNS[group])


def _count(group: str, text: str) -> int:
    return sum(len(p.findall(text)) for p in PATTERNS[group])


# ---------------------------------------------------------------------------------------------
# Signals (types/confidence.py)
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class StructuralSignals:
    span_count: int
    inference_span_count: int
    tool_span_count: int
    agent_span_count: int
    max_depth: int
    unique_tools_used: int
    total_tool_calls: int
    repeated_tool_calls: int
    tool_call_sequence: list[str]


@dataclass(frozen=True)
class TemporalSignals:
    total_latency_ms: float
    mean_span_latency_ms: float
    max_span_latency_ms: float
    min_span_latency_ms: float
    latency_std_ms: float
    time_to_first_tool_ms: float | None
    time_to_first_response_ms: float | None
    inference_latency_total_ms: float
    tool_execution_latency_total_ms: float
    inference_latency_cv: float = 0.0
    tool_latency_cv: float = 0.0


@dataclass(frozen=True)
class TokenSignals:
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_cache_read_tokens: int
    total_cache_write_tokens: int
    tokens_per_turn: list[int]


@dataclass(frozen=True)
class SemanticSignals:
    user_prompt_char_count: int
    user_prompt_word_count: int
    response_char_count: int
    response_word_count: int
    hedging_phrase_count: int
    refusal_phrase_count: int
    question_count: int
    code_block_count: int
    apology_phrase_count: int = 0  # never populated by the source extractor (no patterns)
    filler_phrase_count: int = 0


@dataclass(frozen=True)
class ToolSignals:
    tool_error_count: int
    tool_success_count: int
    tool_timeout_count: int
    unique_error_types: list[str]
    tool_result_char_lengths: list[int]
    empty_result_count: int
    inferred_error_count: int = 0


@dataclass(frozen=True)
class RelevanceSignals:
    query_keywords: list[str]
    query_recall: float
    code_block_count: int = 0
    file_path_count: int = 0
    specific_identifier_count: int = 0
    response_word_count: int = 0
    information_density: float = 0.0
    substantiveness: float = 0.0
    query_intent: str = "unknown"
    format_match: bool = False
    structural_match: float = 0.0
    on_topic_fragments: int = 0
    total_fragments: int = 0
    topic_coherence: float = 0.0
    failure_acknowledged: bool = False
    deflection_detected: bool = False
    low_signal: bool = False
    composite_relevance: float = 0.0


@dataclass(frozen=True)
class TraceSignals:
    trace_id: str
    structural: StructuralSignals
    temporal: TemporalSignals
    semantic: SemanticSignals
    tool: ToolSignals
    tokens: TokenSignals | None = None
    relevance: RelevanceSignals | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _Texts:
    """Agent prompt / response text (AgentStep, or the case input / output as a fallback)."""

    has_agent: bool
    prompts: list[str]
    responses: list[str]
    root_response: str | None

    @property
    def prompt(self) -> str:
        return " ".join(self.prompts)

    @property
    def response(self) -> str:
        return " ".join(self.responses)


def _texts(session: Session, user_prompt: str | None, response: str | None) -> _Texts:
    agents = [s for s in session.steps() if isinstance(s, AgentStep)]
    prompts = [s.user_prompt for s in agents if s.user_prompt]
    responses = [s.response for s in agents if s.response]
    if not prompts and not responses and (user_prompt or response):
        prompts = [user_prompt] if user_prompt else []
        responses = [response] if response else []
        return _Texts(True, prompts, responses, response or None)
    roots = [s for s in agents if not s.parent_id]
    root_response = None
    if roots:
        root = max(roots, key=lambda s: step_duration(s) or 0.0)
        root_response = root.response or None
    return _Texts(bool(agents), prompts, responses, root_response)


class SignalExtractor:
    """Extracts raw signals from a session (agentsight ``SignalExtractor``)."""

    def __init__(self, config: ScoringConfig | None = None):
        self.config = config or load_scoring_config()
        self._internal = self.config.internal

    def extract(
        self, session: Session, user_prompt: str | None = None, response: str | None = None
    ) -> TraceSignals:
        steps = list(session.steps())
        texts = _texts(session, user_prompt, response)
        return TraceSignals(
            trace_id=session.id,
            structural=self._structural(steps),
            temporal=self._temporal(steps),
            semantic=self._semantic(texts),
            tool=self._tool(steps),
            tokens=self._tokens(steps),
            relevance=self._relevance(steps, texts),
        )

    def _structural(self, steps: list[Step]) -> StructuralSignals:
        tools = [s for s in steps if isinstance(s, ToolStep)]
        counts = Counter((s.call.name, _params_signature(s.call.arguments)) for s in tools)
        return StructuralSignals(
            span_count=len(steps),
            inference_span_count=sum(1 for s in steps if isinstance(s, LlmStep)),
            tool_span_count=len(tools),
            agent_span_count=sum(1 for s in steps if isinstance(s, AgentStep)),
            max_depth=max_depth(steps),
            unique_tools_used=len({s.call.name for s in tools}),
            total_tool_calls=len(tools),
            repeated_tool_calls=sum(c - 1 for c in counts.values() if c > 1),
            tool_call_sequence=[s.call.name for s in by_start(tools)],
        )

    def _temporal(self, steps: list[Step]) -> TemporalSignals:
        timed = [s for s in steps if s.start_ms is not None and s.end_ms is not None]
        if not timed:
            return TemporalSignals(0.0, 0.0, 0.0, 0.0, 0.0, None, None, 0.0, 0.0, 0.0, 0.0)
        durations = [step_duration(s) or 0.0 for s in timed]
        start = min(float(s.start_ms or 0.0) for s in timed)
        end = max(float(s.end_ms or 0.0) for s in timed)
        tools = [s for s in timed if isinstance(s, ToolStep)]
        agents = [s for s in timed if isinstance(s, AgentStep)]
        ttft = min(float(s.start_ms or 0.0) for s in tools) - start if tools else None
        ttfr = min(float(s.end_ms or 0.0) for s in agents) - start if agents else None
        llm_d = [float(s.duration_ms or 0.0) for s in timed if isinstance(s, LlmStep)]
        tool_d = [float(s.duration_ms or 0.0) for s in tools]
        return TemporalSignals(
            total_latency_ms=max(0.0, end - start),
            mean_span_latency_ms=statistics.mean(durations),
            max_span_latency_ms=max(durations),
            min_span_latency_ms=min(durations),
            latency_std_ms=statistics.stdev(durations) if len(durations) > 1 else 0.0,
            time_to_first_tool_ms=ttft,
            time_to_first_response_ms=ttfr,
            inference_latency_total_ms=sum(llm_d),
            tool_execution_latency_total_ms=sum(tool_d),
            inference_latency_cv=compute_cv(llm_d),
            tool_latency_cv=compute_cv(tool_d),
        )

    def _semantic(self, texts: _Texts) -> SemanticSignals:
        prompt, response = texts.prompt, texts.response
        return SemanticSignals(
            user_prompt_char_count=len(prompt),
            user_prompt_word_count=len(prompt.split()) if prompt else 0,
            response_char_count=len(response),
            response_word_count=len(response.split()) if response else 0,
            hedging_phrase_count=_count("hedging", response),
            refusal_phrase_count=_count("refusal", response),
            question_count=response.count("?"),
            code_block_count=response.count("```") // 2,
        )

    def _tool(self, steps: list[Step]) -> ToolSignals:
        errors = success = timeouts = inferred = empty = 0
        error_types: set[str] = set()
        lengths: list[int] = []
        timeout_ms = self._internal["tool_timeout_ms"]
        for step in steps:
            if not isinstance(step, ToolStep):
                continue
            content = tool_content(step)
            err = tool_error(step)
            if err:
                errors += 1
                error_types.add(err)
            else:
                success += 1
                if content and matches("error", content):
                    inferred += 1
            duration = step_duration(step)
            if duration is not None and duration > timeout_ms:
                timeouts += 1
            lengths.append(len(content))
            if not content.strip():
                empty += 1
        return ToolSignals(
            tool_error_count=errors,
            tool_success_count=success,
            tool_timeout_count=timeouts,
            unique_error_types=sorted(error_types),
            tool_result_char_lengths=lengths,
            empty_result_count=empty,
            inferred_error_count=inferred,
        )

    def _tokens(self, steps: list[Step]) -> TokenSignals | None:
        usages = [s.usage for s in steps if s.usage is not None]
        if not usages:
            return None
        per_turn = [(u.input_tokens or 0) + (u.output_tokens or 0) for u in usages]
        return TokenSignals(
            total_input_tokens=sum(u.input_tokens or 0 for u in usages),
            total_output_tokens=sum(u.output_tokens or 0 for u in usages),
            total_tokens=sum(per_turn),
            total_cache_read_tokens=sum(u.cache_read_tokens or 0 for u in usages),
            total_cache_write_tokens=sum(u.cache_write_tokens or 0 for u in usages),
            tokens_per_turn=[t for t in per_turn if t],
        )

    # -- relevance ------------------------------------------------------------------------------

    def _relevance(self, steps: list[Step], texts: _Texts) -> RelevanceSignals | None:
        if not texts.has_agent or not texts.prompt:
            return None
        keywords = extract_keywords(texts.prompt)
        if not keywords:
            return None
        if len(keywords) <= self._internal["low_signal_max_keywords"]:
            return self._relevance_low_signal(keywords, texts)
        return self._relevance_full(steps, keywords, texts)

    def _relevance_low_signal(self, keywords: list[str], texts: _Texts) -> RelevanceSignals:
        prompt, response = texts.prompt, texts.response
        subst = self.substantiveness(response)
        intent = detect_query_intent(prompt)
        match = self.structural_match(intent, response)
        sm = 1.0 if match else 0.0
        w = self._internal["low_signal_weights"]
        composite = subst["score"] * w.get("substantiveness", 0.45) + sm * w.get(
            "structural_match", 0.55
        )
        failure = matches("failure_acknowledgment", response)
        deflected = failure and matches("deflection", response)
        if not deflected and failure and texts.root_response:
            threshold = self._internal["bare_failure_substantiveness_threshold"]
            if self.substantiveness(texts.root_response)["score"] <= threshold:
                deflected = True
        return RelevanceSignals(
            query_keywords=keywords,
            query_recall=0.0,
            code_block_count=subst["code_blocks"],
            file_path_count=subst["file_paths"],
            specific_identifier_count=subst["identifiers"],
            response_word_count=subst["word_count"],
            information_density=subst["density"],
            substantiveness=subst["score"],
            query_intent=intent,
            format_match=match,
            structural_match=sm,
            failure_acknowledged=failure,
            deflection_detected=deflected,
            low_signal=True,
            composite_relevance=composite,
        )

    def _relevance_full(
        self, steps: list[Step], keywords: list[str], texts: _Texts
    ) -> RelevanceSignals:
        prompt, response = texts.prompt, texts.response
        fragments = activity_fragments(steps, texts)
        activity = " ".join(fragments).lower()
        recall = sum(1 for kw in keywords if kw in activity) / len(keywords)
        subst = self.substantiveness(response)
        intent = detect_query_intent(prompt)
        match = self.structural_match(intent, response)
        on_topic = sum(1 for f in fragments if any(kw in f.lower() for kw in keywords))
        total = len(fragments)
        coherence = on_topic / total if total > 0 else 0.0
        failure = matches("failure_acknowledgment", response)
        deflected = (failure and matches("deflection", response)) or (
            failure and recall < self._internal["deflection_query_recall_threshold"]
        )
        if texts.root_response and not deflected and failure:
            threshold = self._internal["bare_failure_substantiveness_threshold"]
            if self.substantiveness(texts.root_response)["score"] <= threshold:
                deflected = True
        sm = 1.0 if match else 0.0
        rw = self._internal["relevance_weights"]
        composite = (
            recall * rw.get("query_recall", 0.35)
            + subst["score"] * rw.get("substantiveness", 0.20)
            + sm * rw.get("structural_match", 0.20)
            + coherence * rw.get("topic_coherence", 0.25)
        )
        return RelevanceSignals(
            query_keywords=keywords,
            query_recall=recall,
            code_block_count=subst["code_blocks"],
            file_path_count=subst["file_paths"],
            specific_identifier_count=subst["identifiers"],
            response_word_count=subst["word_count"],
            information_density=subst["density"],
            substantiveness=subst["score"],
            query_intent=intent,
            format_match=match,
            structural_match=sm,
            on_topic_fragments=on_topic,
            total_fragments=total,
            topic_coherence=coherence,
            failure_acknowledged=failure,
            deflection_detected=deflected,
            composite_relevance=composite,
        )

    def substantiveness(self, response: str) -> dict[str, Any]:
        """Concrete artifacts (code blocks, paths, identifiers) and information density."""
        if not response:
            return {
                "code_blocks": 0,
                "file_paths": 0,
                "identifiers": 0,
                "word_count": 0,
                "density": 0.0,
                "score": 0.0,
            }
        code_blocks = response.count("```") // 2
        file_paths = _count("file_path_pattern", response)
        identifiers = _count("identifier_pattern", response)
        word_count = len(response.split())
        density = 0.0
        if word_count > 0:
            content = [
                w
                for w in re.findall(r"\b[a-z]+\b", response.lower())
                if w not in STOP_WORDS and len(w) >= 3
            ]
            density = len(set(content)) / word_count
        internal = self._internal
        thresholds = internal["substantiveness_artifact_thresholds"]
        min_partial = thresholds[0] if len(thresholds) > 0 else 1
        min_full = thresholds[1] if len(thresholds) > 1 else 3
        artifacts = code_blocks + file_paths + identifiers
        if artifacts >= min_full:
            artifact_score = 1.0
        elif artifacts >= min_partial:
            artifact_score = (
                internal["substantiveness_artifact_base_score"]
                + (artifacts - min_partial) * internal["substantiveness_artifact_step"]
            )
        else:
            artifact_score = 0.0
        baseline = internal["substantiveness_density_baseline"]
        density_score = min(1.0, density / baseline) if density > 0 else 0.0
        sw = internal["substantiveness_weights"]
        score = artifact_score * sw.get("artifacts", 0.6) + density_score * sw.get("density", 0.4)
        return {
            "code_blocks": code_blocks,
            "file_paths": file_paths,
            "identifiers": identifiers,
            "word_count": word_count,
            "density": density,
            "score": min(1.0, score),
        }

    def structural_match(self, intent: str, response: str) -> bool:
        """Whether the response format matches the query intent."""
        if intent == "unknown" or not response:
            return True
        internal = self._internal
        if intent == "how_to":
            return (
                matches("structural_steps", response)
                or "```" in response
                or matches("structural_imperatives", response)
                or matches("structural_bullets", response)
            )
        if intent == "action":
            return matches("structural_confirmation", response) or "```" in response
        if intent == "list":
            return (
                matches("structural_bullets", response)
                or matches("structural_steps", response)
                or response.count("\n") >= internal["list_min_newlines"]
            )
        if intent == "explain":
            return len(response.split()) >= internal["explain_min_words"]
        if intent == "question":
            return len(response.split()) >= internal["question_min_words"]
        return True


def detect_query_intent(query: str) -> str:
    """One of ``how_to``, ``action``, ``list``, ``explain``, ``question``, ``unknown``."""
    q = query.strip().lower()
    for intent in ("how_to", "action", "list", "explain"):
        if matches(f"intent_{intent}", q):
            return intent
    return "question" if q.rstrip().endswith("?") else "unknown"


def activity_fragments(steps: Iterable[Step], texts: _Texts | None = None) -> list[str]:
    """Tool names, flattened tool params, tool results and agent responses, in step order."""
    fragments: list[str] = []
    has_agent_text = False
    for step in steps:
        if isinstance(step, ToolStep):
            fragments.append(step.call.name)
            params = " ".join(_param_values(step.call.arguments))
            if params:
                fragments.append(params)
            content = tool_content(step)
            if content.strip():
                fragments.append(content)
        elif isinstance(step, AgentStep) and step.response:
            fragments.append(step.response)
            has_agent_text = True
    if texts is not None and not has_agent_text:
        fragments.extend(texts.responses)  # case-output fallback acts as the agent response
    return fragments


def max_depth(steps: Sequence[Step]) -> int:
    """Depth of the step tree (roots have depth 1), as ``SignalExtractor._compute_max_depth``.

    Steps whose parent is missing from the session are unreachable and ignored (source quirk).
    """
    if not steps:
        return 0
    children: dict[str, list[str]] = {}
    roots: list[str] = []
    for step in steps:
        if step.parent_id is None:
            roots.append(step.id)
        else:
            children.setdefault(step.parent_id, []).append(step.id)
    if not roots:
        return 1
    best = 0
    stack = [(r, 1, frozenset({r})) for r in roots]
    while stack:
        node, depth, seen = stack.pop()
        best = max(best, depth)
        stack.extend((c, depth + 1, seen | {c}) for c in children.get(node, ()) if c not in seen)
    return best


# ---------------------------------------------------------------------------------------------
# Derived scores (deriver.py)
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DerivedScores:
    """The 6 weighted risk scores (0 = good, 1 = bad) plus their raw components."""

    errors: float = 0.0
    loops: float = 0.0
    depth: float = 0.0
    relevance: float = 0.0
    hedging: float = 0.0
    deflection: float = 0.0
    error_rate: float = 0.0
    empty_result_rate: float = 0.0
    inferred_error_rate: float = 0.0
    hedging_density: float = 0.0
    apology_density: float = 0.0
    refusal_detected: bool = False

    def core(self) -> dict[str, float]:
        return {name: getattr(self, name) for name in SCORE_NAMES}


class ScoreDeriver:
    """Computes the 6 derived scores from raw signals (agentsight ``ScoreDeriver``)."""

    def __init__(self, config: ScoringConfig | None = None):
        self.config = config or load_scoring_config()
        self._internal = self.config.internal

    def derive(self, signals: TraceSignals) -> DerivedScores:
        total = signals.structural.total_tool_calls
        words = signals.semantic.response_word_count
        error_rate = safe_divide(signals.tool.tool_error_count, total)
        empty_rate = safe_divide(signals.tool.empty_result_count, total)
        inferred_rate = safe_divide(signals.tool.inferred_error_count, total)
        hedging_density = safe_divide(signals.semantic.hedging_phrase_count * 100, words)
        apology_density = safe_divide(signals.semantic.apology_phrase_count * 100, words)
        refusal = signals.semantic.refusal_phrase_count > 0
        ew = self._internal["error_weights"]
        errors = min(
            1.0,
            error_rate * ew.get("explicit", 0.5)
            + empty_rate * ew.get("empty", 0.3)
            + inferred_rate * ew.get("inferred", 0.2),
        )
        max_density = self._internal["hedging_density_max"]
        hedging = min(
            1.0,
            min(1.0, hedging_density / max_density) * self._internal["hedging_weight"]
            + min(1.0, apology_density / max_density) * self._internal["apology_weight"],
        )
        return DerivedScores(
            errors=errors,
            loops=safe_divide(signals.structural.repeated_tool_calls, total),
            depth=self._depth(signals.structural.max_depth),
            relevance=(
                0.0 if signals.relevance is None else 1.0 - signals.relevance.composite_relevance
            ),
            hedging=hedging,
            deflection=self._deflection(signals, refusal),
            error_rate=error_rate,
            empty_result_rate=empty_rate,
            inferred_error_rate=inferred_rate,
            hedging_density=hedging_density,
            apology_density=apology_density,
            refusal_detected=refusal,
        )

    def _depth(self, depth: int) -> float:
        expected = self._internal["expected_depth"]
        if depth <= expected or expected == 0:
            return 0.0
        return min(1.0, (depth - expected) / expected)

    def _deflection(self, signals: TraceSignals, refusal: bool) -> float:
        internal = self._internal
        if refusal:
            return internal["refusal_score"]
        rel = signals.relevance
        if rel is None:
            return 0.0
        if rel.low_signal:
            return internal["deflection_pattern_score"] if rel.deflection_detected else 0.0
        if rel.deflection_detected:
            return internal["deflection_pattern_score"]
        if not rel.failure_acknowledged:
            return 0.0
        if rel.substantiveness <= internal["bare_failure_substantiveness_threshold"]:
            return internal["deflection_pattern_score"]
        threshold = internal["deflection_recall_threshold"]
        if rel.query_recall < threshold:
            return min(1.0, (threshold - rel.query_recall) / threshold)
        return 0.0


# ---------------------------------------------------------------------------------------------
# Aggregation (aggregator.py)
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoreContribution:
    score_name: str
    raw_value: float
    weight: float
    amplified_value: float
    weighted_contribution: float


@dataclass(frozen=True)
class ScoreExplanation:
    contributions: list[ScoreContribution]
    total_weighted_risk: float
    base_confidence: float
    final_confidence: float
    risk_exponent: float
    summary: str


_RISK_DESCRIPTIONS = {
    "infinite_loop": "agent stuck in loop",
    "high_error_rate": "high tool error rate",
    "task_refusal": "task refused",
    "excessive_depth": "excessive trace depth",
    "task_deflection": "task deflection detected",
    "hidden_tool_failures": "hidden tool failures",
}


class ScoreAggregator:
    """``confidence = clamp(1 - sum(weight * score ** exponent))`` plus risk flags."""

    def __init__(self, config: ScoringConfig | None = None, risk_exponent: float | None = None):
        self.config = config or load_scoring_config()
        self.risk_exponent = (
            risk_exponent if risk_exponent is not None else self.config.risk_exponent
        )

    def aggregate(
        self, signals: TraceSignals, derived: DerivedScores
    ) -> tuple[float, list[str], ScoreExplanation]:
        contributions = self.contributions(derived)
        total = sum(c.weighted_contribution for c in contributions)
        confidence = max(0.0, min(1.0, 1.0 - total))
        risks = self.risks(signals, derived)
        explanation = ScoreExplanation(
            contributions=contributions,
            total_weighted_risk=total,
            base_confidence=confidence,
            final_confidence=confidence,
            risk_exponent=self.risk_exponent,
            summary=self._summary(contributions, risks, confidence),
        )
        return confidence, risks, explanation

    def risks(self, signals: TraceSignals, derived: DerivedScores) -> list[str]:
        limits = self.config.limits
        risks = []
        if signals.structural.repeated_tool_calls > limits["max_repeated_calls"]:
            risks.append("infinite_loop")
        if signals.structural.max_depth > limits["max_depth"]:
            risks.append("excessive_depth")
        if derived.errors > limits["max_error_rate"]:
            risks.append("high_error_rate")
        if derived.inferred_error_rate > limits["inferred_error_rate"]:
            risks.append("hidden_tool_failures")
        if derived.refusal_detected:
            risks.append("task_refusal")
        if (
            derived.deflection > limits["deflection"]
            and derived.relevance > limits["relevance_for_deflection"]
        ):
            risks.append("task_deflection")
        return risks

    def contributions(self, derived: DerivedScores) -> list[ScoreContribution]:
        out = []
        for name in SCORE_NAMES:
            weight = self.config.weights.get(name, 0.0)
            raw = getattr(derived, name)
            amplified = raw**self.risk_exponent
            out.append(ScoreContribution(name, raw, weight, amplified, amplified * weight))
        return out

    def _summary(
        self, contributions: list[ScoreContribution], risks: list[str], confidence: float
    ) -> str:
        parts = []
        if risks:
            parts.append(f"RISKS: {', '.join(_RISK_DESCRIPTIONS.get(r, r) for r in risks)}.")
        if not risks and confidence >= 0.8:
            return "Trace shows healthy execution patterns with no significant risk indicators."
        significant = [c for c in contributions if c.weighted_contribution > 0.05]
        if not significant and not risks:
            return f"Confidence score: {confidence:.2f}. No significant risk factors detected."
        parts.append(f"Confidence score: {confidence:.2f}")
        factors = [d for c in significant if (d := _describe(c))]
        if factors:
            parts.append("due to: " + ", ".join(factors))
        return " ".join(parts) + "."


def _describe(c: ScoreContribution) -> str | None:
    w, v = c.weighted_contribution, c.raw_value
    severity = "high" if w >= 0.15 else "elevated" if w >= 0.08 else "moderate"
    return {
        "errors": f"{severity} error rate ({v:.0%})",
        "loops": f"{severity} loop risk ({v:.0%} repeated calls)",
        "depth": f"{severity} depth risk ({v:.0%})",
        "relevance": f"{severity} relevance risk ({v:.0%} composite gap)",
        "hedging": f"{severity} hedging ({v:.0%})",
        "deflection": f"{severity} deflection detected" if v > 0 else None,
    }.get(c.score_name)


# ---------------------------------------------------------------------------------------------
# Results (types/confidence.py) and the scorer (scorer.py)
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfidenceResult:
    """Confidence 0-1 (higher = more confident), risk level, derived scores, flags, insights."""

    confidence: float
    risk_level: RiskLevel
    derived: DerivedScores
    risks: list[str]
    explanation: ScoreExplanation
    signals: TraceSignals
    insights: dict[str, Any] | None = None
    scorer_version: str = "2.0.0"
    thresholds_version: str = "1.2.0"

    @property
    def confidence_score(self) -> float:  # source name
        return self.confidence

    @property
    def flags(self) -> dict[str, bool]:
        """All 6 risk flags (true when raised)."""
        return {f: f in self.risks for f in RISK_FLAGS}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def risk_level(confidence: float, levels: Mapping[str, float] | None = None) -> RiskLevel:
    """Map confidence to low / medium / high / critical (thresholds are inclusive lower bounds)."""
    levels = levels or DEFAULT_CONFIG["risk_levels"]
    if confidence >= levels["low"]:
        return "low"
    if confidence >= levels["medium"]:
        return "medium"
    if confidence >= levels["high"]:
        return "high"
    return "critical"


class ConfidenceScorer:
    """LLM-free, deterministic confidence scorer (agentsight ``ConfidenceScorer``).

    ``weight_overrides`` may be partial; weights are renormalized to sum to 1. ``limits``
    overrides the risk-flag limits (``max_repeated_calls``, ``max_depth``, ``max_error_rate``,
    ``inferred_error_rate``, ``deflection``, ``relevance_for_deflection``); ``internal`` the
    normalization settings. Computation time is not recorded (results are deterministic).
    """

    VERSION = "2.0.0"

    def __init__(
        self,
        profile: str | None = "general",
        weight_overrides: Mapping[str, float] | None = None,
        risk_exponent: float | None = None,
        limits: Mapping[str, float] | None = None,
        internal: Mapping[str, Any] | None = None,
        normalize_weights: bool = True,
        include_insights: bool = True,
        config: ScoringConfig | None = None,
    ):
        self.config = config or load_scoring_config(
            profile=profile,
            weight_overrides=weight_overrides,
            risk_exponent=risk_exponent,
            limits=limits,
            internal=internal,
            normalize_weights=normalize_weights,
        )
        self.include_insights = include_insights
        self.extractor = SignalExtractor(self.config)
        self.deriver = ScoreDeriver(self.config)
        self.aggregator = ScoreAggregator(self.config)

    @property
    def weights(self) -> dict[str, float]:
        return dict(self.config.weights)

    def score(
        self,
        session: Session,
        *,
        user_prompt: str | None = None,
        response: str | None = None,
        include_insights: bool | None = None,
    ) -> ConfidenceResult:
        """Score ``session``. ``user_prompt`` / ``response`` stand in for the agent's prompt and
        response when no :class:`AgentStep` carries them (e.g. LLM-only trajectories)."""
        signals = self.extractor.extract(session, user_prompt, response)
        derived = self.deriver.derive(signals)
        confidence, risks, explanation = self.aggregator.aggregate(signals, derived)
        want = self.include_insights if include_insights is None else include_insights
        found = None
        if want:
            from .insights import InsightsExtractor

            found = InsightsExtractor(self.config).extract(session, signals)
        return ConfidenceResult(
            confidence=confidence,
            risk_level=risk_level(confidence, self.config.risk_levels),
            derived=derived,
            risks=risks,
            explanation=explanation,
            signals=signals,
            insights=found,
            scorer_version=self.VERSION,
            thresholds_version=self.config.version,
        )


# ---------------------------------------------------------------------------------------------
# Batch summary (ConfidenceSummary)
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfidenceSummary:
    """Aggregated confidence over many traces."""

    mean_score: float
    min_score: float
    max_score: float
    std_dev: float
    median_score: float
    risk_distribution: dict[str, int]
    common_risks: list[str]  # risks raised in >= 50% of traces
    risk_frequency: dict[str, int]
    total_traces: int
    gated_count: int = 0  # traces below ``min_confidence``
    lowest_scoring: list[tuple[str, float]] = field(default_factory=list)  # bottom 3

    @classmethod
    def from_results(
        cls,
        results: Mapping[str, ConfidenceResult] | Sequence[ConfidenceResult],
        min_confidence: float | None = None,
    ) -> ConfidenceSummary:
        """Summarize results keyed by trace name (a sequence is keyed by its index)."""
        items = (
            list(results.items())
            if isinstance(results, Mapping)
            else [(str(i), r) for i, r in enumerate(results)]
        )
        if not items:
            return cls(0.0, 0.0, 0.0, 0.0, 0.0, dict.fromkeys(RISK_LEVELS, 0), [], {}, 0, 0, [])
        scores = [r.confidence for _, r in items]
        levels = Counter(r.risk_level for _, r in items)
        freq = Counter(risk for _, r in items for risk in r.risks)
        half = len(items) * 0.5
        gated = sum(1 for s in scores if s < min_confidence) if min_confidence is not None else 0
        lowest = sorted(items, key=lambda kv: kv[1].confidence)[:3]
        return cls(
            mean_score=statistics.mean(scores),
            min_score=min(scores),
            max_score=max(scores),
            std_dev=statistics.stdev(scores) if len(scores) > 1 else 0.0,
            median_score=statistics.median(scores),
            risk_distribution={lvl: levels.get(lvl, 0) for lvl in RISK_LEVELS},
            common_risks=[r for r, n in freq.items() if n >= half],
            risk_frequency=dict(freq),
            total_traces=len(items),
            gated_count=gated,
            lowest_scoring=[(name, r.confidence) for name, r in lowest],
        )

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["lowest_scoring"] = [list(p) for p in self.lowest_scoring]
        return out
