"""Agentic test generation (``generation.agentic``).

Port of ETK ``generator_builtin.agentic.AgenticGenerator``::

    [seed traces] -> taxonomy -> scenarios -> generation -> augmentation -> review sampling

- seed traces (optional): any format ``adapters.traces`` loads; conversations are sampled and
  clustered, and ground the taxonomy (observed tools), planning (real failures) and generation
  (per-scenario few-shot examples). Without ``tools``, the tools the traces declare are used;
- taxonomy: the judge proposes tools, combinations and topics; turn counts, difficulty and
  outcomes are the caller's (``taxonomy=``). On failure, a derived taxonomy is used;
- scenarios: planned in batches of 15 with coverage feedback, validated against the taxonomy;
- generation: one case per scenario; tool calls must validate against the tool JSON schemas
  (:class:`ToolValidator`) and use exactly the planned tools; bad replies are repaired;
- augmentation: ``rephrase``, ``parameter_variation``, ``multi_turn_extension``, ``edge_case``,
  ``negative_case`` (none by default, as in ETK);
- review sampling: ``ceil(0.2 x group)`` cases per provenance source get
  ``metadata.requires_review`` (seeded, so runs are reproducible).

Each case becomes an :class:`EvalCase` with ``expected = {tool_calls, response, outcome,
topics}`` (``tool_calls`` flat, in the ``agent.tool_trajectory_match`` shape; multi-turn cases
add ``tool_calls_by_turn`` and have ``input = {"turns": [...]}``).
"""

from __future__ import annotations

import logging
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ...exceptions import JudgeError, JudgeNonRetryable
from ...judges.base import JudgeModel
from ...types.case import EvalCase
from .._llm import GenerationError
from .models import (
    STRATEGIES,
    ExtractedConversation,
    GeneratedCase,
    Scenario,
    SeedExample,
    Taxonomy,
    TaxonomyConfig,
    ToolSpec,
    TraceCoverage,
    TraceSampling,
    calls_to_wire,
    tool_specs,
)
from .onchain import (
    ONCHAIN_SYSTEM_PROMPT,
    ONCHAIN_TOOLS,
    SYNTHETIC_ADDRESSES,
    onchain_example_cases,
    onchain_seed_examples,
)
from .seeding import (
    compute_trace_coverage,
    conversation_to_seed_example,
    conversations_to_seed_examples,
    extract_conversations,
    find_matching_trace_examples,
    load_sessions,
    select_diverse_failure_patterns,
    session_to_conversation,
    tools_from_sessions,
)
from .stages import (
    apply_review_sampling,
    augment_cases,
    build_default_taxonomy,
    build_taxonomy,
    filter_applicable_strategies,
    generate_cases,
    sample_scenarios,
)
from .topics import Topic, TopicPlan, TopicPlanner
from .validator import ToolValidator, check_tool_calls

__all__ = [
    "ONCHAIN_SYSTEM_PROMPT",
    "ONCHAIN_TOOLS",
    "STRATEGIES",
    "SYNTHETIC_ADDRESSES",
    "AgenticGenerator",
    "AgenticRun",
    "GeneratedCase",
    "GenerationError",
    "Scenario",
    "SeedExample",
    "Taxonomy",
    "TaxonomyConfig",
    "ToolSpec",
    "ToolValidator",
    "Topic",
    "TopicPlan",
    "TopicPlanner",
    "TraceSampling",
    "apply_review_sampling",
    "build_default_taxonomy",
    "check_tool_calls",
    "onchain_example_cases",
    "onchain_seed_examples",
    "session_to_conversation",
    "to_eval_case",
]

logger = logging.getLogger(__name__)

DEFAULT_CONCURRENCY = 10  # ETK
DEFAULT_AUGMENTATION_CONCURRENCY = 5  # ETK
DEFAULT_REVIEW_SAMPLE_RATE = 0.2  # ETK
_STRATEGY_OUTCOME = {"negative_case": "refusal", "edge_case": None}


@dataclass
class AgenticRun:
    """Intermediate artefacts of the last run (ETK writes them as taxonomy/scenarios.json)."""

    taxonomy: Taxonomy
    scenarios: list[Scenario]
    cases: list[GeneratedCase]
    conversations: list[ExtractedConversation] = field(default_factory=list)
    trace_coverage: TraceCoverage | None = None
    review: dict[str, tuple[int, int]] = field(default_factory=dict)


def to_eval_case(case: GeneratedCase, id_prefix: str = "agentic") -> EvalCase:
    prov = case.provenance
    scenario = case.scenario
    outcome = scenario.outcome if scenario else None
    if prov.augmentation_strategy in _STRATEGY_OUTCOME:
        outcome = _STRATEGY_OUTCOME[prov.augmentation_strategy]
    flat = [{"name": c.name, "arguments": dict(c.arguments)} for c in case.flat_calls()]
    expected: dict[str, Any] = {
        "tool_calls": flat,
        "response": case.response,
        "outcome": outcome,
        "topics": list(case.topics),
    }
    if case.tool_calls and isinstance(case.tool_calls[0], tuple):
        expected["tool_calls_by_turn"] = calls_to_wire(case.tool_calls, "arguments")
    multi = isinstance(case.input, tuple)
    metadata: dict[str, Any] = {
        "generator": "generation.agentic",
        "provenance": prov.to_dict(),
        "requires_review": case.requires_review,
    }
    if scenario is not None:
        metadata["scenario"] = {
            "id": scenario.id,
            "description": scenario.description,
            "turns": scenario.turns,
            "difficulty": scenario.difficulty,
            "outcome": scenario.outcome,
            "tools_involved": list(scenario.tools_involved),
            "priority": scenario.priority,
        }
    return EvalCase(
        id=f"{id_prefix}-{prov.scenario_id}",
        input={"turns": list(case.input)} if multi else case.input,
        expected=expected,
        metadata=metadata,
    )


class AgenticGenerator:
    """Agent test cases with planned tool-use coverage (ETK ``AgenticGenerator``)."""

    def __init__(
        self,
        judge: JudgeModel,
        tools: Iterable[ToolSpec | Mapping[str, Any]] = (),
        taxonomy: Taxonomy | TaxonomyConfig | Mapping[str, Any] | None = None,
        *,
        system_prompt: str | None = None,
        focus_areas: Sequence[str] = (),
        seed_examples: Iterable[SeedExample | Mapping[str, Any]] = (),
        augmentations: Sequence[str] = (),
        review_sample_rate: float = DEFAULT_REVIEW_SAMPLE_RATE,
        trace_sampling: TraceSampling | None = None,
        seed: int = 0,
        concurrency: int = DEFAULT_CONCURRENCY,
        augmentation_concurrency: int = DEFAULT_AUGMENTATION_CONCURRENCY,
        id_prefix: str = "agentic",
    ):
        self.judge = judge
        self.tools = tool_specs(tools)
        ToolValidator(self.tools).check_schemas()  # a bad schema is a config error: fail early
        self.fixed_taxonomy: Taxonomy | None = None
        if isinstance(taxonomy, Taxonomy):
            self.fixed_taxonomy = taxonomy
        elif isinstance(taxonomy, Mapping) and isinstance(taxonomy.get("axes"), Mapping):
            axes = TaxonomyConfig.from_any(taxonomy["axes"])
            self.fixed_taxonomy = Taxonomy.from_prompt(taxonomy, axes)
        self.axes = (
            self.fixed_taxonomy.config
            if self.fixed_taxonomy
            else TaxonomyConfig.from_any(taxonomy)  # type: ignore[arg-type]
        )
        unknown = [s for s in augmentations if s not in STRATEGIES]
        if unknown:
            raise ValueError(f"unknown augmentation strategies {unknown}; one of {STRATEGIES}")
        if not 0.0 <= review_sample_rate <= 1.0:
            raise ValueError("review_sample_rate must be in [0, 1]")
        self.system_prompt = system_prompt
        self.focus_areas = list(focus_areas)
        self.seed_examples = [SeedExample.from_any(e) for e in seed_examples]
        self.augmentations = list(augmentations)
        self.review_sample_rate = review_sample_rate
        self.trace_sampling = trace_sampling or TraceSampling()
        self.seed = seed
        self.concurrency, self.augmentation_concurrency = concurrency, augmentation_concurrency
        self.id_prefix = id_prefix
        self.last_run: AgenticRun | None = None

    async def _taxonomy(self, tools: list[ToolSpec], coverage: TraceCoverage | None) -> Taxonomy:
        if self.fixed_taxonomy is not None:
            return self.fixed_taxonomy
        try:
            return await build_taxonomy(
                self.judge,
                system_prompt=self.system_prompt,
                tools=tools,
                focus_areas=self.focus_areas,
                axes=self.axes,
                trace_coverage=coverage,
            )
        except JudgeNonRetryable:
            raise
        except JudgeError as exc:
            logger.warning("taxonomy construction failed, using the derived default: %s", exc)
            return build_default_taxonomy(tools, self.focus_areas, self.axes)

    async def generate(
        self, n: int, *, seed_traces: Iterable[Any] = (), trace_format: str = "auto"
    ) -> list[EvalCase]:
        """``n`` planned base cases (plus augmentations) as :class:`EvalCase` values.

        ``seed_traces``: ``Session`` objects, parsed trace data or trace file paths.
        """
        if n <= 0:
            raise ValueError("n must be > 0")
        rng = random.Random(self.seed)
        sessions = load_sessions(seed_traces, trace_format)
        tools = self.tools or tool_specs(tools_from_sessions(sessions))
        validator = ToolValidator(tools)
        validator.check_schemas()
        conversations = (
            extract_conversations(sessions, self.trace_sampling, rng) if sessions else []
        )
        coverage = compute_trace_coverage(conversations) if conversations else None
        seeds = self.seed_examples or conversations_to_seed_examples(conversations)

        taxonomy = await self._taxonomy(tools, coverage)
        failures = [c for c in conversations if c.outcome == "failure"]
        scenarios = await sample_scenarios(
            self.judge,
            taxonomy=taxonomy,
            total=n,
            focus_areas=self.focus_areas,
            tool_names=[t.name for t in tools],
            trace_coverage=coverage,
            failures=select_diverse_failure_patterns(failures),
            total_failures=len(failures),
        )

        def finder(tools_involved: Sequence[str], outcome: str) -> list[SeedExample]:
            matches = find_matching_trace_examples(conversations, tools_involved, outcome)
            return [conversation_to_seed_example(c) for c in matches]

        base = await generate_cases(
            self.judge,
            scenarios,
            system_prompt=self.system_prompt,
            tools=tools,
            seed_examples=seeds,
            validator=validator,
            concurrency=self.concurrency,
            trace_examples=finder if conversations else None,
        )
        strategies = filter_applicable_strategies(self.augmentations, self.system_prompt, tools)
        augmented = (
            await augment_cases(
                self.judge,
                base,
                strategies,
                system_prompt=self.system_prompt,
                tools=tools,
                validator=validator,
                concurrency=self.augmentation_concurrency,
            )
            if strategies and base
            else []
        )
        final, review = apply_review_sampling(base + augmented, self.review_sample_rate, rng)
        self.last_run = AgenticRun(taxonomy, scenarios, final, conversations, coverage, review)
        return [to_eval_case(c, self.id_prefix) for c in final]
