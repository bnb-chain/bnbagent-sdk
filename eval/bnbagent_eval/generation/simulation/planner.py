"""Coverage-guided scenario planner (port of AgentSight ``agent_sim.simulation.planner``).

The judge proposes one scenario per call (``SCENARIO_GENERATION_PROMPT``, verbatim) given the
current coverage and the previous scenarios; duplicate intents are retried (more than 5
consecutive retries end planning). ``seed`` makes the run reproducible: it picks the suggested
focus among the least-covered stress axes and is part of every request key.
"""

from __future__ import annotations

import json
import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ...engine.simulated import ask_json
from ...judges.base import JudgeModel
from ...judges.prompts import PromptTemplate
from ...types.simulation import FailureMode, Scenario, StressAxis, parse_enums

SCENARIO_GENERATION_PROMPT = """
Generate ONE test scenario with multiple sub-intent variations.

Stress axes: {stress_axes}
Failure modes: {failure_modes}

Current coverage: {coverage}
Previously generated: {previous_scenarios}

Agent: {agent_system_prompt}
Tools: {agent_tools}
Intents: {intents}
Entities: {domain_entities}
Common Bottlenecks: {common_bottlenecks}

Number of sub-intents to generate: {num_sub_intents}

Rules:
- Pick one intent from the list
- Generate exactly {num_sub_intents} unique sub_intents as a list
- Each sub_intent MUST describe a SPECIFIC outcome or edge case
  (e.g., "return approved within 30 days", "return rejected - final sale item",
  "return approved with restocking fee", "return rejected - item damaged by customer")
- Sub_intents should cover different success/failure paths, edge cases, and policy boundaries
- Make sub_intents concrete and testable, not vague descriptions
- Consider the common bottlenecks when designing stress scenarios
- Prefer uncovered stress axes
- Prefer rare failure modes

Examples of GOOD sub_intents:
- "refund approved - full amount to original payment"
- "refund rejected - outside 30-day window"
- "refund approved - store credit only due to missing receipt"
- "refund rejected - item shows signs of use"

Examples of BAD sub_intents (too vague):
- "successful refund"
- "refund with issues"
- "complex refund scenario"
"""

FOCUS_HINT = "\nSuggested focus (least covered stress axes): {focus}\n"

PLANNER_SYSTEM_V1 = """You design test scenarios for evaluating an AI agent.

## Output Format

Return only a JSON object with the fields: intent (string), sub_intent (list of strings),
relevant_entities (list of strings), stress_axes (list of stress axis values) and
expected_failures (list of failure mode values). Use only the listed axis and mode values."""

_LIST = {"type": "array", "items": {"type": "string"}}

SCENARIO_V1 = PromptTemplate(
    metric_id="simulation.scenario_planner",
    version="v1",
    system=PLANNER_SYSTEM_V1,
    parts={"simple": SCENARIO_GENERATION_PROMPT + FOCUS_HINT},
    output_schema={
        "type": "object",
        "required": ["intent", "sub_intent", "expected_failures"],
        "properties": {
            "intent": {"type": "string"},
            "sub_intent": _LIST,
            "relevant_entities": _LIST,
            "stress_axes": _LIST,
            "expected_failures": _LIST,
        },
    },
    source="agentsight:agent_sim.SCENARIO_GENERATION_PROMPT",
)

MAX_RETRIES = 5


@dataclass(frozen=True)
class GroundingProfile:
    """Domain-specific grounding for agent behaviour patterns."""

    intents: tuple[str, ...]
    domain_entities: tuple[str, ...] = ()
    common_bottlenecks: tuple[str, ...] = ()
    interaction_budget: tuple[int, int] = (2, 5)  # [min, max] turns per conversation
    utterance_length: int = 50  # max words per utterance
    custom_user_persona: str = ""  # merged with the competency level descriptions


@dataclass(frozen=True)
class SimulationInput:
    """Complete input configuration for scenario generation."""

    agent_system_prompt: str
    agent_tools: tuple[str, ...]
    grounding_profile: GroundingProfile
    num_scenarios: int = 3
    num_sub_intents_per_scenario: int = 2


@dataclass(frozen=True)
class SimulationOutput:
    scenarios: tuple[Scenario, ...]
    coverage_report: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


class CoverageTracker:
    """Tracks coverage of stress axes, failure modes and intents."""

    def __init__(self) -> None:
        self.stress = {a.value: 0 for a in StressAxis}
        self.failure = {f.value: 0 for f in FailureMode}
        self.intent: dict[str, int] = {}

    def update(self, scenario: Scenario) -> None:
        for axis in scenario.stress_axes:
            self.stress[axis.value] += 1
        for f in scenario.expected_failures:
            self.failure[f.value] += 1
        self.intent[scenario.intent] = self.intent.get(scenario.intent, 0) + 1

    def report(self) -> dict[str, Any]:
        return {
            "stress_axis_coverage": dict(self.stress),
            "failure_mode_coverage": dict(self.failure),
            "intent_coverage": dict(self.intent),
        }

    def uncovered(self) -> list[StressAxis]:
        return [a for a in StressAxis if self.stress[a.value] == 0]

    def least_covered(self, k: int, rng: random.Random) -> list[StressAxis]:
        """``k`` stress axes with the lowest counts; ties broken by ``rng``."""
        axes = list(StressAxis)
        rng.shuffle(axes)
        axes.sort(key=lambda a: self.stress[a.value])
        return axes[:k]


class ScenarioPlanner:
    """Generates scenarios with ``judge``, guided by a :class:`CoverageTracker`."""

    def __init__(self, judge: JudgeModel, *, seed: int = 0, focus_axes: int = 2):
        self.judge = judge
        self.seed = seed
        self.focus_axes = focus_axes
        self.coverage = CoverageTracker()

    def _fields(
        self, inp: SimulationInput, previous: Sequence[Scenario], rng: random.Random
    ) -> dict[str, Any]:
        prev = [
            {
                "intent": s.intent,
                "sub_intent": list(s.sub_intent),
                "stress_axes": [a.value for a in s.stress_axes],
                "expected_failures": [f.value for f in s.expected_failures],
            }
            for s in previous
        ]
        g = inp.grounding_profile
        focus = self.coverage.least_covered(self.focus_axes, rng)
        return {
            "stress_axes": [a.value for a in StressAxis],
            "failure_modes": [f.value for f in FailureMode],
            "coverage": json.dumps(self.coverage.report(), indent=2),
            "previous_scenarios": json.dumps(prev, indent=2),
            "agent_system_prompt": inp.agent_system_prompt,
            "agent_tools": list(inp.agent_tools),
            "intents": list(g.intents),
            "domain_entities": list(g.domain_entities),
            "common_bottlenecks": list(g.common_bottlenecks),
            "num_sub_intents": inp.num_sub_intents_per_scenario,
            "focus": [a.value for a in focus],
        }

    @staticmethod
    def to_scenario(spec: dict[str, Any], index: int) -> Scenario:
        return Scenario(
            intent=str(spec["intent"]),
            sub_intent=tuple(map(str, spec.get("sub_intent", ()))),
            relevant_entities=tuple(map(str, spec.get("relevant_entities", ()))),
            stress_axes=parse_enums(StressAxis, spec.get("stress_axes", ())),
            expected_failures=parse_enums(FailureMode, spec.get("expected_failures", ())),
            metadata={"index": index},
        )

    async def plan(self, inp: SimulationInput) -> SimulationOutput:
        self.coverage = CoverageTracker()
        rng = random.Random(self.seed)
        scenarios: list[Scenario] = []
        retries = calls = 0
        while len(scenarios) < inp.num_scenarios and retries <= MAX_RETRIES:
            fields = self._fields(inp, scenarios, rng)
            spec = await ask_json(
                self.judge,
                SCENARIO_V1,
                "simple",
                fields,
                key=f"plan:{self.seed}:{calls}",
                trusted=tuple(fields),
            )
            calls += 1
            scenario = self.to_scenario(spec, len(scenarios))
            if scenario.intent in self.coverage.intent:
                retries += 1
                continue
            self.coverage.update(scenario)
            scenarios.append(scenario)
            retries = 0
        return SimulationOutput(
            scenarios=tuple(scenarios),
            coverage_report=self.coverage.report(),
            metadata={"count": len(scenarios), "judge_calls": calls, "seed": self.seed},
        )
