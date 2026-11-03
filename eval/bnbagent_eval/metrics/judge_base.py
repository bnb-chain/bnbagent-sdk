"""JudgeMetric: the standard native LLM-judge flow.

``_evaluate_native`` (default implementation) = select input mode → build fields → fence →
k samples (budget / deadline checked per call, cache, one repair re-ask) → label → score →
lower-median aggregation → provenance. Metrics with special logic override ``score_parsed`` or
``_evaluate_native`` and reuse :meth:`JudgeMetric.ask`.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from ..adapters.traces.render import render_trajectory, session_messages, tools_text
from ..exceptions import JudgeParseError, SuiteError
from ..hashing import hash_obj
from ..judges.base import JudgeRequest, JudgeResponse
from ..judges.fencing import DEFAULT_SALT, fence, fence_clause, make_nonce
from ..judges.prompts import PromptTemplate
from ..judges.sampling import median_low
from ..judges.structured import REPAIR_INSTRUCTION, parse
from ..types.case import EvalCase, content_text
from .base import BaseMetric, Kind, MethodSpec, MetricContext, Outcome, StopScoring
from .text._common import as_text

DEFAULT_MAX_FIELD_CHARS = 24_000
NEGOTIATED_STAGES = ("pre_submit", "review", "vote")

# Common params accepted by every native judge metric. ``agent_id`` is not
# one of them: only metrics whose prompt renders the trajectory accept it (AGENT_ID_PARAM).
JUDGE_PARAMS: dict[str, Any] = {
    # "auto" or any mode the metric's template defines (checked by select_mode).
    "input_mode": {"type": "string", "pattern": "^[a-z_]+$"},
    "domain_rubric": {"type": "string", "maxLength": 2000},
    "prompt_version": {"type": "string"},
    "samples": {"type": "integer", "minimum": 1, "maximum": 15},
    "max_field_chars": {"type": "integer", "minimum": 200},
}


# Restricts the rendered trajectory (``fields()["trajectory"]``) to one agent's steps.
AGENT_ID_PARAM: dict[str, Any] = {
    "agent_id": {"type": "string", "description": "Restrict to one agent of the trajectory"}
}


def judge_params(extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    props = dict(JUDGE_PARAMS)
    props.update(extra or {})
    return {"type": "object", "properties": props, "additionalProperties": False}


def native_method(*required: str, optional: Sequence[str] = ()) -> MethodSpec:
    return MethodSpec(
        "native",
        Kind.JUDGE,
        required_inputs=frozenset(required),
        optional_inputs=frozenset(optional),
    )


@dataclass(frozen=True)
class Sample:
    parsed: dict[str, Any]
    response: JudgeResponse


def truncate(text: str, limit: int) -> tuple[str, bool]:
    """Keep the head (70%) and tail (30%) of over-long text, with a marker."""
    if len(text) <= limit:
        return text, False
    head, tail = int(limit * 0.7), int(limit * 0.3)
    cut = len(text) - head - tail
    return f"{text[:head]}\n[... {cut} characters truncated ...]\n{text[-tail:]}", True


def conversation_text(case: EvalCase) -> str:
    """AgentSight format: ``Role: content`` turns separated by blank lines.

    Without explicit messages, the turns are derived from the trajectory's agent steps.
    """
    messages = case.conversation
    if not messages and case.trajectory is not None:
        messages = session_messages(case.trajectory)
    return "\n\n".join(f"{m.role.capitalize()}: {m.content}" for m in messages)


def contexts_text(case: EvalCase) -> str:
    lines = []
    for i, c in enumerate(case.contexts, 1):
        meta = ", ".join(
            f"{k}: {v}" for k, v in (("source", c.source), ("timestamp", c.timestamp)) if v
        )
        lines.append(f"[{i}] {c.text}" + (f" ({meta})" if meta else ""))
    return "\n".join(lines)


def check_limits(ctx: MetricContext, tokens: int = 0, what: str = "judge call") -> None:
    """Raise :class:`StopScoring` if the deadline passed or the run budget cannot take one more
    judge call (``tokens`` estimated)."""
    if ctx.deadline is not None and ctx.clock() >= ctx.deadline:
        raise StopScoring("deadline", f"deadline reached before the {what}")
    if ctx.budget is not None and not ctx.budget.reserve(judge_calls=1, tokens=tokens):
        raise StopScoring("budget_exhausted", "run budget exhausted")


async def complete_within_limits(ctx: MetricContext, request: JudgeRequest) -> JudgeResponse:
    """``ctx.judge.complete(request)`` after the deadline / budget check (every judge call of a
    metric, native or through a vendor bridge, goes through here)."""
    estimate = (len(request.system) + len(request.user)) // 4 + ctx.judge.config.max_tokens
    check_limits(ctx, estimate)
    return await ctx.judge.complete(request)


class LimitedJudge:
    """``ctx.judge`` with the run's deadline and budget applied to every call (handed to vendor
    libraries, which call the judge themselves)."""

    def __init__(self, ctx: MetricContext):
        self._ctx = ctx
        self.config = ctx.judge.config

    def __getattr__(self, name: str) -> Any:  # omit_temperature, auto_tool_choice, ...
        return getattr(self._ctx.judge, name)

    async def complete(self, request: JudgeRequest) -> JudgeResponse:
        return await complete_within_limits(self._ctx, request)

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        check_limits(self._ctx, what="embed call")
        return await self._ctx.judge.embed(texts)


class JudgeMetric(BaseMetric):
    """Base for native LLM-judge metrics with one prompt template per version."""

    templates: ClassVar[Mapping[str, PromptTemplate]]  # prompt_version -> template
    default_version: ClassVar[str] = "v1"
    include_input: ClassVar[bool] = True  # AgentSight build_prompt(include_input=...)
    label_field: ClassVar[str] = "score"  # reply field holding the categorical label
    # AgentSight GENERAL / COMPARISON categories (``categories.validate_inputs``): actual output
    # *or* a conversation. Applies to the judge method only (rule methods need ``output``).
    output_or_conversation: ClassVar[bool] = False

    def has_input(self, name: str, case: EvalCase, ctx: MetricContext) -> bool:
        if (
            name == "output"
            and self.output_or_conversation
            and ctx.method.kind is Kind.JUDGE
            and case.conversation
        ):
            return True
        return super().has_input(name, case, ctx)

    # ------------------------------------------------------------- templates
    def template_for(self, params: Mapping[str, Any]) -> PromptTemplate:
        version = params.get("prompt_version", self.default_version)
        try:
            return self.templates[version]
        except KeyError as exc:
            raise SuiteError(
                f"{self.spec.id}: unknown prompt_version {version!r} "
                f"(available: {sorted(self.templates)})"
            ) from exc

    def prompt_hash(
        self, method: MethodSpec, params: Mapping[str, Any] | None = None
    ) -> str | None:
        if method.kind is not Kind.JUDGE:
            return None
        return self.template_for(params or {}).hash()

    # ---------------------------------------------------------------- inputs
    def select_mode(self, case: EvalCase, ctx: MetricContext, template: PromptTemplate) -> str:
        requested = ctx.params.get("input_mode", "auto")
        if requested != "auto":
            if requested not in template.modes:
                raise ValueError(f"input_mode {requested!r} is not supported by {self.spec.id}")
            return requested
        # AgentSight ``detect_input_format``: a message list is a conversation. Ours needs two
        # messages when an output is also given; a lone message without output is judged as a
        # conversation too (else the judge would see an empty response).
        conv = len(case.conversation) >= 2 or (bool(case.conversation) and case.output is None)
        if conv and "conversation" in template.modes:
            return "conversation"
        if case.expected is not None and "with_expected" in template.modes:
            return "with_expected"
        return template.modes[0]

    def fields(self, case: EvalCase, ctx: MetricContext, mode: str) -> dict[str, str]:
        out = {
            "input": (content_text(case.input) or as_text(case.input))
            if self.include_input
            else "",
            "actual_output": content_text(case.output) or as_text(case.output),
            "expected_output": as_text(case.expected) if case.expected is not None else "",
            "conversation_history": conversation_text(case),
            "contexts": contexts_text(case),
            "trajectory": render_trajectory(case.trajectory, agent_id=ctx.params.get("agent_id"))
            if case.trajectory is not None
            else "",
            "available_tools": tools_text(case.trajectory) if case.trajectory is not None else "",
        }
        return out

    # ------------------------------------------------------------------ core
    async def ask(
        self,
        case: EvalCase,
        ctx: MetricContext,
        template: PromptTemplate,
        mode: str,
        fields: Mapping[str, str],
        *,
        tag: str = "",
    ) -> tuple[list[Sample], dict]:
        """Run ``samples`` judge calls; return samples and provenance info."""
        if ctx.judge is None:
            raise SuiteError(f"{self.spec.id} needs a judge model")
        k = int(ctx.params.get("samples", getattr(ctx.judge.config, "samples", 1) or 1))
        limit = int(ctx.params.get("max_field_chars", DEFAULT_MAX_FIELD_CHARS))
        clipped, truncated = {}, False
        for name, value in fields.items():
            clipped[name], cut = truncate(value, limit)
            truncated |= cut
        if (
            truncated
            and ctx.config.required
            and not ctx.config.allow_truncation
            and ctx.stage in NEGOTIATED_STAGES
        ):
            raise StopScoring("truncated", "input exceeds max_field_chars in a negotiated suite")
        system_base = template.system_prompt(ctx.params.get("domain_rubric"))
        case_hash = hash_obj(case.to_dict())
        samples: list[Sample] = []
        sources: set[str] = set()
        for i in range(k):
            nonce = make_nonce(case_hash, f"{self.spec.id}{tag}@{template.version}", i)
            fenced = {name: fence(name, value, nonce) for name, value in clipped.items()}
            request = JudgeRequest(
                system=system_base + fence_clause(nonce),
                user=template.render(mode, fenced),
                output_schema=template.output_schema,
                metadata={
                    "metric_id": self.spec.id,
                    "prompt_version": template.version,
                    "mode": mode,
                    "sample_idx": i,
                    "tag": tag,
                },
            )
            cache_key = hash_obj(
                {
                    "provider": ctx.judge.config.provider,
                    "model": ctx.judge.config.model,
                    "temperature": ctx.judge.config.temperature,
                    "max_tokens": ctx.judge.config.max_tokens,
                    "template": template.hash(),
                    "rubric": ctx.params.get("domain_rubric"),
                    "mode": mode,
                    "tag": tag,
                    "inputs": clipped,
                    "sample": i,
                }
            )
            parsed, response = await self._call(ctx, request, template, cache_key)
            sources.add(response.source)
            samples.append(Sample(parsed=parsed, response=response))
        judge = ctx.judge.config
        provenance = {
            "prompt_hash": template.hash(),
            "judge": {
                "provider": judge.provider,
                "model": judge.model,
                "temperature": None
                if getattr(ctx.judge, "omit_temperature", False)
                else judge.temperature,
                "max_tokens": judge.max_tokens,
                "salt": DEFAULT_SALT,
                # only when the model could not be forced to call the verdict tool
                **(
                    {"tool_choice": "auto"}
                    if getattr(ctx.judge, "auto_tool_choice", False)
                    else {}
                ),
            },
            "samples": k,
            "judge_source": sources.pop() if len(sources) == 1 else "mixed",
            "truncated": truncated,
        }
        return samples, provenance

    async def _call(
        self, ctx: MetricContext, request: JudgeRequest, template: PromptTemplate, cache_key: str
    ) -> tuple[dict[str, Any], JudgeResponse]:
        if ctx.cache is not None:
            hit = ctx.cache.get(cache_key)
            if hit is not None:
                return hit["parsed"], JudgeResponse(
                    text=hit.get("text", ""),
                    parsed=hit["parsed"],
                    model=hit.get("model", ""),
                    source=ctx.cache.name,
                )
        response = await self._complete(ctx, request)
        try:
            parsed = parse(response.text, response.parsed, template.output_schema)
        except JudgeParseError as first:
            repair = dataclasses.replace(
                request, user=request.user + "\n\n" + REPAIR_INSTRUCTION.format(problems=first)
            )
            response = await self._complete(ctx, repair)
            parsed = parse(response.text, response.parsed, template.output_schema)
        if ctx.cache is not None:
            ctx.cache.set(
                cache_key, {"parsed": parsed, "text": response.text, "model": response.model}
            )
        return parsed, response

    async def _complete(self, ctx: MetricContext, request: JudgeRequest) -> JudgeResponse:
        return await complete_within_limits(ctx, request)

    # --------------------------------------------------------------- scoring
    def score_parsed(self, parsed: Mapping[str, Any], template: PromptTemplate) -> float:
        """Map one parsed judge reply to a score in [0, 1] (default: label lookup)."""
        if template.labels is None:
            raise NotImplementedError(f"{self.spec.id} must implement score_parsed")
        return template.labels[parsed[self.label_field]]

    def outcome(
        self,
        samples: list[Sample],
        scores: list[float],
        provenance: dict,
        template: PromptTemplate,
        **details: Any,
    ) -> Outcome:
        """Combine k samples: lower median score; reasoning/extras from the median sample."""
        score = median_low(scores)
        chosen = samples[scores.index(score)].parsed
        extras = {k: v for k, v in chosen.items() if k not in ("reasoning", self.label_field)}
        d = {**extras, **details}
        if isinstance(chosen.get(self.label_field), str):
            d["label"] = chosen[self.label_field]
        if len(samples) > 1:
            d["sample_scores"] = scores
        return Outcome(
            score=score, reason=chosen.get("reasoning"), details=d, provenance=provenance
        )

    async def _evaluate_native(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        template = self.template_for(ctx.params)
        mode = self.select_mode(case, ctx, template)
        samples, provenance = await self.ask(
            case, ctx, template, mode, self.fields(case, ctx, mode)
        )
        scores = [self.score_parsed(s.parsed, template) for s in samples]
        return self.outcome(samples, scores, provenance, template, mode=mode)
