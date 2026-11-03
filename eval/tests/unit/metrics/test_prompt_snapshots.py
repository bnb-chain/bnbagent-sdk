"""Prompt snapshots, ``domain_rubric`` and prompt-injection fixtures.

Prompt snapshots: every :class:`PromptTemplate` under ``bnbagent_eval.judges.prompts`` (metric,
agentic, analysis, generation and authoring prompts) is pinned by ``module:attr[@version]`` to its
hash in ``tests/fixtures/prompt_hashes.json``. Editing a template without bumping its version
fails; bumping the version adds a new key, which must be pinned explicitly:

    # re-pin, from the package directory (eval/):
    python -c "from tests.unit.metrics.test_prompt_snapshots import _pin; _pin()"
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Mapping
from pathlib import Path

import pytest

from bnbagent_eval.judges.prompts import PromptTemplate
from bnbagent_eval.metrics import registry
from bnbagent_eval.metrics.judge_base import JudgeMetric

FIXTURES = Path(__file__).parents[2] / "fixtures"
SNAPSHOT = FIXTURES / "prompt_hashes.json"
PAYLOADS = json.loads((FIXTURES / "injection" / "payloads.json").read_text(encoding="utf-8"))[
    "payloads"
]
GUESSED_NONCE = "0123456789abcdef"


def current_hashes() -> dict[str, str]:
    import importlib
    import pkgutil

    import bnbagent_eval.judges.prompts as prompts

    out: dict[str, str] = {}
    for info in pkgutil.walk_packages(prompts.__path__, prompts.__name__ + "."):
        module = importlib.import_module(info.name)
        short = info.name.removeprefix(prompts.__name__ + ".")
        for attr, value in vars(module).items():
            if isinstance(value, PromptTemplate):
                out[f"{short}:{attr}@{value.version}"] = value.hash()
            elif isinstance(value, Mapping):
                for t in value.values():
                    if isinstance(t, PromptTemplate):
                        out[f"{short}:{attr}@{t.version}"] = t.hash()
    return dict(sorted(out.items()))


# ------------------------------------------------------------------------ prompt snapshots


def test_snapshot_covers_every_template() -> None:
    pinned = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    current = current_hashes()
    assert len(current) >= 80, "template discovery found too few templates"
    assert sorted(current) == sorted(pinned), (
        "templates added or removed; re-pin with _pin() (see the module docstring)"
    )


@pytest.mark.parametrize("key", sorted(current_hashes()))
def test_template_text_changes_only_with_its_version(key: str) -> None:
    pinned = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert current_hashes()[key] == pinned.get(key), (
        f"{key} changed without a new prompt version: bump the template version (old results "
        "must stay reproducible) and re-pin the snapshot"
    )


def test_template_version_field_matches_its_key() -> None:
    for spec in registry.list_specs():
        templates = getattr(registry.resolve(spec.id)[0], "templates", None)
        if isinstance(templates, Mapping):
            for version, template in templates.items():
                if isinstance(template, PromptTemplate):
                    assert template.version == version, (spec.id, version, template.version)


# --------------------------------------------------------------------------- domain_rubric

from ...contract.catalog import CONTRACT  # noqa: E402
from ...contract.test_metric_contract import JUDGE_PAIRS, _best, _metric  # noqa: E402

NATIVE = [(s, m) for s, m in JUDGE_PAIRS if issubclass(registry.resolve(s.id)[0], JudgeMetric)]
NATIVE_IDS = [f"{s.id}:{m.name}" for s, m in NATIVE]
RUBRIC = "Answers must cite the BEP number they rely on."


def _accepts(spec, name: str) -> bool:
    props = spec.params_schema.get("properties", {})
    return name in props or spec.params_schema.get("additionalProperties", True) is not False


@pytest.mark.parametrize(("spec", "method"), NATIVE, ids=NATIVE_IDS)
async def test_domain_rubric_reaches_every_judge_call(spec, method) -> None:
    if not _accepts(spec, "domain_rubric"):
        pytest.skip("metric does not take domain_rubric")
    case, ctx = _best(CONTRACT[spec.id], spec, method)
    plain = ctx.judge
    await _metric(spec).evaluate(case, ctx)
    case, ctx = _best(CONTRACT[spec.id], spec, method)
    ctx.params = {**ctx.params, "domain_rubric": RUBRIC}
    res = await _metric(spec).evaluate(case, ctx)
    assert res.status == "scored", res
    assert ctx.judge.requests and all(RUBRIC in r.system for r in ctx.judge.requests)
    assert all(RUBRIC not in r.system for r in plain.requests)


# ----------------------------------------------------------------------- injection fixtures


def _inject(case, payload: str):
    fields = {}
    for name in ("output", "input"):
        if isinstance(getattr(case, name), str):
            fields[name] = f"{getattr(case, name)}\n{payload}"
    return dataclasses.replace(case, **fields), fields


# Only methods whose contract case has a free-text input / output to inject into.
_INJECTABLE = [(s, m) for s, m in JUDGE_PAIRS if _inject(CONTRACT[s.id].worst(), "x")[1]]


@pytest.mark.parametrize("payload_id", sorted(PAYLOADS))
@pytest.mark.parametrize(
    ("spec", "method"), _INJECTABLE, ids=[f"{s.id}:{m.name}" for s, m in _INJECTABLE]
)
async def test_injection_payload_stays_fenced_and_cannot_set_the_score(
    spec, method, payload_id: str
) -> None:
    cc = CONTRACT[spec.id]
    payload = PAYLOADS[payload_id].replace("{nonce}", GUESSED_NONCE)
    _, ctx = _best(cc, spec, method)  # same skips as the contract suite
    case, injected = _inject(cc.worst(), payload)
    if not injected:
        pytest.skip("no free-text field to inject into")
    from bnbagent_eval.judges.providers.fake import FakeJudge

    ctx.judge = FakeJudge(cc.judge_worst)
    worst_case = cc.worst()
    _, wctx = _best(cc, spec, method)
    wctx.judge = FakeJudge(cc.judge_worst)
    baseline = await _metric(spec).evaluate(worst_case, wctx)
    res = await _metric(spec).evaluate(case, ctx)
    # Structure: the payload is only ever rendered inside a fence, with its tags defused.
    for req in ctx.judge.requests:
        nonce = req.system.split("<untrusted-")[1].split(">")[0]
        assert nonce != GUESSED_NONCE
        fence = re.compile(
            rf'<untrusted-{nonce} field="[a-z_]+">\n(.*?)\n</untrusted-{nonce}>', re.S
        )
        outside = fence.sub("", req.user)
        assert payload.splitlines()[0] not in outside
        for body in fence.findall(req.user):
            assert not re.search(r"<\s*/?\s*untrusted", body, re.IGNORECASE)
    # Outcome: with the same judge verdict, the injected text does not move the score.
    if baseline.status == "scored" and res.status == "scored":
        assert res.score == baseline.score, (res, baseline)


def _pin() -> None:  # pragma: no cover - maintenance helper
    """Rewrite ``tests/fixtures/prompt_hashes.json`` from the current templates.

    Run only after a deliberate template version bump; review the diff before committing.
    """
    SNAPSHOT.write_text(json.dumps(current_hashes(), indent=1) + "\n", encoding="utf-8")
