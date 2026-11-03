"""Suite lint / lock, calibration statistics, console report, judge determinism."""

from __future__ import annotations

import pytest
import yaml

from bnbagent_eval import EvalCase, EvalClient, EvalRequest, MetricConfig, PassRule, Suite
from bnbagent_eval.analysis.calibration import agreement, cohen_kappa, spearman
from bnbagent_eval.authoring.lint import lint_suite, lock_suite
from bnbagent_eval.cli import main
from bnbagent_eval.engine import Runner, resolve_suite
from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.reporting import to_console
from bnbagent_eval.types import JudgeConfig

GOOD = {"reasoning": "r", "score": "Good"}


def codes(issues) -> set[str]:
    return {i.code for i in issues}


def test_lint_flags_weak_negotiated_suites() -> None:
    weak = Suite(name="w", version="1", metrics=(MetricConfig(id="quality.relevance"),))
    assert {
        "judge_not_pinned",
        "no_deterministic_metric",
        "no_required_metric",
        "unlocked",
        "few_samples",
    } <= codes(lint_suite(weak))
    assert not [i for i in lint_suite(weak, use="dev") if i.level == "error"]
    strong = lock_suite(
        Suite(
            name="s",
            version="1",
            judge=JudgeConfig(provider="openai", model="m", samples=3),
            pass_rule=PassRule(inconclusive_band=0.05),
            metrics=(
                MetricConfig(id="format.json_valid", required=True),
                MetricConfig(id="quality.relevance", threshold=0.75),
            ),
        )
    )
    assert lint_suite(strong) == []


def test_lint_service_and_errors() -> None:
    py = Suite(
        name="p", version="1", metrics=(MetricConfig(id="custom.python", params={"fn": "x"}),)
    )
    assert "not_service_allowed" in codes(lint_suite(py, use="service"))
    assert "unresolvable" in codes(
        lint_suite(Suite(name="x", version="1", metrics=(MetricConfig(id="nope.x"),)))
    )
    required = Suite(
        name="r", version="1", metrics=(MetricConfig(id="ops.latency", required=True),)
    )
    assert "required_without_threshold" in codes(lint_suite(required))


def test_lint_warns_on_ignored_value_limits() -> None:
    from bnbagent_eval.metrics.registry import list_specs

    value_metrics = {s.id for s in list_specs() if s.value_limits}
    assert value_metrics == {
        "ops.latency",
        "ops.cost",
        "ops.token_usage",
        "onchain.gas_efficiency",
    }
    suite = Suite(
        name="v",
        version="1",
        metrics=(
            MetricConfig(id="ops.latency", value_max=2000, value_target=500),
            MetricConfig(
                id="text.length", params={"max_words": 50}, value_max=100, required=True
            ),  # has a unit, no value
        ),
    )
    issues = [i for i in lint_suite(suite, use="dev") if i.code == "value_limits_ignored"]
    assert [(i.metric, i.level) for i in issues] == [("text.length", "warning")]
    assert "value_max" in issues[0].message
    # ignored limits do not count as a pass criterion for a required metric
    refusal = Suite(
        name="r",
        version="1",
        metrics=(MetricConfig(id="safety.refusal", value_min=0.5, required=True),),
    )
    assert {"value_limits_ignored", "required_without_threshold"} <= codes(lint_suite(refusal))


def test_lock_pins_prompt_hash() -> None:
    suite = Suite(name="s", version="1", metrics=(MetricConfig(id="quality.relevance"),))
    locked = lock_suite(suite)
    entry = locked.lock["quality.relevance"]
    assert entry.prompt_hash and entry.prompt_hash.startswith("0x")
    assert resolve_suite(locked).suite_hash == resolve_suite(suite).suite_hash


def test_cli_lint_and_lock(tmp_path, capsys) -> None:
    path = tmp_path / "s.yaml"
    path.write_text("name: s\nversion: '1'\nmetrics:\n  - {id: quality.relevance}\n")
    assert main(["lint", str(path)]) == 1
    assert "judge_not_pinned" in capsys.readouterr().out
    out = tmp_path / "locked.yaml"
    assert main(["lint", str(path), "--use", "dev", "--lock", str(out)]) == 0
    assert "quality.relevance" in yaml.safe_load(out.read_text())["lock"]


def test_calibration_statistics() -> None:
    ref = [1.0, 0.75, 0.5, 0.25, 0.0, 1.0]
    perfect = agreement(ref, ref)
    assert (
        perfect.mae,
        perfect.cohen_kappa,
        perfect.weighted_kappa,
        perfect.spearman,
        perfect.decision_agreement,
        perfect.mcc,
    ) == (0.0, 1.0, 1.0, 1.0, 1.0, 1.0)
    off_by_one = agreement([0.75, 0.5, 0.25, 0.0, 0.0, 0.75], ref)
    assert off_by_one.mae == pytest.approx(0.25 * 5 / 6)
    assert off_by_one.weighted_kappa > off_by_one.cohen_kappa  # near misses are partly credited
    assert cohen_kappa([0, 1, 0, 1], [0, 1, 1, 0], 2) == pytest.approx(0.0)
    assert spearman([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    with pytest.raises(ValueError):
        agreement([], [])


async def test_console_report() -> None:
    suite = Suite(name="t", version="1", metrics=(MetricConfig(id="quality.relevance"),))
    resp = await EvalClient(judge=FakeJudge(GOOD)).evaluate(
        suite=suite, cases=[EvalCase(id="c1", output="a")]
    )
    text = to_console(resp)
    assert "verdict: PASS" in text and "quality.relevance" in text and "c1" in text


async def test_judge_determinism_and_truncation_in_negotiated_stage() -> None:
    suite = Suite(
        name="t",
        version="1",
        metrics=(
            MetricConfig(id="quality.relevance", required=True, params={"max_field_chars": 300}),
        ),
    )
    cases = (EvalCase(id="1", input="q", output="x" * 1000),)
    hashes = {
        (
            await Runner(judge=FakeJudge(GOOD)).run(EvalRequest(suite=suite, cases=cases))
        ).result_hash
        for _ in range(5)
    }
    assert len(hashes) == 1  # same judge outputs -> same result_hash
    resp = await Runner(judge=FakeJudge(GOOD)).run(
        EvalRequest(stage="review", suite=suite, cases=cases)
    )
    m = resp.results[0].metrics[0]
    assert m.status == "skipped" and m.error.code == "truncated"
    assert resp.verdict.status == "inconclusive"
    allowed = Suite(
        name="t",
        version="1",
        metrics=(
            MetricConfig(
                id="quality.relevance",
                required=True,
                allow_truncation=True,
                params={"max_field_chars": 300},
            ),
        ),
    )
    resp = await Runner(judge=FakeJudge(GOOD)).run(
        EvalRequest(stage="review", suite=allowed, cases=cases)
    )
    assert resp.results[0].metrics[0].score == 0.75
