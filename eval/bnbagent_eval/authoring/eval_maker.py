"""Eval maker: turn a natural-language description into a lint-clean suite.

A port of the AgentSight eval_maker (a LangGraph state machine) as a plain async loop:

1. **assess**: domain, risks, needed qualities and input quality (rejects unusable input);
2. **strategy**: one evaluation dimension per stated requirement (catalogue or custom);
3. **match**: catalogue metrics for the dimensions; ids are validated against the registry
   (aliases resolved, near-misses repaired, hallucinations dropped);
4. **generate custom**: ``custom.rubric`` / ``custom.requirements_compliance`` /
   ``custom.judge_prompt`` configs for the dimensions left uncovered;
5. **reflect** (at most ``max_reflections``): judge self-critique fed by ``lint_suite``
   findings and validation notes; a regeneration re-runs match and generate;
6. **validate**: ``Suite.load`` + ``lock_suite`` + ``lint_suite``; lint errors raise;
7. **build**: the locked suite, a rationale per metric and the step log.

Every judge call asks for structured JSON (one repair re-ask, as ``JudgeMetric``) and fences
untrusted text. For negotiated stages the judge is pinned (temperature 0, >= 3 samples), an
inconclusive band is set and rule methods are preferred where a metric has one.
"""

from __future__ import annotations

import dataclasses
import difflib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .._optional import available
from ..engine.resolve import resolve_suite
from ..exceptions import EvalError, JudgeParseError
from ..hashing import hash_obj
from ..judges.base import JudgeModel, JudgeRequest
from ..judges.fencing import fence, fence_clause, make_nonce
from ..judges.prompts import PromptTemplate
from ..judges.prompts.authoring import (
    eval_maker_assess,
    eval_maker_generate,
    eval_maker_match,
    eval_maker_reflect,
    eval_maker_strategy,
    stage_guidance,
)
from ..judges.structured import REPAIR_INSTRUCTION, parse
from ..metrics import registry
from ..metrics.base import INPUT_NAMES, Kind, MethodSpec, MetricSpec
from ..types.case import EvalCase
from ..types.suite import MetricConfig, Suite
from .lint import LintIssue, Use, lint_suite, lock_suite

__all__ = [
    "CUSTOM_METRIC_IDS",
    "EvalMakerError",
    "EvalMakerResult",
    "EvalMakerStep",
    "InputQualityError",
    "make_suite",
]

NEGOTIATED_STAGES = ("registration", "pre_submit", "review", "vote")
CUSTOM_METRIC_IDS = eval_maker_generate.CUSTOM_METRIC_IDS
# Never offered by the maker: they need code or people, not a description.
EXCLUDED_METRICS = frozenset({"custom.python", "custom.human_review"})
MIN_MATCH_CONFIDENCE = 0.7  # AgentSight match_blackbox
ID_REPAIR_CUTOFF = 0.85  # difflib ratio for repairing a near-miss metric id
# AgentSight QualityThresholds (minimums) and the overall-score weights of assess_quality.
MIN_SCORES = {"clarity": 0.3, "completeness": 0.25, "correlation": 0.2, "overall": 0.3}
OVERALL_WEIGHTS = {"clarity": 0.35, "completeness": 0.35, "correlation": 0.30}
NEGOTIATED_SAMPLES = 3
INCONCLUSIVE_BAND = 0.05
MAX_SAMPLE_CASES, MAX_SAMPLE_CHARS = 3, 600
_TRUSTED_FIELDS = frozenset({"catalogue", "stage_guidance", "inputs"})


class EvalMakerError(EvalError):
    """The eval maker could not produce a lint-clean suite."""

    def __init__(self, message: str, issues: Sequence[LintIssue] = ()):
        super().__init__(message)
        self.issues = tuple(issues)


class InputQualityError(EvalMakerError):
    """The description is too vague or incomplete to generate a reliable suite."""

    def __init__(self, message: str, assessment: Mapping[str, Any]):
        super().__init__(message)
        self.assessment = dict(assessment)


@dataclass(frozen=True)
class EvalMakerStep:
    """One stage of the run: the (validated) judge output plus validation notes."""

    stage: str
    data: Mapping[str, Any]
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"stage": self.stage, "data": dict(self.data), "notes": list(self.notes)}


@dataclass(frozen=True)
class EvalMakerResult:
    suite: Suite
    rationale: Mapping[str, str]  # metric key -> why it is in the suite
    steps: tuple[EvalMakerStep, ...]
    lint: tuple[LintIssue, ...]  # remaining findings (never errors)

    @property
    def reflections(self) -> int:
        return sum(1 for s in self.steps if s.stage == "reflect")

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite.to_dict(),
            "rationale": dict(self.rationale),
            "steps": [s.to_dict() for s in self.steps],
            "lint": [i.to_dict() for i in self.lint],
        }


@dataclass(frozen=True)
class _Planned:
    config: MetricConfig
    kind: Kind
    method: MethodSpec
    dimensions: tuple[str, ...]
    rationale: str


@dataclass
class _Run:
    """State shared by the stages of one ``make_suite`` call."""

    description: str
    judge: JudgeModel
    stage: str | None
    catalogue: Mapping[str, MetricSpec]
    present: frozenset[str] | None  # inputs present in the sample cases (None: no samples)
    samples_text: str
    steps: list[EvalMakerStep] = dataclasses.field(default_factory=list)
    assessment: dict[str, Any] = dataclasses.field(default_factory=dict)
    dimensions: list[dict[str, Any]] = dataclasses.field(default_factory=list)

    @property
    def strict(self) -> bool:
        return self.stage in NEGOTIATED_STAGES

    @property
    def use(self) -> Use:
        return "negotiated" if self.strict else "dev"


async def make_suite(
    description: str,
    *,
    judge: JudgeModel,
    sample_cases: Sequence[EvalCase | Mapping[str, Any]] = (),
    stage: str | None = None,
    max_reflections: int = 2,
    name: str | None = None,
) -> EvalMakerResult:
    """Generate a lint-clean suite for ``description`` (see the module docstring).

    ``stage`` (``dev``, ``registration``, ``pre_submit``, ``review``, ``vote``) selects the
    lint use and the stage guidance. Raises :class:`InputQualityError` for unusable input,
    :class:`EvalMakerError` if lint errors remain after the reflections.
    """
    if not description.strip():
        raise EvalMakerError("the description is empty")
    if max_reflections < 0:
        raise ValueError("max_reflections must be >= 0")
    cases = [c if isinstance(c, EvalCase) else EvalCase.from_dict(c) for c in sample_cases]
    run = _Run(
        description=description,
        judge=judge,
        stage=stage,
        catalogue=_catalogue(),
        present=_present_inputs(cases) if cases else None,
        samples_text=_samples_text(cases),
    )
    await _assess(run)
    await _strategy(run)
    planned, notes = await _draft(run, [], 0)
    reflections = 0
    while True:
        suite, issues = _check(run, planned, name)
        notes = notes + _compatibility_notes(run, planned)
        actionable = [i for i in issues if i.level != "info"]
        if reflections >= max_reflections:
            break
        critique = await _reflect(run, planned, actionable, notes, reflections)
        reflections += 1
        if not critique["should_regenerate"] and not actionable:
            break
        feedback = [_issue_text(i) for i in actionable]
        feedback += [str(s) for s in critique.get("improvements_suggested", [])]
        feedback += notes
        planned, notes = await _draft(run, feedback, reflections)
    return _validate_and_build(run, suite, issues, planned)


# ------------------------------------------------------------------ stages
async def _assess(run: _Run) -> None:
    data = await _ask(
        run.judge,
        eval_maker_assess.V1,
        {"description": run.description, "samples": run.samples_text},
        tag="assess",
    )
    scores = {k: float(data[f"{k}_score"]) for k in OVERALL_WEIGHTS}
    scores["overall"] = sum(scores[k] * w for k, w in OVERALL_WEIGHTS.items())
    data["overall_score"] = round(scores["overall"], 4)
    low = [f"{k} {scores[k]:.2f} < {m}" for k, m in MIN_SCORES.items() if scores[k] < m]
    run.steps.append(EvalMakerStep("assess", data, tuple(data.get("warnings", []))))
    if low:
        issues = [str(i) for k in OVERALL_WEIGHTS for i in data.get(f"{k}_issues", []) or []] + [
            str(r) for r in data.get("recommendations", []) or []
        ]
        detail = f"; {'; '.join(issues[:5])}" if issues else ""
        raise InputQualityError(
            f"the description is too vague to generate a suite ({', '.join(low)}){detail}", data
        )
    run.assessment = data


async def _strategy(run: _Run) -> None:
    data = await _ask(
        run.judge,
        eval_maker_strategy.V1,
        {
            "description": run.description,
            "assessment": _json(_assessment_brief(run.assessment)),
            "stage_guidance": stage_guidance(run.stage),
            "catalogue": _catalogue_text(run.catalogue),
        },
        tag="strategy",
    )
    dims, seen = [], set()
    for d in data["dimensions"]:
        dim_name = _unique(_slug(d["name"]) or "dimension", seen)
        dims.append({**d, "name": dim_name})
    run.dimensions = dims
    run.steps.append(EvalMakerStep("strategy", {**data, "dimensions": dims}))


async def _draft(run: _Run, feedback: list[str], round_: int) -> tuple[list[_Planned], list[str]]:
    """Match catalogue metrics, then generate custom metrics for the uncovered dimensions."""
    matched, notes = await _match(run, feedback, round_)
    covered = {d for p in matched for d in p.dimensions}
    uncovered = [d for d in run.dimensions if d["name"] not in covered]
    keys = {p.config.key for p in matched}
    custom, more = await _generate(run, uncovered, feedback, keys, round_)
    return matched + custom, notes + more


async def _match(run: _Run, feedback: list[str], round_: int) -> tuple[list[_Planned], list[str]]:
    dims = [
        {
            "name": d["name"],
            "description": d.get("description", ""),
            "approach": d.get("approach"),
            "candidate_metric": d.get("candidate_metric"),
        }
        for d in run.dimensions
    ]
    data = await _ask(
        run.judge,
        eval_maker_match.V1,
        {
            "dimensions": _json(dims),
            "stage_guidance": stage_guidance(run.stage),
            "inputs": ", ".join(sorted(run.present)) if run.present is not None else "unknown",
            "feedback": _bullets(feedback),
            "catalogue": _catalogue_text(run.catalogue),
        },
        tag="match",
        idx=round_,
    )
    planned: list[_Planned] = []
    notes: list[str] = []
    by_id: dict[str, int] = {}
    for m in data["matches"]:
        item, note = _validate_match(run, m)
        if note:
            notes.append(note)
        if item is None:
            continue
        if item.config.id in by_id:  # one config per metric: merge the coverage
            i = by_id[item.config.id]
            prior = planned[i]
            planned[i] = dataclasses.replace(prior, dimensions=prior.dimensions + item.dimensions)
            continue
        by_id[item.config.id] = len(planned)
        planned.append(item)
    run.steps.append(
        EvalMakerStep(
            "match",
            {**data, "accepted": [_planned_dict(p) for p in planned], "round": round_},
            tuple(notes),
        )
    )
    return planned, notes


def _validate_match(run: _Run, m: Mapping[str, Any]) -> tuple[_Planned | None, str | None]:
    raw, dim = m.get("metric_id"), str(m.get("dimension", ""))
    if not raw:
        return None, None
    confidence = float(m.get("confidence", 1.0))
    if confidence < MIN_MATCH_CONFIDENCE:
        return None, f"match {raw!r} for {dim!r} dropped: confidence {confidence:.2f} < 0.7"
    metric_id, alias_method, note = _canonical_id(str(raw), run.catalogue)
    if metric_id is None:
        return None, note
    spec = run.catalogue[metric_id]
    notes = [note] if note else []
    usable = {x.name: x for x in spec.methods if _method_available(x)}
    method = m.get("method") or alias_method
    if method and method not in usable:
        notes.append(f"{metric_id}: method {method!r} is not available; using the default")
        method = None
    if method is None and run.strict:
        method = _prefer_rule(spec, usable, run.present)
    if method is None and spec.methods[0].name not in usable:
        method = next(iter(usable))
    params = dict(m.get("params") or {})
    threshold, tnote = _threshold(m.get("threshold"), metric_id)
    if tnote:
        notes.append(tnote)
    cfg = MetricConfig(
        id=metric_id,
        method=method,
        params=params,
        threshold=threshold,
        required=bool(m.get("required")),
    )
    problem = _probe(cfg)
    if problem:
        notes.append(f"{metric_id} for {dim!r} dropped: {problem}")
        return None, "; ".join(notes)
    method_spec = usable[method or spec.methods[0].name]
    rationale = str(m.get("reasoning") or spec.description)
    item = _Planned(cfg, method_spec.kind, method_spec, (dim,), rationale)
    return item, "; ".join(notes) or None


async def _generate(
    run: _Run, uncovered: list[dict[str, Any]], feedback: list[str], keys: set[str], round_: int
) -> tuple[list[_Planned], list[str]]:
    if not uncovered:
        run.steps.append(EvalMakerStep("generate", {"metrics": [], "round": round_}))
        return [], []
    dims = [
        {"name": d["name"], "description": d.get("description", ""), "group": d["name"]}
        for d in uncovered
    ]
    data = await _ask(
        run.judge,
        eval_maker_generate.V1,
        {
            "description": run.description,
            "assessment": _json(_assessment_brief(run.assessment)),
            "dimensions": _json(dims),
            "stage_guidance": stage_guidance(run.stage),
            "feedback": _bullets(feedback),
        },
        tag="generate",
        idx=round_,
    )
    planned: list[_Planned] = []
    notes: list[str] = []
    for g in data["metrics"]:
        metric_id = g["metric_id"]
        threshold, tnote = _threshold(g.get("threshold"), metric_id)
        if tnote:
            notes.append(tnote)
        key = _unique(_slug(g["name"]) or "custom_metric", keys)
        cfg = MetricConfig(
            id=metric_id,
            name=key,
            params=dict(g["params"]),
            threshold=threshold,
            required=bool(g.get("required")),
        )
        problem = _probe(cfg)
        if problem:
            keys.discard(key)
            notes.append(f"custom metric {g['name']!r} dropped: {problem}")
            continue
        spec = run.catalogue.get(metric_id) or registry.get_spec(metric_id)
        dimensions = tuple(str(d) for d in g.get("dimensions") or ()) or (key,)
        rationale = str(g.get("rationale") or "custom metric for an uncovered dimension")
        planned.append(_Planned(cfg, spec.methods[0].kind, spec.methods[0], dimensions, rationale))
    run.steps.append(
        EvalMakerStep(
            "generate",
            {**data, "accepted": [_planned_dict(p) for p in planned], "round": round_},
            tuple(notes),
        )
    )
    return planned, notes


async def _reflect(
    run: _Run, planned: list[_Planned], issues: list[LintIssue], notes: list[str], round_: int
) -> dict[str, Any]:
    data = await _ask(
        run.judge,
        eval_maker_reflect.V1,
        {
            "description": run.description,
            "stage_guidance": stage_guidance(run.stage),
            "draft": _json([_planned_dict(p) for p in planned]),
            "lint": _bullets([_issue_text(i) for i in issues]),
            "notes": _bullets(notes),
        },
        tag="reflect",
        idx=round_,
    )
    lint = [i.to_dict() for i in issues]
    run.steps.append(EvalMakerStep("reflect", {**data, "lint": lint, "round": round_ + 1}))
    return data


def _validate_and_build(
    run: _Run, suite: Suite | None, issues: list[LintIssue], planned: list[_Planned]
) -> EvalMakerResult:
    errors = [i for i in issues if i.level == "error"]
    run.steps.append(EvalMakerStep("validate", {"lint": [i.to_dict() for i in issues]}))
    if suite is None or errors:
        detail = "; ".join(_issue_text(i) for i in errors) or "no suite"
        raise EvalMakerError(f"the generated suite is not lint-clean: {detail}", issues)
    rationale = {p.config.key: f"Covers {', '.join(p.dimensions)}: {p.rationale}" for p in planned}
    run.steps.append(EvalMakerStep("build", {"metrics": list(rationale)}))
    return EvalMakerResult(
        suite=suite, rationale=rationale, steps=tuple(run.steps), lint=tuple(issues)
    )


# ------------------------------------------------------------- validation
def _check(
    run: _Run, planned: list[_Planned], name: str | None
) -> tuple[Suite | None, list[LintIssue]]:
    """Build, load, lock and lint the candidate suite."""
    if not planned:
        return None, [LintIssue("error", "empty_suite", "no metric survived validation")]
    try:
        suite = lock_suite(Suite.load(_suite_dict(run, planned, name)))
    except (EvalError, ValueError) as exc:
        return None, [LintIssue("error", "invalid_suite", str(exc))]
    return suite, lint_suite(suite, use=run.use)


def _suite_dict(run: _Run, planned: list[_Planned], name: str | None) -> dict[str, Any]:
    has_judge = any(p.kind is Kind.JUDGE for p in planned)
    data: dict[str, Any] = {
        "name": name or _default_name(run.assessment),
        "version": "1",
        "description": run.description.strip()[:500],
        "metrics": [p.config.to_dict() for p in planned],
        "extensions": {"eval_maker": {"stage": run.stage or "dev"}},
    }
    if has_judge:
        cfg = run.judge.config
        if run.strict:
            cfg = dataclasses.replace(
                cfg,
                temperature=0.0 if cfg.temperature is not None else None,
                samples=max(cfg.samples, NEGOTIATED_SAMPLES),
            )
            data["pass_rule"] = {"inconclusive_band": INCONCLUSIVE_BAND}
        data["judge"] = cfg.to_dict()
    return data


def _probe(cfg: MetricConfig) -> str | None:
    """Resolve one metric on its own; returns the problem, if any."""
    try:
        resolve_suite(Suite(name="probe", version="0", metrics=(cfg,)))
    except (EvalError, ValueError) as exc:
        return str(exc)
    return None


def _compatibility_notes(run: _Run, planned: list[_Planned]) -> list[str]:
    """AgentSight sample compatibility (non-blocking): required inputs absent from samples."""
    if run.present is None:
        return []
    out = []
    for p in planned:
        missing = sorted(p.method.required_inputs - run.present)
        if missing:
            out.append(
                f"{p.config.key} needs inputs {missing} that the sample cases do not contain"
            )
    return out


def _canonical_id(
    raw: str, catalogue: Mapping[str, MetricSpec]
) -> tuple[str | None, str | None, str | None]:
    """``(metric_id, alias_method, note)``: exact id, alias, near-miss repair, or dropped."""
    if raw in catalogue:
        return raw, None, None
    try:
        cls, alias_method = registry.resolve(raw)
    except KeyError:
        cls, alias_method = None, None
    if cls is not None and cls.spec.id in catalogue:
        return cls.spec.id, alias_method, f"alias {raw!r} resolved to {cls.spec.id!r}"
    close = difflib.get_close_matches(raw, list(catalogue), n=1, cutoff=ID_REPAIR_CUTOFF)
    if close:
        return close[0], None, f"unknown metric id {raw!r} repaired to {close[0]!r}"
    return None, None, f"unknown metric id {raw!r} dropped (not in the catalogue)"


def _prefer_rule(
    spec: MetricSpec, usable: Mapping[str, MethodSpec], present: frozenset[str] | None
) -> str | None:
    """For negotiated stages: a rule method instead of a non-rule default, if one is usable."""
    default = usable.get(spec.methods[0].name)
    if default is not None and default.kind is Kind.RULE:
        return None
    for m in usable.values():
        if m.kind is Kind.RULE and (present is None or m.required_inputs <= present):
            return m.name
    return None


def _threshold(value: Any, metric_id: str) -> tuple[float | None, str | None]:
    if value is None:
        return None, None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1:
        return float(value), None
    return None, f"{metric_id}: threshold {value!r} is outside [0, 1]; using the default"


# ---------------------------------------------------------------- judge
async def _ask(
    judge: JudgeModel, template: PromptTemplate, fields: Mapping[str, str], *, tag: str, idx=0
) -> dict[str, Any]:
    """One fenced, structured judge call with a single repair re-ask."""
    nonce = make_nonce(hash_obj(dict(fields)), f"{template.metric_id}@{template.version}", idx)
    rendered = {k: v if k in _TRUSTED_FIELDS else fence(k, v, nonce) for k, v in fields.items()}
    request = JudgeRequest(
        system=template.system + fence_clause(nonce),
        user=template.render("simple", rendered),
        output_schema=template.output_schema,
        metadata={
            "metric_id": template.metric_id,
            "prompt_version": template.version,
            "tag": f"eval_maker:{tag}",
        },
    )
    response = await judge.complete(request)
    try:
        return parse(response.text, response.parsed, template.output_schema)
    except JudgeParseError as first:
        repair = dataclasses.replace(
            request, user=request.user + "\n\n" + REPAIR_INSTRUCTION.format(problems=first)
        )
        response = await judge.complete(repair)
        return parse(response.text, response.parsed, template.output_schema)


# -------------------------------------------------------------- helpers
def _method_available(method: MethodSpec) -> bool:
    return method.extra_module is None or available(method.extra_module)


def _catalogue() -> dict[str, MetricSpec]:
    """Registry specs the maker may choose (custom.* are generated, not matched)."""
    out = {}
    for spec in registry.list_specs():
        if spec.id in EXCLUDED_METRICS or spec.id in CUSTOM_METRIC_IDS:
            continue
        if any(_method_available(m) for m in spec.methods):
            out[spec.id] = spec
    return out


def _catalogue_text(catalogue: Mapping[str, MetricSpec]) -> str:
    lines = []
    for spec in catalogue.values():
        methods = ", ".join(
            f"{m.name} ({m.kind.value}; needs {', '.join(sorted(m.required_inputs)) or 'none'})"
            for m in spec.methods
            if _method_available(m)
        )
        line = f"- {spec.id}: {spec.description} | methods: {methods}"
        props = spec.params_schema.get("properties", {})
        required = list(spec.params_schema.get("required", ()))
        if required:
            shown = "; ".join(f"{r} {json.dumps(props.get(r, {}))[:160]}" for r in required)
            line += f" | required params: {shown}"
        lines.append(line)
    return "\n".join(lines)


def _present_inputs(cases: Sequence[EvalCase]) -> frozenset[str]:
    return frozenset(n for n in INPUT_NAMES - {"runs"} if any(c.has(n) for c in cases))


def _samples_text(cases: Sequence[EvalCase]) -> str:
    if not cases:
        return "Not provided"
    parts = [f"{len(cases)} samples provided:"]
    for i, case in enumerate(cases[:MAX_SAMPLE_CASES]):
        text = json.dumps(case.to_dict(), sort_keys=True, ensure_ascii=False, default=str)
        parts.append(f"Sample {i + 1}: {text[:MAX_SAMPLE_CHARS]}")
    return "\n".join(parts)


def _assessment_brief(assessment: Mapping[str, Any]) -> dict[str, Any]:
    return {k: assessment.get(k) for k in ("domain", "risks", "qualities")}


def _planned_dict(p: _Planned) -> dict[str, Any]:
    cfg = {k: v for k, v in p.config.to_dict().items() if v not in (None, {}, False)}
    return {**cfg, "kind": p.kind.value, "dimensions": list(p.dimensions)}


def _issue_text(issue: LintIssue) -> str:
    where = f" ({issue.metric})" if issue.metric else ""
    return f"[{issue.level}] {issue.code}{where}: {issue.message}"


def _bullets(items: Sequence[str]) -> str:
    return "\n".join(f"- {i}" for i in items) if items else "None"


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str)


def _slug(text: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", text.lower())).strip("_")[:40]


def _unique(base: str, taken: set[str]) -> str:
    key, n = base, 2
    while key in taken:
        key, n = f"{base}_{n}", n + 1
    taken.add(key)
    return key


def _default_name(assessment: Mapping[str, Any]) -> str:
    slug = _slug(str(assessment.get("domain") or "")).replace("_", "-")
    return f"{slug}-suite" if slug else "generated-suite"
