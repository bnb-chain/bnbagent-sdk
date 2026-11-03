from __future__ import annotations

import json

import pytest

from bnbagent_eval.engine.budget import BudgetTracker
from bnbagent_eval.engine.dataset import load_cases, parse_cases
from bnbagent_eval.exceptions import RequestError
from bnbagent_eval.metrics.text._common import as_text, references
from bnbagent_eval.types import Budget


def test_budget_reservations_are_atomic_and_bounded() -> None:
    b = BudgetTracker(Budget(max_judge_calls=3, max_tokens=100, max_usd=0.5))
    assert b.reserve(judge_calls=2, tokens=40, usd=0.2)
    assert not b.reserve(judge_calls=2)  # would exceed calls: nothing is reserved
    assert not b.reserve(tokens=61)
    assert not b.reserve(usd=0.31)
    assert b.reserve(judge_calls=1, tokens=60, usd=0.3)
    cost = b.cost()
    assert (cost.judge_calls, cost.tokens, cost.usd_estimate) == (3, 100, 0.5)
    assert BudgetTracker().reserve(judge_calls=10**6)


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("c.json", '[{"id": "1"}, {"id": "2"}]'),
        ("c.json", '{"cases": [{"id": "1"}, {"id": "2"}]}'),
        ("c.jsonl", '{"id": "1"}\n\n{"id": "2"}\n'),
        ("c.yaml", "cases:\n  - id: '1'\n  - id: '2'\n"),
    ],
)
def test_load_cases_formats(tmp_path, name, text) -> None:
    path = tmp_path / name
    path.write_text(text)
    assert [c.id for c in load_cases(path)] == ["1", "2"]


def test_load_cases_errors(tmp_path) -> None:
    with pytest.raises(RequestError, match="cannot read"):
        load_cases(tmp_path / "missing.json")
    with pytest.raises(RequestError, match="expected a list"):
        parse_cases({"not": "cases"})
    with pytest.raises(RequestError, match="unknown field"):
        parse_cases([{"id": "1", "bogus": True}])


def test_text_helpers() -> None:
    assert as_text({"b": 1, "a": 2}) == json.dumps({"a": 2, "b": 1})
    assert as_text(None) == "" and as_text(3) == "3"
    assert references({"answers": ["x", "y"]}) == ["x", "y"]
    assert references("a <OR> b") == ["a", "b"]
    assert references(["only"]) == ["only"]
