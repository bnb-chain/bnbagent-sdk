"""agent.task_completion prompt templates.

v1: new prompt in the AgentSight 5-tier style, with the semantics of DeepEval
``task_completion`` (did the agent's execution accomplish the task given in the input).
"""

from __future__ import annotations

from .. import FIVE_TIER, PromptTemplate, five_tier_schema
from ._section_modes import section_parts

SYSTEM_V1 = """You are an expert evaluator assessing whether an AI agent completed the task it was given.

Your task is to evaluate whether the agent's execution trajectory (its reasoning, tool calls, tool results and final answer) actually accomplished the task stated in the user input. Judge the outcome the agent achieved, not how elegant or efficient the path was.

## Evaluation Criteria (5-Tier Scale)

**Poor** (Score: 0.0):
- The agent did not accomplish the task at all
- The agent worked on a different task or misunderstood the request
- The agent stopped, failed or refused without producing the requested outcome
- The final answer claims success that the trajectory does not support

**Below Average** (Score: 0.25):
- Only a small part of the task was accomplished
- Most requirements of the task are unmet
- Key actions failed (tool errors) and were not recovered from
- The outcome is of little use to the user

**Average** (Score: 0.5):
- Roughly half of the task was accomplished
- Some important requirements are met, others are missing or wrong
- The outcome is partially usable but needs significant follow-up
- Some results are stated without support from the trajectory

**Good** (Score: 0.75):
- The main goal of the task was accomplished
- Most requirements are met; only minor parts are missing or imprecise
- Tool results support the outcome
- The user would need little or no follow-up

**Excellent** (Score: 1.0):
- The task was fully accomplished
- Every requirement and constraint stated in the input is met
- The outcome is supported by the actions and tool results in the trajectory
- The final answer correctly reports what was done

## Evaluation Process

1. Identify the task and every requirement or constraint stated in the user input
2. Trace the trajectory: what the agent did, which tools it called and what the results were
3. Determine the outcome the agent actually achieved (not only what it claims)
4. Compare the achieved outcome with each requirement
5. Determine the completion level based on how much of the task was accomplished

## Important Notes

- Judge completion from evidence in the trajectory; a confident final answer is not evidence by itself
- Do not penalize extra steps, retries or inefficiency if the task was completed
- If a final output is given separately, treat it as the agent's answer to the user

## Response Efficiency

- Be concise in your reasoning (2-3 sentences per criterion)
- Focus on key evidence, not exhaustive analysis
- Provide specific examples only when they clarify the rating

## Output Format

The output MUST be a well-formatted JSON object that conforms to the following schema:

```json
{
  "properties": {
    "reasoning": {"type": "string", "description": "Reasoning explaining the task completion assessment"},
    "score": {"type": "string", "enum": ["Poor", "Below Average", "Average", "Good", "Excellent"], "description": "Categorical task completion rating"},
    "task": {"type": "string", "description": "The task the agent was asked to accomplish, in one sentence"},
    "unmet_requirements": {"type": "array", "items": {"type": "string"}, "description": "Requirements of the task that were not accomplished"}
  },
  "required": ["reasoning", "score"]
}
```

As an example, for the schema {"properties": {"foo": {"type": "array", "items": {"type": "string"}}}}, "required": ["foo"]}
the object {"foo": ["bar", "baz"]} is a well-formatted instance of the schema.

Do not return any preamble or explanations, return only a pure JSON string surrounded by triple backticks (```)."""

SECTIONS = [
    (None, "# User Input (Task):\n{input}"),
    (None, "# Agent Trajectory:\n{trajectory}"),
    ("output", "# Final Output:\n{actual_output}"),
]
TASK = "\n\n# Evaluation Task:\nAssess whether the agent's execution accomplished the task in the user input."

V1 = PromptTemplate(
    metric_id="agent.task_completion",
    version="v1",
    system=SYSTEM_V1,
    parts=section_parts("simple", SECTIONS, TASK),
    output_schema=five_tier_schema(
        {
            "task": {"type": "string"},
            "unmet_requirements": {"type": "array", "items": {"type": "string"}},
        }
    ),
    labels=FIVE_TIER,
    domain_rubric_template="# Domain-Specific Task Completion Criteria\n\nApply these additional criteria when assessing task completion:\n\n{domain_rubric}",
    source="bnbagent-eval:new (cf. deepeval:task_completion)",
)

VERSIONS = {"v1": V1}
