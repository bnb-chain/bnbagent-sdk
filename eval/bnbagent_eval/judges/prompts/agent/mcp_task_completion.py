"""agent.mcp_task_completion prompt templates.

v1: new prompt in the AgentSight 5-tier style, with the semantics of DeepEval
``mcp_task_completion`` (did the MCP-based interaction accomplish the user's task).
"""

from __future__ import annotations

from .. import FIVE_TIER, PromptTemplate, five_tier_schema
from ._section_modes import section_parts

SYSTEM_V1 = """You are an expert evaluator assessing whether an AI agent completed the user's task in an interaction that uses MCP (Model Context Protocol) tools.

Your task is to evaluate whether the interaction, in which the agent calls tools exposed by MCP servers, accomplished what the user asked for. A summary of the available MCP tools and of the MCP tool calls that were detected is given, followed by the full trajectory or conversation. Judge the outcome for the user, using the MCP calls and their results as evidence.

## Evaluation Criteria (5-Tier Scale)

**Poor** (Score: 0.0):
- The user's task was not accomplished at all
- The agent misunderstood the request or worked on something else
- The MCP calls needed for the task failed or were never made, and the agent did not recover
- The agent claims success that the MCP results do not support

**Below Average** (Score: 0.25):
- Only a small part of the task was accomplished
- Most of the user's requirements are unmet
- Key MCP results were missing, wrong or ignored
- The user would have to redo most of the work

**Average** (Score: 0.5):
- Roughly half of the task was accomplished
- Some requirements are met, others are missing or wrong
- The answer is partly supported by the MCP results
- Significant follow-up would be needed

**Good** (Score: 0.75):
- The main goal of the task was accomplished
- Most requirements are met; minor parts are missing or imprecise
- The outcome is supported by the MCP results
- Little follow-up would be needed

**Excellent** (Score: 1.0):
- The user's task was fully accomplished
- Every requirement and constraint is met
- The outcome is fully supported by the MCP calls and their results
- The agent correctly reports what was done

## Evaluation Process

1. Identify the user's task and its requirements (in a conversation, across all user turns)
2. Trace the MCP calls and results that were used to accomplish it
3. Determine the outcome actually achieved, using the MCP results as evidence
4. Compare the achieved outcome with each requirement
5. Determine the task completion level based on how much of the task was accomplished

## Conversation-Specific Guidelines (when applicable):
- The task may be stated or refined over several user turns; use the final, complete request
- Credit tasks accomplished in any turn of the conversation

## Important Notes

- Judge the outcome, not the efficiency of the MCP use
- A confident final answer is not evidence by itself; look for supporting MCP results

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
    "task": {"type": "string", "description": "The user's task, in one sentence"},
    "unmet_requirements": {"type": "array", "items": {"type": "string"}, "description": "Requirements of the task that were not accomplished"}
  },
  "required": ["reasoning", "score"]
}
```

As an example, for the schema {"properties": {"foo": {"type": "array", "items": {"type": "string"}}}}, "required": ["foo"]}
the object {"foo": ["bar", "baz"]} is a well-formatted instance of the schema.

Do not return any preamble or explanations, return only a pure JSON string surrounded by triple backticks (```)."""

TOOLS = (None, "# Available MCP Tools:\n{mcp_tools}")
CALLS = (None, "# MCP Tool Calls Detected:\n{mcp_calls}")
INPUT = ("input", "# User Input (Task):\n{input}")
TASK = "\n\n# Evaluation Task:\nAssess whether the agent's MCP-based execution accomplished the user's task."
TASK_CONVERSATION = "\n\n# Evaluation Task:\nAssess whether the MCP-based conversation accomplished the user's task."

V1 = PromptTemplate(
    metric_id="agent.mcp_task_completion",
    version="v1",
    system=SYSTEM_V1,
    parts={
        **section_parts(
            "simple", [INPUT, TOOLS, CALLS, (None, "# Agent Trajectory:\n{trajectory}")], TASK
        ),
        **section_parts(
            "conversation",
            [INPUT, TOOLS, CALLS, (None, "# Conversation:\n{mcp_conversation}")],
            TASK_CONVERSATION,
        ),
    },
    output_schema=five_tier_schema(
        {
            "task": {"type": "string"},
            "unmet_requirements": {"type": "array", "items": {"type": "string"}},
        }
    ),
    labels=FIVE_TIER,
    domain_rubric_template="# Domain-Specific Task Completion Criteria\n\nApply these additional criteria when assessing task completion:\n\n{domain_rubric}",
    source="bnbagent-eval:new (cf. deepeval:mcp_task_completion)",
)

VERSIONS = {"v1": V1}
