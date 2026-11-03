"""Contract inputs for format.structure and the custom.* judge metrics."""

from __future__ import annotations

import json

from bnbagent_eval.types import EvalCase

from .catalog import ContractCase


def qa() -> EvalCase:
    return EvalCase(id="q", input="Write a haiku about BNB.", output='{"title": "BNB"}')


def requirements(score: float) -> str:
    return json.dumps(
        {
            "requirements": [
                {"id": "REQ-1", "description": "d", "score": score, "rationale": "r"},
                {"id": "REQ-2", "description": "d", "score": score, "rationale": "r"},
            ],
            "summary": "s",
            "issues": [],
        }
    )


def rubric(verdict: str) -> str:
    return json.dumps(
        {
            "evaluations": [
                {"rubric_id": "json", "property": "p", "rationale": "r", "verdict": verdict},
                {"rubric_id": "title", "property": "p", "rationale": "r", "verdict": verdict},
            ]
        }
    )


CONTRACT = {
    "format.structure": ContractCase(
        best=qa,
        worst=qa,
        params={"expected_structure": "A JSON object with a string field 'title'."},
        judge_best={"reasoning": "r", "score": "Excellent"},
        judge_worst={"reasoning": "r", "score": "Poor"},
    ),
    "custom.judge_prompt": ContractCase(
        best=qa,
        worst=qa,
        params={"prompt": "Is {output} a good answer to {input}?"},
        judge_best={"reasoning": "r", "score": "Excellent"},
        judge_worst={"reasoning": "r", "score": "Poor"},
    ),
    "custom.rubric": ContractCase(
        best=qa,
        worst=qa,
        params={
            "rubrics": [
                {"id": "json", "text": "The response is JSON."},
                {"id": "title", "text": "The response has a title.", "weight": 2},
            ]
        },
        judge_best=rubric("yes"),
        judge_worst=rubric("no"),
    ),
    "custom.requirements_compliance": ContractCase(
        best=qa,
        worst=qa,
        params={"requirements": ["Output is JSON.", "Output has a title."]},
        judge_best=requirements(1.0),
        judge_worst=requirements(0.0),
    ),
}
