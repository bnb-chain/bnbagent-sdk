from __future__ import annotations

import statistics

import pytest
from hypothesis import given
from hypothesis import strategies as st

from bnbagent_eval.engine.aggregate import (
    Welford,
    aggregate,
    build_case,
    combine,
    decide,
    percentile,
)
from bnbagent_eval.types import ErrorInfo, MetricResult, PassRule


def mr(
    name="m",
    score=1.0,
    passed=True,
    required=False,
    status="scored",
    weight=1.0,
    error=None,
    value=None,
) -> MetricResult:
    return MetricResult(
        metric_id="custom.python",
        name=name,
        method="rule",
        score=score,
        passed=passed,
        required=required,
        status=status,
        weight=weight,
        error=error,
        value=value,
    )


@given(st.lists(st.floats(min_value=0, max_value=1), min_size=1, max_size=50))
def test_welford_matches_statistics(xs) -> None:
    w = Welford()
    for x in xs:
        w.add(x)
    assert w.mean == pytest.approx(statistics.fmean(xs), abs=1e-9)
    assert w.std == pytest.approx(statistics.pstdev(xs), abs=1e-9)
    assert (w.min, w.max) == (min(xs), max(xs))


def test_percentile() -> None:
    assert percentile([], 50) is None
    assert percentile([5], 95) == 5
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile(list(range(101)), 95) == 95


def test_combine_modes() -> None:
    rs = [
        mr(score=1.0, weight=3),
        mr(name="b", score=0.0, weight=1),
        mr(name="c", score=None, status="error"),
    ]
    assert combine(rs, "weighted_average") == 0.75
    assert combine(rs, "mean") == 0.5
    assert combine(rs, "min") == 0.0
    assert combine(rs, "max") == 1.0
    assert combine([mr(score=None, status="skipped")], "mean") is None


@given(st.lists(st.floats(min_value=0, max_value=1), min_size=1, max_size=20))
def test_aggregate_in_range_and_order_independent(scores) -> None:
    rule = PassRule()
    cases = [
        build_case(str(i), [mr(score=s, passed=s >= 0.5)], rule) for i, s in enumerate(scores)
    ]
    a, b = aggregate(cases), aggregate(list(reversed(cases)))
    assert 0.0 <= a.score <= 1.0
    assert a.score == pytest.approx(b.score) and a.pass_rate == pytest.approx(b.pass_rate)


def verdict(results, rule=None, stage="dev"):
    rule = rule or PassRule(min_aggregate=0.5, min_pass_rate=0.0)
    cases = [build_case("1", results, rule)]
    return decide(aggregate(cases), cases, rule, stage)


def test_verdict_truth_table() -> None:
    ok = mr(score=0.9)
    assert verdict([ok]).status == "pass"
    # 1. a failing required metric fails, whatever the aggregate
    assert verdict([ok, mr(name="r", score=0.4, passed=False, required=True)]).status == "fail"
    # 2. required metric errored / skipped -> inconclusive
    err = mr(
        name="r",
        score=None,
        passed=None,
        required=True,
        status="error",
        error=ErrorInfo("metric_error"),
    )
    assert (
        verdict(
            [ok, err], PassRule(min_aggregate=0.5, min_pass_rate=0.0, max_error_rate=1.0)
        ).status
        == "inconclusive"
    )
    # 2b. error rate above max -> inconclusive
    opt_err = mr(
        name="o", score=None, passed=None, status="error", error=ErrorInfo("metric_error")
    )
    assert verdict([ok, opt_err]).status == "inconclusive"
    assert (
        verdict(
            [ok, opt_err], PassRule(min_aggregate=0.5, min_pass_rate=0.0, max_error_rate=0.5)
        ).status
        == "pass"
    )
    # 2c. deadline / budget exhaustion -> inconclusive
    late = mr(name="o", score=None, passed=None, status="skipped", error=ErrorInfo("deadline"))
    assert (
        verdict(
            [ok, late], PassRule(min_aggregate=0.5, min_pass_rate=0.0, max_error_rate=1.0)
        ).status
        == "inconclusive"
    )
    # 3. pending human review
    pend = mr(name="h", score=None, passed=None, status="pending_human")
    assert verdict([ok, pend]).status == "pending_human"
    # 4. within the inconclusive band
    assert (
        verdict(
            [mr(score=0.52)],
            PassRule(min_aggregate=0.5, min_pass_rate=0.0, inconclusive_band=0.05),
        ).status
        == "inconclusive"
    )
    # 5. aggregate / pass rate
    assert verdict([mr(score=0.3, passed=False)]).status == "fail"
    assert (
        verdict([mr(score=0.9)], PassRule(min_aggregate=0.5, min_pass_rate=1.0)).status == "pass"
    )
    assert verdict([mr(score=None, passed=None)]).status == "inconclusive"


@pytest.mark.parametrize(
    ("stage", "status", "action"),
    [
        ("dev", "pass", None),
        ("registration", "pass", "publishable"),
        ("registration", "fail", "not_publishable"),
        ("pre_submit", "pass", "submit"),
        ("pre_submit", "fail", "retry_or_decline"),
        ("review", "pass", "no_action"),
        ("review", "fail", "dispute"),
        ("vote", "pass", "abstain"),
        ("vote", "fail", "vote_reject"),
    ],
)
def test_stage_actions(stage, status, action) -> None:
    score = 0.9 if status == "pass" else 0.1
    assert verdict([mr(score=score, passed=status == "pass")], stage=stage).action == action


@pytest.mark.parametrize(
    ("policy", "action"),
    [("retry", "retry_or_decline"), ("decline", "decline"), ("submit", "submit")],
)
def test_pre_submit_inconclusive_policy(policy, action) -> None:
    rule = PassRule(
        min_aggregate=0.5, min_pass_rate=0.0, inconclusive_band=0.1, on_inconclusive=policy
    )
    v = verdict([mr(score=0.55)], rule, stage="pre_submit")
    assert (v.status, v.action) == ("inconclusive", action)


def test_case_passed_requires_required_metrics() -> None:
    rule = PassRule(min_aggregate=0.5)
    assert build_case("1", [mr(score=0.9)], rule).passed is True
    assert (
        build_case(
            "1",
            [
                mr(score=0.9),
                mr(name="r", score=None, passed=None, required=True, status="skipped"),
            ],
            rule,
        ).passed
        is False
    )
    assert build_case("1", [mr(score=0.2, passed=False)], rule).passed is False
