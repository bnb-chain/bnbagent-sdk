"""Shared pieces of the AgentSight whitebox prompt templates (rubric schema, step lists)."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from typing import Any

from .. import PromptTemplate

RUBRICS_FIELD = "{rubrics}"

# JsonAutoRaterResponseParser format: one entry per property (rubric text), verdict yes/no/nr.
RUBRIC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["evaluations"],
    "properties": {
        "evaluations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["property", "verdict"],
                "properties": {
                    "property": {"type": "string"},
                    "rationale": {"type": "string"},
                    # Free string, normalized like AgentSight _normalize_verdict (yes/pass/true,
                    # no/fail/false, nr/n/r/na/n/a, any case): the v1/v2 prompts ask for "n/r".
                    "verdict": {"type": "string"},
                },
            },
        }
    },
}


def steps_schema(*arrays: str) -> dict[str, Any]:
    """``analysis`` plus arrays of step indices (adaptivity / hallucination / efficiency)."""
    step_list = {"type": "array", "items": {"type": ["integer", "string"]}}
    return {
        "type": "object",
        "required": ["analysis", *arrays],
        "properties": {"analysis": {"type": "string"}, **{a: step_list for a in arrays}},
    }


def with_rubrics(template: PromptTemplate, texts: Sequence[str]) -> PromptTemplate:
    """Insert the (trusted) rubric texts as AgentSight does: ``"\\n*  ".join(texts)``."""
    listing = "\n*  ".join(texts).replace("{", "{{").replace("}", "}}")
    parts = {k: v.replace(RUBRICS_FIELD, listing) for k, v in template.parts.items()}
    return dataclasses.replace(template, parts=parts)
