"""Agentic judge, step 3 (verdict): the ETK ``write_verdict`` schema as a JSON reply.

v1: ETK agent_as_judge ``tools/verdict.write_verdict`` description and ``PartialScore`` rules
(unique names, score 0-1, weight 0-1000, default 1). ``<<criteria>>`` is replaced with the
checks from the plan step.
"""

from __future__ import annotations

from .. import PromptTemplate

SYSTEM_V1 = """You are an impartial evaluator. Write the evaluation verdict from the evidence you have gathered.

Do NOT compute an overall score yourself. Break the evaluation down into named metrics and report each one — the overall score is derived automatically as a weighted average of your partial scores, so the aggregate arithmetic is always exact.

You may ONLY score from evidence you have actually read; if a check could not be verified, score it 0 and say why in `issues`.

## Output Format

Return only a JSON object with:
- `scores`: non-empty list of per-metric partial scores. Each item is an object with:
    - `name` (string, required): a label for the metric (use the check names). Names MUST be unique across the list.
    - `score` (number, required): the metric's score, between 0.0 and 1.0.
    - `weight` (number, optional): relative importance, between 0 and 1000. Defaults to 1.0. Use 0 to record a metric without letting it affect the overall score.
- `summary`: non-empty string describing the evaluation result
- `issues`: list of strings describing specific problems found (empty list if none)"""

SCORES_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "score": {"type": "number"},
            "weight": {"type": "number"},
        },
        "required": ["name", "score"],
    },
}
# A bare {"type": "object"} lets forced-tool providers (Bedrock, Anthropic) reply ``{}``.
OUTPUT_SCHEMA_V1 = {
    "type": "object",
    "properties": {
        "scores": SCORES_SCHEMA,
        "summary": {"type": "string"},
        "issues": {"type": "array", "items": {"type": "string"}},
    },
}

V1 = PromptTemplate(
    metric_id="custom.agentic.verdict",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "agentic": "# Task Input:\n{input}\n\n# Plan:\n{plan}\n\n# Evidence Gathered:\n{observations}\n\n# Problems With The Previous Verdict:\n{problems}",
        "task_agentic": "\n\n# Checks\n<<criteria>>",
    },
    output_schema=OUTPUT_SCHEMA_V1,
    source="etk:agent_as_judge.write_verdict",
)

VERSIONS = {"v1": V1}
