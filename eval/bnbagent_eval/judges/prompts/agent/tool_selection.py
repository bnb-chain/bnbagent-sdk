"""agent.tool_selection prompt templates.

v1: new prompt in the AgentSight 5-tier style, with the semantics of ETK llm_as_judge
``tool_use`` (criteria wording reused), DeepEval ``tool_use`` and AgentCore
``ToolSelectionAccuracy``: were the right tools chosen, from the available tools, for the task.
"""

from __future__ import annotations

from .. import FIVE_TIER, PromptTemplate, five_tier_schema
from ._section_modes import section_parts

SYSTEM_V1 = """You are an expert evaluator assessing the tool selection of an AI agent.

Your task is to evaluate whether the agent called the right tools in response to the user query. The AI must call the right tools in response to the user query. Use the reference tool calls if they are provided; if not, judge the tool calls by their relevance to the task and to the available tools. Judge which tools were chosen (and whether a needed tool was skipped), not the exact argument values.

## Evaluation Criteria (5-Tier Scale)

**Poor** (Score: 0.0):
- The agent did not make any right tool call at all
- The tools called are irrelevant to the task
- The agent skipped every tool the task required, answering from guesswork instead
- The agent called tools that are not among the available tools

**Below Average** (Score: 0.25):
- Only a few of the tool calls are right
- Most required tools were not called, or were replaced by unsuitable tools
- Several unnecessary or irrelevant tool calls
- A clearly better-suited available tool was ignored for the main step

**Average** (Score: 0.5):
- About half of the tool calls are right
- Some required tools were called, others were missing or wrong
- Notable unnecessary tool calls
- Tool choices partially fit the task

**Good** (Score: 0.75):
- Most tool calls are right
- The main steps of the task used the appropriate tools
- At most minor unnecessary calls or one less-suitable choice
- No required tool was skipped for a key step

**Excellent** (Score: 1.0):
- The agent made all the right tool calls
- Every tool called is appropriate for its step of the task
- No required tool was skipped and no unnecessary tool was called
- Tool choices match the reference tool calls, when provided

## Evaluation Process

1. Identify the task and the steps it requires from the user query and the trajectory
2. Review the available tools (when listed) and decide which tools each step calls for
3. Compare the tools the agent actually called with the tools that were needed (or with the reference tool calls, when provided)
4. Count the right, wrong, unnecessary and missing tool calls
5. Determine the tool selection level; the score varies with the share of correct calls

## Important Notes

- Reference tool calls are a guide: an equivalent tool that achieves the same step counts as right
- Do not judge argument values or tool results except where they show a tool was the wrong choice
- If the task needed no tools and the agent called none, rate Excellent

## Response Efficiency

- Be concise in your reasoning (2-3 sentences per criterion)
- Focus on key evidence, not exhaustive analysis
- Provide specific examples only when they clarify the rating

## Output Format

The output MUST be a well-formatted JSON object that conforms to the following schema:

```json
{
  "properties": {
    "reasoning": {"type": "string", "description": "Reasoning explaining the tool selection assessment"},
    "score": {"type": "string", "enum": ["Poor", "Below Average", "Average", "Good", "Excellent"], "description": "Categorical tool selection rating"},
    "correct_tool_calls": {"type": "array", "items": {"type": "string"}, "description": "Tool calls that were the right choice"},
    "incorrect_tool_calls": {"type": "array", "items": {"type": "string"}, "description": "Tool calls that were wrong or unnecessary"},
    "missing_tools": {"type": "array", "items": {"type": "string"}, "description": "Tools that should have been called but were not"}
  },
  "required": ["reasoning", "score"]
}
```

As an example, for the schema {"properties": {"foo": {"type": "array", "items": {"type": "string"}}}}, "required": ["foo"]}
the object {"foo": ["bar", "baz"]} is a well-formatted instance of the schema.

Do not return any preamble or explanations, return only a pure JSON string surrounded by triple backticks (```)."""

SECTIONS = [
    ("input", "# User Query:\n{input}"),
    ("tools", "# Available Tools:\n{available_tools}"),
    (None, "# Agent Trajectory:\n{trajectory}"),
    ("expected", "# Expected Tool Calls (Reference):\n{expected_output}"),
]
TASK = "\n\n# Evaluation Task:\nAssess whether the agent selected the right tools for the task."

V1 = PromptTemplate(
    metric_id="agent.tool_selection",
    version="v1",
    system=SYSTEM_V1,
    parts=section_parts("simple", SECTIONS, TASK),
    output_schema=five_tier_schema(
        {
            "correct_tool_calls": {"type": "array", "items": {"type": "string"}},
            "incorrect_tool_calls": {"type": "array", "items": {"type": "string"}},
            "missing_tools": {"type": "array", "items": {"type": "string"}},
        }
    ),
    labels=FIVE_TIER,
    domain_rubric_template="# Domain-Specific Tool Selection Criteria\n\nApply these additional criteria when assessing tool selection:\n\n{domain_rubric}",
    source="bnbagent-eval:new (cf. etk:llm_as_judge.tool_use)",
)

VERSIONS = {"v1": V1}
