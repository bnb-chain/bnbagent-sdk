"""Simulated-user runs (port of ``agent_sim`` ``simulator_offline.run_simulation``).

One conversation per scenario × sub-intent × persona (by default the four competency levels),
each driven by :class:`~bnbagent_eval.engine.simulated.SimulatedUserTarget` against the real
target (``agent_sim`` used a mock agent offline). Score the cases with ``conversation.*`` metrics,
e.g. ``conversation.failure_modes`` (the ``ConversationAnalyzer``).
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from ...engine.simulated import SimulatedUserTarget, StopWhen
from ...judges.base import JudgeModel
from ...types.case import EvalCase
from ...types.simulation import PERSONAS, CompetencyLevel, Persona, Scenario
from .planner import ScenarioPlanner, SimulationInput, SimulationOutput

if TYPE_CHECKING:
    from ...engine.targets import Target

PersonaLike = Persona | CompetencyLevel | str | Mapping[str, Any]


async def simulate(
    target: Target,
    scenarios: Iterable[Scenario | Mapping[str, Any] | str] | SimulationOutput,
    *,
    user_judge: JudgeModel,
    max_turns: int | tuple[int, int] = 5,
    stop_when: StopWhen | None = None,
    personas: Iterable[PersonaLike] = PERSONAS,
    seed: int = 0,
    concurrency: int = 4,
) -> list[EvalCase]:
    """Run every scenario × sub-intent × persona; return one case per conversation.

    ``max_turns`` is a turn cap, or ``(min, max)`` to draw it per conversation with a seeded RNG
    (``agent_sim`` ``interaction_budget``). Cases come back in input order.
    """
    if isinstance(scenarios, SimulationOutput):
        scenarios = scenarios.scenarios
    plans = [Scenario.coerce(s) for s in scenarios]
    people = [Persona.coerce(p) for p in personas]
    rng = random.Random(seed)
    jobs: list[tuple[EvalCase, SimulatedUserTarget]] = []
    for i, scenario in enumerate(plans):
        for j, sub in enumerate(scenario.sub_intent or (scenario.intent,)):
            for persona in people:
                turns = rng.randint(*max_turns) if isinstance(max_turns, tuple) else max_turns
                case = EvalCase(
                    id=f"sim-{i}-{j}-{persona.competency.value}",
                    metadata={"source": "bnbagent-eval/simulation", "seed": seed},
                )
                sim = SimulatedUserTarget(
                    target,
                    persona,
                    scenario,
                    user_judge,
                    turns,
                    sub_intent=sub,
                    stop_when=stop_when,
                )
                jobs.append((case, sim))
    gate = asyncio.Semaphore(max(1, concurrency))

    async def one(case: EvalCase, sim: SimulatedUserTarget) -> EvalCase:
        async with gate:
            return await sim.invoke(case)

    return list(await asyncio.gather(*(one(c, s) for c, s in jobs)))


async def run_simulation(
    target: Target,
    simulation_input: SimulationInput,
    *,
    planner_judge: JudgeModel,
    user_judge: JudgeModel | None = None,
    seed: int = 0,
    personas: Iterable[PersonaLike] | None = None,
    stop_when: StopWhen | None = None,
    concurrency: int = 4,
) -> tuple[list[EvalCase], SimulationOutput]:
    """Plan scenarios, then simulate them (turn budget from the grounding profile)."""
    plan = await ScenarioPlanner(planner_judge, seed=seed).plan(simulation_input)
    g = simulation_input.grounding_profile
    if personas is None:
        personas = [
            Persona(level, g.custom_user_persona, g.utterance_length) for level in CompetencyLevel
        ]
    lo, hi = g.interaction_budget
    cases = await simulate(
        target,
        plan,
        user_judge=user_judge or planner_judge,
        max_turns=(lo, hi),
        stop_when=stop_when,
        personas=personas,
        seed=seed,
        concurrency=concurrency,
    )
    return cases, plan
