"""Eval maker stage 4 (generate custom): custom metric configs for uncovered dimensions.

v1: AgentSight eval_maker ``GENERATION_PROMPT`` (one evaluator per dimension group, previous
feedback, the standard placeholder contract, a production-ready config), retargeted from
free-form evaluator classes to the three bnbagent-eval custom metrics (``custom.rubric``,
``custom.requirements_compliance``, ``custom.judge_prompt``) and their param schemas, with the
lint rule for criteria and the stage paragraph (``bnbagent-eval:new``).
"""

from __future__ import annotations

from .. import PromptTemplate

SYSTEM_V1 = """You generate custom metric configurations for evaluation dimensions that no catalogue metric covers.

Choose one of these custom metrics per evaluator:

1. custom.rubric: a weighted list of yes / no properties the response must have (score = weighted share satisfied).
   params: {"rubrics": [{"id": "R1", "text": "<one checkable property>", "weight": 1}]}
2. custom.requirements_compliance: per-requirement compliance scores 0-1 (score = weighted mean).
   params: {"requirements": [{"id": "Q1", "text": "<one requirement>", "weight": 1}]}
3. custom.judge_prompt: your own judge prompt, scored on a scale.
   params: {"prompt": "<instructions>", "category": "general" | "rag" | "conversational" | "comparison", "scale": "five_tier" | "pass_fail_na" | "numeric"}

# CRITICAL: Standard Placeholder Contract (custom.judge_prompt only)
Use ONLY these placeholders in the prompt, each wrapped in curly braces: {input}, {output}, {expected}, {contexts}, {conversation}, {trajectory}.
- category rag requires {contexts}; conversational requires {conversation}; comparison requires {expected} and {output}.
- Do not use any other curly braces in the prompt.

# Instructions:
1. Create ONE metric per dimension group listed (one dimension each unless told otherwise), with a short snake_case name
2. Prefer custom.rubric for lists of concrete properties, custom.requirements_compliance for graded requirements, custom.judge_prompt for a holistic judgement
3. Every rubric or requirement text must be specific and checkable: at least 15 characters, naming the observable property; never use vague words such as good, nice, appropriate, reasonable, proper, decent or ok
4. threshold is the pass score in [0, 1]; required is true for metrics that must pass
5. Incorporate every feedback item

Generate complete, production-ready metric configurations.

## Output Format

Return only a JSON object:
{"metrics": [{"name": "...", "metric_id": "custom.rubric" | "custom.requirements_compliance" | "custom.judge_prompt", "dimensions": ["..."], "params": {...}, "threshold": 0.7, "required": true, "rationale": "..."}]}"""

CUSTOM_METRIC_IDS = ("custom.rubric", "custom.requirements_compliance", "custom.judge_prompt")

V1 = PromptTemplate(
    metric_id="authoring.eval_maker_generate",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "simple": (
            "# Requirements:\n{description}\n\n# Domain and risks:\n{assessment}\n\n"
            "# Dimensions to evaluate:\n{dimensions}\n\n# Stage:\n{stage_guidance}\n\n"
            "# Previous feedback to incorporate (if any):\n{feedback}"
        )
    },
    output_schema={
        "type": "object",
        "required": ["metrics"],
        "properties": {
            "metrics": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name", "metric_id", "params"],
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "metric_id": {"enum": list(CUSTOM_METRIC_IDS)},
                        "dimensions": {"type": "array", "items": {"type": "string"}},
                        "params": {"type": "object"},
                        "threshold": {"type": ["number", "null"]},
                        "required": {"type": ["boolean", "null"]},
                        "rationale": {"type": "string"},
                    },
                },
            }
        },
    },
    source="agentsight:eval_maker.generate_custom (retargeted to custom.* metrics)",
)

VERSIONS = {"v1": V1}
