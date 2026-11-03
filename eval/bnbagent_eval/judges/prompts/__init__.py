"""Versioned judge prompt templates.

A template renders one of three input modes (``simple``, ``with_expected``, ``conversation``,
as in AgentSight) plus an optional extra mode a metric defines. ``hash()`` covers every piece of
text and the label map, so any prompt change changes ``prompt_hash`` (and thus ``suite_hash`` and
``result_hash``). Old versions stay importable so locked suites keep working.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ...hashing import hash_obj

MAX_RUBRIC_LENGTH = 2000
_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|above|prior)\s+instructions",
    r"disregard\s+(all\s+)?(previous|above|prior)",
    r"forget\s+(everything|all)\s+(above|before)",
    r"new\s+instructions\s*:",
    r"system\s*:\s*you\s+are",
    r"<\s*system\s*>",
    r"</?\s*prompt\s*>",
    r"</?\s*untrusted",
]

FIVE_TIER = {"Poor": 0.0, "Below Average": 0.25, "Average": 0.5, "Good": 0.75, "Excellent": 1.0}


def validate_domain_rubric(rubric: str) -> None:
    """Reject over-long rubrics and rubrics that look like prompt injections (AgentSight rules)."""
    if len(rubric) > MAX_RUBRIC_LENGTH:
        raise ValueError(f"domain_rubric exceeds {MAX_RUBRIC_LENGTH} characters")
    lowered = rubric.lower()
    for pattern in _INJECTION_PATTERNS:
        if re.search(pattern, lowered):
            raise ValueError("domain_rubric contains prohibited instruction patterns")


@dataclass(frozen=True)
class PromptTemplate:
    metric_id: str
    version: str
    system: str
    parts: Mapping[str, str]  # "<mode>" body and "task_<mode>" task text per mode
    output_schema: Mapping[str, Any]
    labels: Mapping[str, float] | None = None  # value of the "score" field -> normalized score
    domain_rubric_template: str = "# Domain-Specific Criteria\n\n{domain_rubric}"
    source: str = ""  # provenance, e.g. "agentsight:relevance_v0"
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def modes(self) -> tuple[str, ...]:
        return tuple(k for k in self.parts if not k.startswith("task_"))

    def system_prompt(self, domain_rubric: str | None = None) -> str:
        # ``extra["system_suffix"]`` follows the domain rubric (AgentSight appends some addenda
        # to the already rubric-extended system prompt, e.g. Completeness' context call).
        suffix = str(self.extra.get("system_suffix", ""))
        if not domain_rubric:
            return self.system + suffix
        validate_domain_rubric(domain_rubric)
        rubric = self.domain_rubric_template.format(domain_rubric=domain_rubric)
        return f"{self.system}\n\n{rubric}{suffix}"

    def render(self, mode: str, fields: Mapping[str, str]) -> str:
        if mode not in self.parts:
            raise ValueError(f"template {self.metric_id}@{self.version} has no mode {mode!r}")
        return self.parts[mode].format(**fields) + self.parts.get(f"task_{mode}", "")

    def hash(self) -> str:
        return hash_obj(
            {
                "metric_id": self.metric_id,
                "version": self.version,
                "system": self.system,
                "parts": dict(self.parts),
                "output_schema": dict(self.output_schema),
                "labels": dict(self.labels) if self.labels else None,
                "domain_rubric_template": self.domain_rubric_template,
                "extra": dict(self.extra),
            }
        )


def five_tier_schema(extra_properties: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Output schema used by the AgentSight-style 5-tier judges."""
    props: dict[str, Any] = {
        "reasoning": {"type": "string"},
        "score": {"type": "string", "enum": list(FIVE_TIER)},
    }
    props.update(extra_properties or {})
    return {"type": "object", "required": ["reasoning", "score"], "properties": props}
