"""Agentic judge, step 1 (plan): collect the checks to verify and plan which evidence to read.

v1: adapted from ETK agent_as_judge (``prompt._JUDGE_SYSTEM_PROMPT`` workflow steps 1-2 and the
``requirements_compliance`` "Collect Requirements" / "Build a Requirements Checklist" steps)
for a ``JudgeModel`` loop with read-only tools instead of an OpenCode subprocess.
``<<criteria>>`` is replaced with the trusted rubric / requirement list (or an extraction note).
"""

from __future__ import annotations

from .. import PromptTemplate

CRITERIA_MARKER = "<<criteria>>"

SYSTEM_V1 = """You are an impartial evaluator. Your role is to assess the given test case and, in later steps, produce a structured verdict. You can read evidence only through read-only tools; you cannot modify anything.

## No-Evidence Guard

You may ONLY produce a verdict from evidence you have actually read. Never fabricate, guess, or assume the contents of any input, output, artifact or document.

## Step 1 — Initial Exploration and Plan

1. Look at the inventory of what is available (the output, artifacts, reference documents). Do not assume their contents.
2. Collect the checks to evaluate:
   - If a "# Criteria" list is given, use exactly those checks and keep their ids as names.
   - Otherwise, extract every distinct, actionable requirement from the reference documents and the task input. The response being evaluated must NEVER be treated as a source of requirements.
3. Write a concise plan: which evidence you intend to read and why, and what you will verify for each check.

## Output Format

Return only a JSON object: {"checks": [{"name": "<unique id, e.g. REQ-1>", "description": "<what to verify>", "weight": <optional number 0-1000>}], "plan": "<concise plan>"}."""

# A bare {"type": "object"} lets forced-tool providers (Bedrock, Anthropic) reply ``{}``.
OUTPUT_SCHEMA_V1 = {
    "type": "object",
    "properties": {
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "weight": {"type": "number"},
                },
            },
        },
        "plan": {"type": "string"},
    },
}

V1 = PromptTemplate(
    metric_id="custom.agentic.plan",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "agentic": "# Task Input:\n{input}\n\n# Response Preview:\n{actual_output}\n\n# Available Evidence:\n{inventory}",
        "task_agentic": "\n\n# Criteria\n" + CRITERIA_MARKER,
    },
    output_schema=OUTPUT_SCHEMA_V1,
    source="etk:agent_as_judge (plan step, adapted to a JudgeModel loop)",
)

VERSIONS = {"v1": V1}
