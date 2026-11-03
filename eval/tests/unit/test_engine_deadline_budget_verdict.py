"""Engine: deadline and budget through the Runner, verdict truth-table rows, the stage action
table and hypothesis properties of verdict precedence."""

from __future__ import annotations

import asyncio
import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from bnbagent_eval import EvalClient, EvalRequest, MetricConfig, Suite, register_python_metric
from bnbagent_eval.engine import Runner
from bnbagent_eval.engine.aggregate import STAGE_ACTIONS, aggregate, build_case, decide
from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.types import Budget, ErrorInfo, EvalCase, MetricResult, PassRule, RunOptions

REPLY = {"reasoning": "reads well", "score": "Good"}


def _suite(*, required: bool = False) -> Suite:
    return Suite(
        name="e",
        version="1",
        metrics=(
            MetricConfig(id="text.exact_match"),
            MetricConfig(id="quality.fluency", required=required),
        ),
    )


def _cases(n: int) -> tuple[EvalCase, ...]:
    return tuple(EvalCase(id=str(i), output="a", expected="a") for i in range(n))


# ------------------------------------------------------------------ deadline (frozen clock)
async def test_deadline_passed_skips_metrics_and_is_inconclusive() -> None:
    judge = FakeJudge(REPLY)
    runner = Runner(judge=judge, clock=lambda: 2_000.0)
    resp = await runner.run(
        EvalRequest(
            suite=_suite(required=True), cases=_cases(2), options=RunOptions(deadline=1_000)
        )
    )
    metrics = [m for c in resp.results for m in c.metrics]
    assert {(m.status, m.error.code) for m in metrics} == {("skipped", "deadline")}
    assert judge.requests == []  # nothing was spent after the deadline
    assert resp.verdict.status == "inconclusive"
    assert "stopped early: deadline" in resp.verdict.reasons


async def test_deadline_in_the_future_scores_normally() -> None:
    runner = Runner(judge=FakeJudge(REPLY), clock=lambda: 500.0)
    resp = await runner.run(
        EvalRequest(suite=_suite(), cases=_cases(1), options=RunOptions(deadline=1_000))
    )
    assert all(m.status == "scored" for m in resp.results[0].metrics)


# ------------------------------------------------------------------ budget through the Runner
async def test_budget_exhaustion_skips_the_rest_and_reports_cost() -> None:
    judge = FakeJudge(REPLY)
    opts = RunOptions(budget=Budget(max_judge_calls=2), max_concurrency=5)
    resp = await EvalClient(judge=judge).evaluate(
        EvalRequest(suite=_suite(), cases=_cases(5), options=opts)
    )
    fluency = [m for c in resp.results for m in c.metrics if m.metric_id == "quality.fluency"]
    assert sum(m.status == "scored" for m in fluency) == 2
    assert [m.error.code for m in fluency if m.status == "skipped"] == ["budget_exhausted"] * 3
    assert len(judge.requests) == 2 and resp.cost.judge_calls == 2
    rule = [m for c in resp.results for m in c.metrics if m.metric_id == "text.exact_match"]
    assert all(m.score == 1.0 for m in rule)  # rule metrics are not budgeted
    assert resp.verdict.status == "inconclusive"
    assert "stopped early: budget_exhausted" in resp.verdict.reasons


# ------------------------------------------------------------------ concurrency
_state = {"active": 0, "peak": 0}


async def _jitter(case: EvalCase) -> float:
    _state["active"] += 1
    _state["peak"] = max(_state["peak"], _state["active"])
    await asyncio.sleep(random.Random(case.id).random() / 50)
    _state["active"] -= 1
    return 1.0


register_python_metric("deadline_jitter", _jitter)


async def test_concurrency_limit_reached_and_order_deterministic_under_random_delays() -> None:
    suite = Suite(
        name="j",
        version="1",
        metrics=(MetricConfig(id="custom.python", params={"fn": "deadline_jitter"}),),
    )
    cases = tuple(EvalCase(id=f"c{i}") for i in range(20))
    hashes = set()
    for _ in range(2):
        _state.update(active=0, peak=0)
        resp = await Runner().run(
            EvalRequest(suite=suite, cases=cases, options=RunOptions(max_concurrency=4))
        )
        assert _state["peak"] == 4  # the limit is used, and never exceeded
        assert [c.case_id for c in resp.results] == [c.id for c in cases]
        hashes.add(resp.result_hash)
    assert len(hashes) == 1


# ------------------------------------------------------------------ truth table rows
def mr(name="m", score=1.0, passed=True, required=False, status="scored", error=None):
    return MetricResult(
        metric_id="custom.python",
        name=name,
        method="rule",
        score=score,
        passed=passed,
        required=required,
        status=status,
        error=error,
    )


LENIENT = PassRule(min_aggregate=0.5, min_pass_rate=0.0, max_error_rate=1.0)


def verdict(results, rule=LENIENT, stage="dev"):
    cases = [build_case("1", results, rule)]
    return decide(aggregate(cases), cases, rule, stage)


def test_required_skipped_is_inconclusive() -> None:
    skipped = mr("r", None, None, True, "skipped", ErrorInfo("missing_input"))
    v = verdict([mr(score=0.9), skipped])
    assert v.status == "inconclusive" and "required metric not scored: r" in v.reasons


def test_budget_exhausted_optional_metric_is_inconclusive() -> None:
    out = mr("o", None, None, False, "skipped", ErrorInfo("budget_exhausted"))
    assert verdict([mr(score=0.9), out]).status == "inconclusive"


def test_error_rate_counts_errored_and_skipped() -> None:
    results = [
        mr("a", 0.9),
        mr("b", None, None, False, "error", ErrorInfo("metric_error")),
        mr("c", None, None, False, "skipped", ErrorInfo("missing_input")),
        mr("d", 0.9),
    ]
    assert aggregate([build_case("1", results, LENIENT)]).error_rate == 0.5
    v = verdict(results, PassRule(min_aggregate=0.5, min_pass_rate=0.0, max_error_rate=0.4))
    assert v.status == "inconclusive" and any("error rate 0.500" in r for r in v.reasons)


def test_precedence_required_fail_beats_error_and_pending() -> None:
    results = [
        mr("f", 0.1, False, True),
        mr("e", None, None, True, "error", ErrorInfo("metric_error")),
        mr("h", None, None, False, "pending_human"),
    ]
    assert verdict(results).status == "fail"
    assert verdict(results[1:]).status == "inconclusive"  # unscored required beats pending
    assert verdict([mr(score=0.9), results[2]]).status == "pending_human"


def test_stage_action_table_is_complete() -> None:
    statuses = {"pass", "fail", "inconclusive", "pending_human"}
    stages = {"dev", "registration", "pre_submit", "review", "vote"}
    assert set(STAGE_ACTIONS) == stages
    for stage, table in STAGE_ACTIONS.items():
        assert set(table) == statuses, stage
    assert all(a is None for a in STAGE_ACTIONS["dev"].values())
    for status in statuses:  # registration publishes only a pass
        want = "publishable" if status == "pass" else "not_publishable"
        assert STAGE_ACTIONS["registration"][status] == want


# ------------------------------------------------------------------ properties (hypothesis)
scores = st.floats(min_value=0, max_value=1, allow_nan=False)
optional = st.builds(lambda i, s: mr(f"o{i}", s, s >= 0.5), st.integers(0, 5), scores)


@given(st.lists(optional, min_size=0, max_size=6), st.sampled_from(sorted(STAGE_ACTIONS)))
def test_adding_a_failing_required_metric_always_fails(results, stage) -> None:
    failing = mr("req", 0.0, False, True)
    assert verdict([*results, failing], stage=stage).status == "fail"


@given(st.lists(optional, min_size=1, max_size=6), scores)
def test_resolving_an_errored_required_metric_passes_only_if_its_score_passes(results, s):
    errored = mr("req", None, None, True, "error", ErrorInfo("metric_error"))
    before = verdict([*results, errored])
    assert before.status == "inconclusive"
    resolved = mr("req", s, s >= 0.5, True)
    after = verdict([*results, resolved])
    if not resolved.passed:
        assert after.status != "pass"


@pytest.mark.parametrize("stage", sorted(STAGE_ACTIONS))
def test_every_stage_has_an_action_for_every_status(stage) -> None:
    for results in (
        [mr(score=0.9)],
        [mr(score=0.1, passed=False)],
        [mr("r", None, None, True, "skipped", ErrorInfo("deadline"))],
        [mr(score=0.9), mr("h", None, None, False, "pending_human")],
    ):
        v = verdict(results, stage=stage)
        if stage != "pre_submit":
            assert v.action == STAGE_ACTIONS[stage][v.status]
