"""Base for agent judges whose prompt has optional sections (task, tools, plan, reference).

In ``auto`` mode the input mode is derived from the optional sections that have data (see
``judges/prompts/agent/_section_modes.py``); an explicit ``input_mode`` selects that part as is.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from ...adapters.traces.render import render_trajectory, tools_text
from ...judges.prompts import PromptTemplate
from ...judges.prompts.agent._section_modes import section_mode
from ...types.case import EvalCase
from ...types.trajectory import LlmStep, Session
from ..base import MetricContext
from ..judge_base import AGENT_ID_PARAM, JudgeMetric, judge_params
from ..text._common import as_text
from .trajectory import expected_calls

AGENT_JUDGE_PARAMS = judge_params(AGENT_ID_PARAM)


def plan_text(session: Session | None, agent_id: str | None = None) -> str:
    """Text of every LLM step (where explicit plans and reasoning live), one block per step."""
    if session is None:
        return ""
    return "\n\n".join(
        f"[step {s.id}] {s.response}"
        for s in session.steps()
        if isinstance(s, LlmStep) and s.response
        # as the rendered trajectory: steps without an agent id are kept
        if agent_id is None or s.agent_id in (agent_id, None)
    )


def trajectory_record(session: Session, agent_id: str | None = None) -> str:
    """Rendered trajectory without its ``# Available tools`` header (rendered separately)."""
    text = render_trajectory(session, agent_id=agent_id)
    tools = tools_text(session)
    prefix = f"# Available tools\n{tools}"
    if tools and text.startswith(prefix):
        text = text[len(prefix) :].lstrip("\n")
    return text


def expected_calls_text(expected: Any) -> str:
    """Reference tool calls as ``- name(args)`` lines (raw JSON if not a tool-call list)."""
    try:
        calls = expected_calls(expected)
    except ValueError:
        return as_text(expected)
    return "\n".join(
        f"- {c.name}({json.dumps(dict(c.arguments), sort_keys=True, ensure_ascii=False)})"
        for c in calls
    )


class SectionJudge(JudgeMetric):
    """5-tier judge whose auto mode is ``simple`` / ``with_<section>_and_...``."""

    optional_sections: ClassVar[tuple[str, ...]] = ()  # in prompt order
    separate_tools: ClassVar[bool] = False  # render available tools outside the trajectory

    def base_mode(self, case: EvalCase, ctx: MetricContext) -> str:
        return "simple"

    def section_present(self, name: str, case: EvalCase, ctx: MetricContext) -> bool:
        if name == "tools":
            return case.trajectory is not None and bool(tools_text(case.trajectory))
        if name == "plan":
            return bool(plan_text(case.trajectory, ctx.params.get("agent_id")))
        return case.has(name)

    def select_mode(self, case: EvalCase, ctx: MetricContext, template: PromptTemplate) -> str:
        if ctx.params.get("input_mode", "auto") != "auto":
            return super().select_mode(case, ctx, template)
        present = [s for s in self.optional_sections if self.section_present(s, case, ctx)]
        return section_mode(self.base_mode(case, ctx), present)

    def fields(self, case: EvalCase, ctx: MetricContext, mode: str) -> dict[str, str]:
        out = super().fields(case, ctx, mode)
        agent_id = ctx.params.get("agent_id")
        if self.separate_tools and case.trajectory is not None:
            out["trajectory"] = trajectory_record(case.trajectory, agent_id)
        if "expected" in self.optional_sections and case.expected is not None:
            out["expected_output"] = expected_calls_text(case.expected)
        out["plan_text"] = plan_text(case.trajectory, agent_id)
        return out
