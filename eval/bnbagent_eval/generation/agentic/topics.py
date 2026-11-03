"""AgentSight ``TopicPlanner``: a plan of diverse topics for test-case generation.

Port of ``agent_lens.generators.topic_planner`` onto the ``JudgeModel`` protocol (AgentSight
runs a Strands agent with a ``TopicPlan`` structured-output model). Topics beyond
``num_topics`` are dropped, as in AgentSight. :meth:`TopicPlan.focus_areas` turns a plan into
``AgenticGenerator`` focus areas. (AgentSight's ``DatasetGenerator`` is broken in its source and
is not ported.)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from ...judges.base import JudgeModel
from ...judges.prompts.generation.agentic import TOPICS_V1
from .._llm import Prompt, ask_json


@dataclass(frozen=True)
class Topic:
    title: str
    description: str
    key_aspects: tuple[str, ...]


@dataclass(frozen=True)
class TopicPlan:
    topics: tuple[Topic, ...]

    def focus_areas(self) -> list[str]:
        return [f"{t.title}: {t.description}" for t in self.topics]


class TopicPlanner:
    """Plans diverse topics for test case generation based on agent context."""

    def __init__(self, judge: JudgeModel, planning_prompt: str | None = None):
        self.judge = judge
        self.planning_prompt = planning_prompt or TOPICS_V1.system

    async def plan(
        self, context: str, task_description: str, num_topics: int, num_cases: int
    ) -> TopicPlan:
        if num_topics <= 0 or num_cases <= 0:
            raise ValueError("num_topics and num_cases must be > 0")
        material = {"c": context, "t": task_description, "n": num_topics, "k": num_cases}
        prompt = Prompt(TOPICS_V1, "topics", material)
        fields = {
            "num_topics": str(num_topics),
            "num_cases": str(num_cases),
            "cases_per_topic": str(math.ceil(num_cases / num_topics)),
            "context": prompt.fence("context", context),
            "task_description": prompt.fence("task_description", task_description),
        }
        system = self.planning_prompt + str(TOPICS_V1.extra["output"])

        def check(obj: dict[str, Any]) -> TopicPlan:
            topics = tuple(
                Topic(str(t["title"]), str(t["description"]), tuple(t["key_aspects"]))
                for t in obj["topics"]
            )
            return TopicPlan(topics[:num_topics])

        return await ask_json(
            self.judge, prompt, prompt.request(fields, system=system), check=check
        )

    plan_topics_async = plan  # AgentSight name
