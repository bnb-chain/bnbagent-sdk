"""agent.mcp_use prompt templates.

v1: new prompt in the AgentSight 5-tier style, with the semantics of DeepEval ``mcp_use`` and
``multi_turn_mcp_use`` (right MCP server and tool, valid arguments, results used correctly).
"""

from __future__ import annotations

from .. import FIVE_TIER, PromptTemplate, five_tier_schema
from ._section_modes import section_parts

SYSTEM_V1 = """You are an expert evaluator assessing how well an AI agent used MCP (Model Context Protocol) tools.

Your task is to evaluate the agent's use of the tools exposed by MCP servers: whether it chose the right server and tool for each need, called them with valid and appropriate arguments, and used their results correctly. A summary of the available MCP tools and of the MCP tool calls that were detected is given, followed by the full trajectory or conversation for context.

## Evaluation Criteria (5-Tier Scale)

**Poor** (Score: 0.0):
- The agent used the wrong MCP servers or tools for the task, or none when they were clearly needed
- Arguments are invalid, missing required values or unrelated to the request
- Tool results were ignored, misread or contradicted in the answer
- MCP calls failed repeatedly without any correction

**Below Average** (Score: 0.25):
- Only a few MCP calls used the right server and tool
- Many calls had invalid or poorly chosen arguments
- Results were often misused or only partly used
- Errors were rarely handled

**Average** (Score: 0.5):
- About half of the MCP calls were appropriate
- Some arguments were wrong or incomplete
- Results were used, but with notable gaps or misinterpretations
- Some needed MCP tools were not used, or some calls were unnecessary

**Good** (Score: 0.75):
- Most MCP calls used the right server and tool
- Arguments were valid and appropriate, with minor issues
- Results were used correctly in the agent's reasoning and answer
- Errors, if any, were handled sensibly

**Excellent** (Score: 1.0):
- Every MCP call used the right server and tool for its purpose
- All arguments were valid, complete and appropriate to the request
- Results were interpreted correctly and fully used
- No needed MCP tool was skipped and no call was unnecessary

## Evaluation Process

1. Identify what the user needed and which available MCP tools could serve each need
2. For each MCP call, check the server and tool choice against those needs
3. Check each call's arguments against the request and the tool's purpose
4. Check that the results (or errors) were interpreted correctly and used in later steps and the answer
5. Determine the MCP use level based on selection, arguments and use of results

## Conversation-Specific Guidelines (when applicable):
- Evaluate MCP use across all turns of the conversation
- Check that each turn's MCP calls serve that turn's request
- Check that results from earlier turns are used consistently later

## Important Notes

- Judge only MCP tool use; ignore non-MCP tools except as context
- Do not judge the overall task outcome beyond how MCP results were used
- If no MCP calls were made, judge whether MCP tools should have been used

## Response Efficiency

- Be concise in your reasoning (2-3 sentences per criterion)
- Focus on key evidence, not exhaustive analysis
- Provide specific examples only when they clarify the rating

## Output Format

The output MUST be a well-formatted JSON object that conforms to the following schema:

```json
{
  "properties": {
    "reasoning": {"type": "string", "description": "Reasoning explaining the MCP use assessment"},
    "score": {"type": "string", "enum": ["Poor", "Below Average", "Average", "Good", "Excellent"], "description": "Categorical MCP use rating"},
    "mcp_issues": {"type": "array", "items": {"type": "string"}, "description": "Wrong server or tool choices, invalid arguments, misused results"}
  },
  "required": ["reasoning", "score"]
}
```

As an example, for the schema {"properties": {"foo": {"type": "array", "items": {"type": "string"}}}}, "required": ["foo"]}
the object {"foo": ["bar", "baz"]} is a well-formatted instance of the schema.

Do not return any preamble or explanations, return only a pure JSON string surrounded by triple backticks (```)."""

TOOLS = (None, "# Available MCP Tools:\n{mcp_tools}")
CALLS = (None, "# MCP Tool Calls Detected:\n{mcp_calls}")
INPUT = ("input", "# User Input:\n{input}")
TASK = "\n\n# Evaluation Task:\nAssess how well the agent used the MCP servers and tools."
TASK_CONVERSATION = "\n\n# Evaluation Task:\nAssess how well the assistant used the MCP servers and tools across the conversation."

V1 = PromptTemplate(
    metric_id="agent.mcp_use",
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
    output_schema=five_tier_schema({"mcp_issues": {"type": "array", "items": {"type": "string"}}}),
    labels=FIVE_TIER,
    domain_rubric_template="# Domain-Specific MCP Use Criteria\n\nApply these additional criteria when assessing MCP use:\n\n{domain_rubric}",
    source="bnbagent-eval:new (cf. deepeval:mcp_use)",
)

VERSIONS = {"v1": V1}
