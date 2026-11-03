"""Behaviour of real models seen in the live judge sweep (``tests/live/sweep_judges.py``).

1. The agentic plan / inspect / verdict templates must declare their fields. With a bare
   ``{"type": "object"}`` schema, forced-tool providers (Bedrock Converse, Anthropic) reply
   ``{}``: the plan is empty and the loop stops at step 1 without reading any evidence.
2. Real models echo the whole criteria listing line (``"REQ-1: <text>"``) as the score name;
   ``derive`` must still match it to the criterion.
"""

from __future__ import annotations

import pytest

from bnbagent_eval.judges.base import JudgeRequest
from bnbagent_eval.judges.prompts.custom import agentic_inspect, agentic_plan, agentic_verdict
from bnbagent_eval.judges.providers._common import tool_reply
from bnbagent_eval.judges.providers.bedrock import BedrockJudge
from bnbagent_eval.metrics.vendors.agentic import derive
from bnbagent_eval.types import JudgeConfig


@pytest.mark.parametrize(
    ("template", "keys"),
    [
        (agentic_plan.V1, {"checks", "plan"}),
        (agentic_inspect.V1, {"action", "args", "scores"}),
        (agentic_verdict.V1, {"scores", "summary", "issues"}),
    ],
)
def test_agentic_schemas_declare_their_fields(template, keys) -> None:
    props = template.output_schema.get("properties") or {}
    assert keys <= set(props), sorted(props)


def test_derive_matches_names_that_echo_the_listing_line() -> None:
    checks = [
        {"id": "REQ-1", "text": "Mentions 21,000 gas.", "weight": None},
        {"id": "cost", "text": "Gives the cost.", "weight": 2},
    ]
    items = [
        {"name": "REQ-1: Mentions 21,000 gas.", "score": 1.0, "weight": 1.0},
        {"name": "cost", "score": 0.5, "weight": 1.0},
    ]
    rows, score = derive(items, checks)
    assert [r["assessed"] for r in rows] == [True, True]
    assert score == round((1.0 * 1 + 0.5 * 2) / 3, 3)


def test_derive_unmatched_name_still_counts_as_unassessed_zero() -> None:
    rows, score = derive(
        [{"name": "something else", "score": 1.0, "weight": 1.0}],
        [{"id": "REQ-1", "text": "t", "weight": None}],
    )
    assert rows[0]["assessed"] is False and score == 0.0


# 3. With auto tool choice, Claude Sonnet on Bedrock may call prompt *actions* as tools
#    (``read_output({})``, ``read_artifact({"name": ...})``) besides ``submit_verdict``. The
#    provider prefers the verdict tool, otherwise maps the first action tool call to an action.


def test_tool_reply_prefers_the_verdict_tool_then_maps_actions() -> None:
    verdict = {"score": "Good"}
    calls = [("read_output", {}), ("submit_verdict", verdict)]
    assert tool_reply(calls, "submit_verdict") == verdict
    calls = [("read_output", {}), ("read_artifact", {"name": "a.txt"})]
    assert tool_reply(calls, "submit_verdict") == {"action": "read_output", "args": {}}
    assert tool_reply([(None, verdict)], "submit_verdict") == verdict
    assert tool_reply([], "submit_verdict") is None


async def test_bedrock_maps_a_stray_action_tool_call() -> None:
    class Client:
        def converse(self, **kwargs):
            content = [
                {"toolUse": {"name": "read_artifact", "input": {"name": "answer.txt"}}},
                {"toolUse": {"name": "read_output", "input": {}}},
            ]
            return {"output": {"message": {"content": content}}, "usage": {}}

    judge = BedrockJudge(JudgeConfig(provider="bedrock", model="m"), client=Client())
    resp = await judge.complete(
        JudgeRequest(system="s", user="u", output_schema={"type": "object"})
    )
    assert resp.parsed == {"action": "read_artifact", "args": {"name": "answer.txt"}}
