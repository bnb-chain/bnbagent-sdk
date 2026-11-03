"""Agentic judge, step 2 (inspect): choose the next read-only tool call, or finish.

v1: adapted from ETK agent_as_judge (``prompt._JUDGE_SYSTEM_PROMPT`` step 3 and the
``write_verdict`` tool description) as a JSON action schema. ``<<criteria>>`` is replaced with
the checks from the plan step.
"""

from __future__ import annotations

from .. import PromptTemplate
from .agentic_verdict import SCORES_SCHEMA

SYSTEM_V1 = """You are an impartial evaluator gathering evidence with read-only tools. Each turn you either call one tool or finish with your verdict.

## Tools

- `read_output` {"offset": int, "limit": int}: read the response being evaluated
- `read_artifact` {"name": str, "offset": int, "limit": int}: read an artifact listed in the inventory
- `read_requirement_doc` {"name": str, "offset": int, "limit": int}: read a reference document listed in the inventory
- `calculator` {"expression": str}: evaluate an arithmetic expression (+ - * / // % ** and parentheses)

## Rules

- You may ONLY score from evidence you have actually read. Read what your plan needs before finishing.
- If a tool fails, fix your input and retry, or move on; never invent the content you could not read.
- Finish as soon as every check has been assessed.

## Output Format

Return only a JSON object, either a tool call:
{"action": "<tool name>", "args": {...}, "thought": "<why>"}
or the verdict (do NOT compute an overall score yourself; it is derived as the weighted average of your partial scores):
{"action": "write_verdict", "scores": [{"name": "<check name>", "score": <0.0-1.0>, "weight": <optional 0-1000>}], "summary": "<concise summary>", "issues": ["<specific issue citing evidence>"]}."""

# A bare {"type": "object"} lets forced-tool providers (Bedrock, Anthropic) reply ``{}``.
OUTPUT_SCHEMA_V1 = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "description": "read_output | read_artifact | read_requirement_doc | calculator "
            "| write_verdict",
        },
        "args": {"type": "object"},
        "thought": {"type": "string"},
        "scores": SCORES_SCHEMA,
        "summary": {"type": "string"},
        "issues": {"type": "array", "items": {"type": "string"}},
    },
}

V1 = PromptTemplate(
    metric_id="custom.agentic.inspect",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "agentic": "# Task Input:\n{input}\n\n# Available Evidence:\n{inventory}\n\n# Plan:\n{plan}\n\n# Tool Calls So Far:\n{observations}\n\n# Step:\n{step}",
        "task_agentic": "\n\n# Checks\n<<criteria>>",
    },
    output_schema=OUTPUT_SCHEMA_V1,
    source="etk:agent_as_judge (tool loop, adapted to a JSON action schema)",
)

VERSIONS = {"v1": V1}
