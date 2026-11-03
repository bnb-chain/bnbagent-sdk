"""``agentic`` method of ``custom.rubric`` and ``custom.requirements_compliance``.

A multi-step judge with read-only tools, built on our ``JudgeModel`` (ETK agent_as_judge
without the OpenCode subprocess):

1. **plan**: collect the checks (the trusted ``rubrics`` / ``requirements`` param, else
   extracted by the judge) and plan which evidence to read;
2. **inspect**: up to ``max_steps`` judge turns, each a JSON action calling one read-only tool
   (``read_output``, ``read_artifact``, ``read_requirement_doc``, ``calculator``) or finishing
   with ``write_verdict``;
3. **verdict** (when the loop did not finish with one): the ETK ``write_verdict`` schema
   ``{scores: [{name, score 0-1, weight 0-1000}], summary, issues}``.

Score = weighted average of the partial scores rounded to 3 decimals (ETK
``AgentVerdict._derive_score``). Param weights are authoritative for listed checks; a listed
check missing from the verdict scores 0. For compatibility, a verdict may also use the native
reply shapes (``requirements: [{id, score}]`` or rubric ``evaluations: [{rubric_id, verdict}]``).
Every judge call goes through ``JudgeMetric.ask`` (fencing, truncation, cache, budget and
deadline per call); ``samples`` = k independent runs, lower median.
"""

from __future__ import annotations

import ast
import dataclasses
import math
import operator
from collections.abc import Mapping, Sequence
from typing import Any

from ...exceptions import JudgeParseError
from ...hashing import canonical_json, hash_obj
from ...judges.prompts import PromptTemplate
from ...judges.prompts.custom import agentic_inspect, agentic_plan, agentic_verdict
from ...judges.sampling import median_low
from ...net import FetchError, fetch_artifact
from ...types.case import EvalCase, content_text
from ...types.result import ErrorInfo
from ..base import Kind, MethodSpec, MetricContext, Outcome
from ..custom.requirements import RequirementsCompliance, requirement_list
from ..custom.rubric import NA, Rubric, normalize_verdict, rubric_list
from ..judge_base import JudgeMetric
from ..registry import register_method
from ..text._common import as_text

METHOD = MethodSpec(
    "agentic",
    Kind.JUDGE,
    optional_inputs=frozenset({"input", "output", "expected", "artifacts", "trajectory"}),
    service_allowed=False,
)
TOOLS = ("read_output", "read_artifact", "read_requirement_doc", "calculator")
FINISH = ("write_verdict", "verdict", "finish", "done")
DEFAULT_MAX_STEPS = 8
DEFAULT_TOOL_CHARS = 8_000
DEFAULT_ARTIFACT_BYTES = 1_000_000
PREVIEW_CHARS = 2_000

PARAMS: dict[str, Any] = {
    "max_steps": {"type": "integer", "minimum": 1, "maximum": 50},
    "max_tool_chars": {"type": "integer", "minimum": 200, "maximum": 100_000},
    "max_artifact_bytes": {"type": "integer", "minimum": 1, "maximum": 50_000_000},
    "documents": {
        "type": "array",
        "items": {
            "anyOf": [
                {"type": "string", "minLength": 1},
                {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "text": {"type": "string"},
                        "uri": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            ]
        },
    },
}


# ------------------------------------------------------------------------------ calculator

_BIN = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_MAX_DIGITS = 100


def _bounded(value: Any) -> float | int:
    if isinstance(value, complex):  # e.g. (-8) ** 0.5
        raise ValueError("complex result")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("result is not finite")
    if abs(value) > 10**_MAX_DIGITS:
        raise ValueError("result too large")
    return value


def calculate(expression: str) -> float | int:
    """Evaluate an arithmetic expression safely (numbers and + - * / // % ** only).

    Results are bounded (|x| <= 1e100, checked before each power is computed), so nested powers
    such as ``((10**100)**100)**100`` from judge-chosen expressions cannot exhaust CPU / memory.
    """
    if len(expression) > 500:
        raise ValueError("expression too long")

    def ev(node: ast.AST) -> float | int:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow):
                if abs(right) > 100:
                    raise ValueError("exponent too large")
                if abs(left) > 1 and right > 0 and right * math.log10(abs(left)) > _MAX_DIGITS:
                    raise ValueError("result too large")
            return _bounded(_BIN[type(node.op)](left, right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return _bounded(_UNARY[type(node.op)](ev(node.operand)))
        raise ValueError(f"unsupported expression element {type(node).__name__}")

    return ev(ast.parse(expression, mode="eval"))


# ---------------------------------------------------------------------------------- tools


@dataclasses.dataclass
class _Workspace:
    """Read-only evidence for one case: output, artifacts and reference documents."""

    case: EvalCase
    ctx: MetricContext
    documents: list[dict[str, Any]]
    cache: dict[str, str] = dataclasses.field(default_factory=dict)

    @property
    def tool_chars(self) -> int:
        return int(self.ctx.params.get("max_tool_chars", DEFAULT_TOOL_CHARS))

    def output_text(self) -> str:
        return content_text(self.case.output) or as_text(self.case.output) or ""

    def inventory(self) -> str:
        lines = [f"- output: {len(self.output_text())} characters (read_output)"]
        for i, a in enumerate(self.case.artifacts):
            size = f", {a.size} bytes" if a.size is not None else ""
            lines.append(f"- artifact {a.name or i}: {a.media_type}{size} (read_artifact)")
        for d in self.documents:
            lines.append(f"- document {d['name']} (read_requirement_doc)")
        return "\n".join(lines)

    def _window(self, text: str, args: Mapping[str, Any]) -> str:
        offset = max(0, int(args.get("offset", 0) or 0))
        limit = min(self.tool_chars, max(1, int(args.get("limit", self.tool_chars) or 1)))
        chunk = text[offset : offset + limit]
        rest = len(text) - offset - len(chunk)
        more = (
            f"\n[... {rest} more characters; use offset={offset + len(chunk)}]" if rest > 0 else ""
        )
        return chunk + more

    async def _fetch(self, uri: str) -> str:
        if uri in self.cache:
            return self.cache[uri]
        cap = int(self.ctx.params.get("max_artifact_bytes", DEFAULT_ARTIFACT_BYTES))
        if uri.startswith("data:"):
            content, _ = await fetch_artifact(uri, cap=cap)
            text = content.decode("utf-8", errors="replace")
        elif self.ctx.storage is not None:  # never fetched over the network directly
            doc = await self.ctx.storage.download(uri)
            body = doc.get("text", doc.get("content")) if isinstance(doc, Mapping) else None
            text = body if isinstance(body, str) else canonical_json(doc)
            if len(text.encode()) > cap:
                raise FetchError("too_large", f"content exceeds {cap} bytes")
        else:
            raise FetchError("unsupported_scheme", "only data: URIs or ctx.storage are readable")
        self.cache[uri] = text
        return text

    def _pick(self, items: Sequence[Any], name: Any, label: Any) -> Any:
        key = str(name if name is not None else "0")
        for i, item in enumerate(items):
            if key in (str(label(item, i)), str(i)):
                return item
        raise KeyError(f"no such item {key!r}")

    async def run(self, action: str, args: Mapping[str, Any]) -> str:
        try:
            if action == "read_output":
                return self._window(self.output_text(), args)
            if action == "read_artifact":
                art = self._pick(self.case.artifacts, args.get("name"), lambda a, i: a.name or i)
                return self._window(await self._fetch(art.uri), args)
            if action == "read_requirement_doc":
                doc = self._pick(self.documents, args.get("name"), lambda d, i: d["name"])
                text = (
                    doc["text"] if doc.get("text") is not None else await self._fetch(doc["uri"])
                )
                return self._window(text, args)
            if action == "calculator":
                return str(calculate(str(args.get("expression", ""))))
        except (
            FetchError,
            KeyError,
            ValueError,
            TypeError,
            ZeroDivisionError,
            OverflowError,
            SyntaxError,
        ) as exc:
            return f"error: {type(exc).__name__}: {exc}"
        except FileNotFoundError as exc:
            return f"error: not found: {exc}"
        return f"error: unknown tool {action!r} (available: {', '.join(TOOLS)})"


def documents(params: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    for i, d in enumerate(params.get("documents") or (), 1):
        if isinstance(d, str):
            is_uri = d.startswith("data:") or "://" in d.split("\n", 1)[0][:200]
            d = {"uri": d} if is_uri else {"text": d}
        out.append(
            {"name": d.get("name") or f"doc-{i}", "text": d.get("text"), "uri": d.get("uri")}
        )
    return out


# -------------------------------------------------------------------------------- verdict


def _number(value: Any, lo: float, hi: float, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{what} must be a number")
    number = float(value)
    if not math.isfinite(number) or not lo <= number <= hi:
        raise ValueError(f"{what} must be a finite number in [{lo}, {hi}]")
    return number


def verdict_items(parsed: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    """Partial scores of a reply, or None if it carries no verdict."""
    if isinstance(parsed.get("scores"), list):
        raw = [(s.get("name"), s.get("score"), s.get("weight")) for s in parsed["scores"]]
    elif isinstance(parsed.get("requirements"), list):
        raw = [(s.get("id"), s.get("score"), s.get("weight")) for s in parsed["requirements"]]
    elif isinstance(parsed.get("evaluations"), list):
        raw = []
        for e in parsed["evaluations"]:
            v = normalize_verdict(e.get("verdict"))
            raw.append(
                (
                    e.get("rubric_id") or e.get("property"),
                    None if v == NA else float(v == "yes"),
                    None,
                )
            )
    else:
        return None
    items, seen = [], set()
    for name, score, weight in raw:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("metric name cannot be blank")
        name = name.strip()
        if name in seen:
            raise ValueError(f"duplicate metric name in scores: {name}")
        seen.add(name)
        items.append(
            {
                "name": name,
                "score": None if score is None else _number(score, 0, 1, f"{name}.score"),
                "weight": 1.0 if weight is None else _number(weight, 0, 1000, f"{name}.weight"),
            }
        )
    if not items:
        raise ValueError("scores must be non-empty")
    return items


def derive(items: list[dict[str, Any]], checks: Sequence[Mapping[str, Any]]) -> tuple[list, Any]:
    """Rows and weighted score; trusted checks fix weights, and unscored checks count as 0."""
    if checks:
        by_name = {i["name"].upper(): i for i in items}
        # Models often echo the whole listing line ("REQ-1: <text>") as the name.
        for i in items:
            by_name.setdefault(i["name"].split(":", 1)[0].strip().upper(), i)
        rows = []
        for c in checks:
            item = by_name.get(str(c["id"]).upper())
            weight = (
                c["weight"] if c.get("weight") is not None else (item or {}).get("weight", 1.0)
            )
            score = item["score"] if item else 0.0
            rows.append(
                {"name": c["id"], "score": score, "weight": weight, "assessed": item is not None}
            )
    else:
        rows = [{**i, "assessed": True} for i in items]
    applicable = [r for r in rows if r["score"] is not None]
    total = sum(r["weight"] for r in applicable)
    if not applicable:
        return rows, None
    if total == 0:
        raise ValueError("cannot derive overall score: the weights of all scores sum to zero")
    return rows, round(sum(r["weight"] * r["score"] for r in applicable) / total, 3)


# --------------------------------------------------------------------------------- runner


def _criteria(metric_id: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    if metric_id == "custom.rubric":
        return rubric_list(params)
    return requirement_list(params)


def _with_criteria(template: PromptTemplate, listing: str) -> PromptTemplate:
    safe = listing.replace("{", "{{").replace("}", "}}")
    parts = {k: v.replace("<<criteria>>", safe) for k, v in template.parts.items()}
    return dataclasses.replace(template, parts=parts)


def _listing(checks: Sequence[Mapping[str, Any]]) -> str:
    return "\n".join(f"{c['id']}: {c['text']}" for c in checks)


def templates(params: Mapping[str, Any], checks: Sequence[Mapping[str, Any]]) -> tuple:
    version = params.get("prompt_version", "v1")
    plan = agentic_plan.VERSIONS[version]
    given = _listing(checks) or "No criteria list was provided: extract the requirements."
    return (
        _with_criteria(plan, given),
        agentic_inspect.VERSIONS[version],
        agentic_verdict.VERSIONS[version],
    )


def prompt_hash(params: Mapping[str, Any], checks: Sequence[Mapping[str, Any]]) -> str:
    return hash_obj([t.hash() for t in templates(params, checks)])


async def _run_once(
    metric: JudgeMetric, case: EvalCase, ctx: MetricContext, checks: list, run: int
) -> tuple[dict[str, Any], dict]:
    ws = _Workspace(case, ctx, documents(ctx.params))
    plan_t, inspect_t, verdict_t = templates(ctx.params, checks)
    inventory = ws.inventory()
    input_text = (
        (content_text(case.input) or as_text(case.input)) if case.input is not None else ""
    )
    tag = f":agentic:r{run}"
    samples, provenance = await metric.ask(
        case,
        ctx,
        plan_t,
        "agentic",
        {
            "input": input_text,
            "actual_output": ws.output_text()[:PREVIEW_CHARS],
            "inventory": inventory,
        },
        tag=f"{tag}:plan",
    )
    planned = samples[0].parsed
    if not checks:  # extracted by the judge (untrusted weights are kept from the verdict)
        checks = [
            {
                "id": str(c.get("name") or c.get("id") or f"REQ-{i}"),
                "text": str(c.get("description", "")),
                "weight": None,
            }
            for i, c in enumerate(planned.get("checks") or (), 1)
            if isinstance(c, Mapping)
        ]
    plan_text = str(planned.get("plan") or "") + ("\n" + _listing(checks) if checks else "")
    inspect_t = _with_criteria(inspect_t, _listing(checks) or "(see plan)")
    verdict_t = _with_criteria(verdict_t, _listing(checks) or "(see plan)")
    max_steps = int(ctx.params.get("max_steps", DEFAULT_MAX_STEPS))
    log: list[dict[str, Any]] = []
    transcript: list[str] = []
    items, reply = None, None
    for step in range(1, max_steps + 1):
        fields = {
            "input": input_text,
            "inventory": inventory,
            "plan": plan_text,
            "observations": "\n\n".join(transcript) or "(none yet)",
            "step": f"{step} of {max_steps}",
        }
        samples, _ = await metric.ask(
            case, ctx, inspect_t, "agentic", fields, tag=f"{tag}:s{step}"
        )
        reply = samples[0].parsed
        action = str(reply.get("action") or "").strip()
        if not action or action in FINISH:
            try:
                items = verdict_items(reply)
            except ValueError:
                items = None
            log.append({"step": step, "action": action or "finish"})
            break
        args = reply.get("args") if isinstance(reply.get("args"), Mapping) else {}
        observation = await ws.run(action, args)
        log.append({"step": step, "action": action, "args": dict(args), "chars": len(observation)})
        transcript.append(f"[{step}] {action}({canonical_json(dict(args))}) ->\n{observation}")
    else:
        log.append({"step": max_steps, "action": "step_cap"})
    problems = "(none)"
    for attempt in range(2):
        if items is not None:
            break
        fields = {
            "input": input_text,
            "plan": plan_text,
            "observations": "\n\n".join(transcript) or "(none)",
            "problems": problems,
        }
        samples, _ = await metric.ask(
            case, ctx, verdict_t, "agentic", fields, tag=f"{tag}:v{attempt}"
        )
        reply = samples[0].parsed
        try:
            items = verdict_items(reply)
            if items is None:
                raise ValueError("the reply has no scores list")
        except ValueError as exc:
            items, problems = None, f"Invalid verdict: {exc}"
    if items is None or reply is None:
        raise JudgeParseError(f"agentic judge produced no valid verdict ({problems})")
    rows, score = derive(items, checks)
    result = {
        "score": score,
        "rows": rows,
        "summary": reply.get("summary"),
        "issues": [str(i) for i in reply.get("issues") or ()],
        "steps": log,
        "checks_source": "param" if _criteria_given(ctx) else "extracted",
    }
    return result, provenance


def _criteria_given(ctx: MetricContext) -> bool:
    return bool(ctx.params.get("rubrics") or ctx.params.get("requirements"))


async def evaluate_agentic(metric: JudgeMetric, case: EvalCase, ctx: MetricContext) -> Outcome:
    if not case.has("output") and not case.has("artifacts"):
        return Outcome(
            None,
            status="skipped",
            error=ErrorInfo("missing_input", "the agentic judge needs an output or artifacts"),
        )
    checks = _criteria(metric.spec.id, ctx.params)
    k = int(
        ctx.params.get("samples", getattr(ctx.judge.config, "samples", 1) if ctx.judge else 1) or 1
    )
    one = dataclasses.replace(ctx, params={**ctx.params, "samples": 1})
    runs, provenance = [], {}
    for run in range(k):
        result, prov = await _run_once(metric, case, one, list(checks), run)
        runs.append(result)
        provenance = provenance or dict(prov)
    scored = [r["score"] for r in runs if r["score"] is not None]
    if not scored:
        chosen, score = runs[0], None
    else:
        score = median_low(scored)
        chosen = next(r for r in runs if r["score"] == score)
    provenance.update(samples=k, prompt_hash=prompt_hash(ctx.params, checks))
    details: dict[str, Any] = {
        "scores": chosen["rows"],
        "issues": chosen["issues"],
        "steps": chosen["steps"],
        "checks_source": chosen["checks_source"],
    }
    if k > 1:
        details["sample_scores"] = [r["score"] for r in runs]
    reason = chosen["summary"] or ("no check is applicable" if score is None else None)
    return Outcome(score=score, reason=reason, details=details, provenance=provenance)


def _lock_hash(metric_id: str):
    return lambda params: prompt_hash(params, _criteria(metric_id, params))


for _cls in (Rubric, RequirementsCompliance):
    register_method(
        _cls.spec.id,
        METHOD,
        evaluate_agentic,
        params=PARAMS,
        prompt_hash=_lock_hash(_cls.spec.id),
    )
