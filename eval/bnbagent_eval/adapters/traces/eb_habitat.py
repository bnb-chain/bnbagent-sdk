"""EmbodiedBench EB-Habitat episodes -> Session.

Input: ``{"instruction": <task>, "input": <prompt listing "action id N: name">, "trajectory":
[{"language_plan", "reasoning_and_reflection", "executable_plan": [{"step_id", "action":
[id, name], "action_success", "env_feedback"}]}]}``.

Provenance: AgentSight ``EBHabitatSessionMapper`` (eb_habitat_mapper.py). Each planning step
becomes an agent step (system prompt = the input prompt, user prompt = the instruction,
response = the language plan, tools = the ``action id`` lines), chained to the previous
planning step; each executed action becomes a tool step under it with arguments
``{"action_id": N}`` and result ``"Success: <s>, Feedback: <f>"`` (an error when the action
did not succeed).

Deviations from AgentSight: ids are deterministic (``eb-<n>``), no timestamps are invented
(AgentSight used UUIDs and ``datetime.now()``), and failed actions are flagged as errors. The
reasoning text (AgentSight ``ToolCall.thinking``) has no field in our model and is dropped.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...types.trajectory import AgentStep, Session, Step, ToolCall, ToolResult, ToolStep
from ._common import Warnings, as_float, as_list, finalize_trace, text_of, tool_def

AGENT_ID = "eb_habitat_agent"


def _tools(prompt: str) -> tuple[dict[str, Any], ...]:
    tools = []
    for line in prompt.split("\n"):
        if line.startswith("action id"):
            parts = line.split(": ", 1)
            if len(parts) > 1:
                tools.append(tool_def(parts[1].rstrip(", ")))
    return tuple(tools)


class EbHabitatAdapter:
    name = "eb_habitat"

    def detect(self, data: Any) -> float:
        if not isinstance(data, Mapping) or not isinstance(data.get("trajectory"), list):
            return 0.0
        steps = [s for s in data["trajectory"] if isinstance(s, Mapping)]
        if steps and any("executable_plan" in s for s in steps):
            return 0.95
        if "instruction" in data and steps and any("language_plan" in s for s in steps):
            return 0.8
        return 0.0

    def to_session(self, data: Any, *, session_id: str | None = None) -> Session:
        warnings = Warnings()
        data = data if isinstance(data, Mapping) else {}
        prompt = text_of(data.get("input", ""))
        tools = _tools(prompt)
        steps: list[Step] = []
        parent: str | None = None
        for n, plan in enumerate(as_list(data.get("trajectory"))):
            if not isinstance(plan, Mapping):
                warnings.add(f"trajectory[{n}] is not an object; skipped")
                continue
            agent = AgentStep(
                id=f"eb-{n}",
                parent_id=parent,
                agent_id=AGENT_ID,
                system_prompt=prompt or None,
                user_prompt=text_of(data.get("instruction", "")) or None,
                response=text_of(plan.get("language_plan", "")) or None,
                available_tools=tools,
            )
            steps.append(agent)
            parent = agent.id
            for m, action_step in enumerate(as_list(plan.get("executable_plan"))):
                if not isinstance(action_step, Mapping):
                    continue
                action = as_list(action_step.get("action"))
                action_id = action[0] if action else 0
                name = text_of(action[1]) if len(action) > 1 else ""
                success = action_step.get("action_success", 0.0)
                feedback = text_of(action_step.get("env_feedback", ""))
                content = f"Success: {success}, Feedback: {feedback}"
                failed = as_float(success) is not None and (as_float(success) or 0.0) <= 0.0
                call_id = text_of(action_step.get("step_id", "")) or None
                steps.append(
                    ToolStep(
                        id=f"eb-{n}-{m}",
                        parent_id=agent.id,
                        error=content if failed else None,
                        call=ToolCall(name=name, arguments={"action_id": action_id}, id=call_id),
                        result=ToolResult(
                            content=content, error=content if failed else None, call_id=call_id
                        ),
                    )
                )
        if not steps:
            warnings.add("EB-Habitat episode has no trajectory steps")
        traces = (
            (finalize_trace("eb-habitat", steps, warnings, expect_timestamps=False, sort=False),)
            if steps
            else ()
        )
        return Session(
            id=session_id or "eb-habitat-session", traces=traces, warnings=warnings.as_tuple()
        )
