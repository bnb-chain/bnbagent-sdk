"""Per-metric contract inputs: params, config, best/worst cases and runs.

Every registered metric must have an entry; the contract suite fails otherwise.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from bnbagent_eval import register_python_metric
from bnbagent_eval.types import AppMetrics, EvalCase, Session, ToolCall, ToolStep, Trace

register_python_metric("contract_echo", lambda case, args: float(case.output == "good"))


@dataclass(frozen=True)
class ContractCase:
    best: Callable[[], EvalCase]
    worst: Callable[[], EvalCase]
    params: dict[str, Any] = field(default_factory=dict)
    method_params: dict[str, dict[str, Any]] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)  # MetricConfig kwargs (value_max, ...)
    best_runs: Callable[[], tuple[EvalCase, ...]] | None = None
    worst_runs: Callable[[], tuple[EvalCase, ...]] | None = None
    best_min: float = 1.0  # documented maximum when a metric cannot reach exactly 1.0
    skip: str | None = None  # reason to skip the behavioural checks (e.g. a missing extra)
    # Judge methods: scripted FakeJudge replies (per method) for the best and worst case.
    judge_best: dict[str, Any] | Callable[..., Any] | None = None
    judge_worst: dict[str, Any] | Callable[..., Any] | None = None


# Scripted 5-tier replies for the native (LLM-mode) method of the format.* rule metrics.
FIVE_TIER_JUDGE: dict[str, Any] = {
    "judge_best": {"reasoning": "r", "score": "Excellent"},
    "judge_worst": {"reasoning": "r", "score": "Poor"},
}
XSD_A = '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="a"/></xs:schema>'


def pair(best_out: Any, worst_out: Any, expected: Any = None, **kw: Any) -> dict[str, Any]:
    return {
        "best": lambda: EvalCase(id="best", output=best_out, expected=expected, **kw),
        "worst": lambda: EvalCase(id="worst", output=worst_out, expected=expected, **kw),
    }


def ref_pair(text: str, disjoint: str) -> dict[str, Any]:
    return pair(text, disjoint, expected=text)


def traj(*names: str) -> Session:
    steps = tuple(
        ToolStep(id=str(i), call=ToolCall(name=n, arguments={"q": 1})) for i, n in enumerate(names)
    )
    return Session(id="s", traces=(Trace(id="t", steps=steps),))


def runs(*outputs: Any) -> Callable[[], tuple[EvalCase, ...]]:
    return lambda: tuple(EvalCase(id="r", output=o) for o in outputs)


def metrics_case(**app: Any) -> Callable[[], EvalCase]:
    return lambda: EvalCase(id="m", output="x", app_metrics=AppMetrics(**app))


SENTENCE = "the quick brown fox jumps over the lazy dog"
DISJOINT = "alpha beta gamma"

CONTRACT: dict[str, ContractCase] = {
    "custom.python": ContractCase(**pair("good", "bad"), params={"fn": "contract_echo"}),
    "text.exact_match": ContractCase(**pair("Paris", "Rome", expected="Paris")),
    "text.quasi_exact_match": ContractCase(**pair("The Paris!", "Rome", expected="paris")),
    "text.contains": ContractCase(**pair("It is Paris.", "Rome", expected="Paris")),
    "text.token_f1": ContractCase(**ref_pair(SENTENCE, DISJOINT)),
    "text.rouge": ContractCase(**ref_pair(SENTENCE, DISJOINT)),
    "text.bleu": ContractCase(**ref_pair(SENTENCE, DISJOINT)),
    "text.meteor": ContractCase(**ref_pair(SENTENCE, DISJOINT), best_min=0.99),
    "text.jaccard": ContractCase(**ref_pair(SENTENCE, DISJOINT)),
    "text.tfidf_cosine": ContractCase(**ref_pair(SENTENCE, DISJOINT), best_min=0.999999),
    "text.classification_accuracy": ContractCase(
        **pair("positive", "negative", expected="positive")
    ),
    "text.length": ContractCase(**pair("a b", "a b c d e"), params={"max_words": 3}),
    "text.embedding_similarity": ContractCase(**ref_pair("bnb chain agents", DISJOINT)),
    "format.json_valid": ContractCase(**pair('{"a": 1}', "not json")),
    "format.json_schema": ContractCase(
        **pair('{"a": 1}', '{"b": 1}'),
        params={"schema": {"type": "object", "required": ["a"]}},
        **FIVE_TIER_JUDGE,
    ),
    "format.regex": ContractCase(
        **pair("123", "abc"), params={"pattern": r"\d+"}, **FIVE_TIER_JUDGE
    ),
    "format.xml": ContractCase(
        **pair("<a><b/></a>", "<a>"),
        method_params={"native": {"xsd": XSD_A}},
        **FIVE_TIER_JUDGE,
    ),
    "ops.latency": ContractCase(
        best=metrics_case(latency_ms=10),
        worst=metrics_case(latency_ms=500),
        config={"value_max": 100},
    ),
    "ops.cost": ContractCase(
        best=metrics_case(cost_usd=0.01),
        worst=metrics_case(cost_usd=1.0),
        config={"value_max": 0.1},
    ),
    "ops.token_usage": ContractCase(
        best=metrics_case(input_tokens=10, output_tokens=5),
        worst=metrics_case(input_tokens=900, output_tokens=500),
        config={"value_max": 100},
    ),
    "ops.reliability": ContractCase(best=metrics_case(), worst=metrics_case(error="timeout")),
    "ops.consistency": ContractCase(
        best=lambda: EvalCase(id="c", output='{"a": 1}'),
        worst=lambda: EvalCase(id="c", output='{"a": 1}'),
        best_runs=runs('{"a": 1}', '{"a": 1}', '{"a": 1}'),
        worst_runs=runs('{"a": 1}', "x"),
        # judge method: equivalence verdict per run pair.
        judge_best={"reasoning": "same meaning", "score": "yes"},
        judge_worst={"reasoning": "different", "score": "no"},
    ),
    "agent.tool_trajectory_match": ContractCase(
        best=lambda: EvalCase(
            id="t", trajectory=traj("search"), expected=[{"name": "search", "arguments": {"q": 1}}]
        ),
        worst=lambda: EvalCase(
            id="t", trajectory=traj("search"), expected=[{"name": "browse", "arguments": {"q": 1}}]
        ),
    ),
    "agent.trajectory_stats": ContractCase(
        best=lambda: EvalCase(id="t", trajectory=traj("search")),
        worst=lambda: EvalCase(id="t", trajectory=traj("a", "b", "c")),
        params={"max_tool_calls": 1},
    ),
}


def _merge_category_catalogs() -> None:
    """Merge ``catalog_<category>.py`` files (one per metric category) into CONTRACT."""
    import importlib
    import pkgutil
    from pathlib import Path

    for info in pkgutil.iter_modules([str(Path(__file__).parent)]):
        if info.name.startswith("catalog_"):
            module = importlib.import_module(f"{__package__}.{info.name}")
            overlap = set(module.CONTRACT) & set(CONTRACT)
            if overlap:
                raise ValueError(f"duplicate contract entries: {sorted(overlap)}")
            CONTRACT.update(module.CONTRACT)


_merge_category_catalogs()
