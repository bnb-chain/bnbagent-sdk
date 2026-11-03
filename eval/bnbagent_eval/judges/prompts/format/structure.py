"""format.structure prompt templates.

v1: ported verbatim from AgentSight ``output_structure_v0`` (system prompt, rubric template); the
user prompt follows ``_build_llm_prompt``: validation type ``description`` for ``format.structure``,
``json`` / ``regex`` / ``xml`` for the ``native`` methods of ``format.json_schema`` /
``format.regex`` / ``format.xml`` (AgentSight's LLM mode for those checks). The suite's
``expected_structure`` is trusted configuration and is substituted for ``SPEC_MARKER`` by
:func:`with_structure` (so ``prompt_hash`` covers it).
"""

from __future__ import annotations

import dataclasses

from .. import FIVE_TIER, PromptTemplate, five_tier_schema

SYSTEM_V1 = "You are an expert evaluator assessing output structure compliance.\nYour task is to evaluate how well an output conforms to an expected structure or format.\n\n# Evaluation Framework\n\nAssess the output against the provided structure specification:\n1. **Format Validity**: Is the output in the correct format (JSON, XML, etc.)?\n2. **Schema Compliance**: Does it match the expected schema/structure?\n3. **Required Fields**: Are all required fields present?\n4. **Field Types**: Are field values of the correct types?\n5. **Structural Integrity**: Is the overall structure well-formed?\n\n# Scoring Guidelines (5-Tier Scale)\n\n- **Poor** (0.0): Major structural issues, wrong format, or missing critical elements\n- **Below Average** (0.25): Significant structural problems, multiple required fields missing\n- **Average** (0.5): Some structural elements correct, but notable deviations\n- **Good** (0.75): Minor deviations, most structure correct, perhaps missing optional fields\n- **Excellent** (1.0): Perfect match to expected structure, all fields present and correct\n\nBe objective and precise in your assessment. Focus on structural compliance, not content quality."
DOMAIN_RUBRIC_V1 = "# Domain-Specific Structure Requirements\n\nApply these additional structure compliance considerations:\n\n{domain_rubric}"

SPEC_MARKER = "<<expected_structure>>"
_BODY = (
    "# Output to Evaluate:\n```\n{actual_output}\n```\n\n# Expected Structure Type: DESCRIPTION\n\n# Structure Specification:\n"
    + SPEC_MARKER
    + "\n\n# Task:\nEvaluate how well the output complies with the expected structure specification.\nConsider format validity, schema compliance, required fields, and structural integrity."
)

V1 = PromptTemplate(
    metric_id="format.structure",
    version="v1",
    system=SYSTEM_V1,
    parts={"simple": _BODY, "conversation": _BODY},
    output_schema=five_tier_schema(
        {
            "missing_elements": {"type": ["array", "null"], "items": {"type": "string"}},
            "extra_elements": {"type": ["array", "null"], "items": {"type": "string"}},
            "format_issues": {"type": ["array", "null"], "items": {"type": "string"}},
        }
    ),
    labels=FIVE_TIER,
    domain_rubric_template=DOMAIN_RUBRIC_V1,
    source="agentsight:output_structure_v0",
)

VERSIONS = {"v1": V1}


_TYPE_LINE = "# Expected Structure Type: DESCRIPTION"


def with_structure(
    template: PromptTemplate, expected_structure: str, validation_type: str = "description"
) -> PromptTemplate:
    """Insert the (trusted) structure description into every mode of ``template``.

    ``validation_type`` is AgentSight's ``validation_type`` (``json`` / ``regex`` / ``xml`` /
    ``description``), rendered upper-case as in ``_build_llm_prompt``.
    """
    spec = expected_structure.replace("{", "{{").replace("}", "}}")
    type_line = f"# Expected Structure Type: {validation_type.upper()}"
    parts = {
        k: v.replace(SPEC_MARKER, spec).replace(_TYPE_LINE, type_line)
        for k, v in template.parts.items()
    }
    return dataclasses.replace(template, parts=parts)


def schema_description(validation_type: str, spec: str) -> str:
    """AgentSight ``_get_schema_description`` for the json / regex / xml validation types."""
    if validation_type == "json":
        return f"JSON Schema:\n```json\n{spec}\n```"
    if validation_type == "regex":
        return f"Regex Pattern: `{spec}`"
    if validation_type == "xml":
        return f"XML Schema:\n```xml\n{spec}\n```"
    return spec
