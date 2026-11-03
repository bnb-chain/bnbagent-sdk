"""The five stages of the agentic pipeline (ETK ``agentic.stages``).

1. taxonomy (judge, with a mechanical fallback), 2. scenario planning in batches of 15 with
coverage feedback, 3. generation of one case per scenario (tool calls validated against the
schemas and against the planned tools), 4. augmentation (one call per case x strategy),
5. stratified review sampling. Prompts: ``judges.prompts.generation.agentic``.
"""

from __future__ import annotations

import json
import logging
import math
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any

from ...exceptions import JudgeError, JudgeNonRetryable, JudgeParseError
from ...judges.base import JudgeModel
from ...judges.prompts.generation.agentic import (
    AUGMENT_V1,
    CASE_V1,
    SCENARIOS_V1,
    TAXONOMY_V1,
)
from ...types.trajectory import ToolCall
from .._llm import GenerationError, Prompt, ask_json, bounded_gather
from .models import (
    AUGMENTATION,
    SCENARIO_SOURCES,
    STRATEGIES,
    ExtractedConversation,
    GeneratedCase,
    Provenance,
    RawCase,
    Scenario,
    SeedExample,
    Taxonomy,
    TaxonomyConfig,
    ToolSpec,
    TraceCoverage,
)
from .validator import ToolValidator, check_tool_calls

logger = logging.getLogger(__name__)

SCENARIO_BATCH_SIZE = 15  # ETK
MAX_CONSECUTIVE_BATCH_FAILURES = 3  # ETK
_INTENT_PREVIEW_CHARS = 80  # ETK
_ENUM_ERROR_MARKER = "must be one of"  # our validator's wording for an enum violation

TraceExampleFinder = Callable[[Sequence[str], str], list[SeedExample]]


def _dump(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


def _numbered(items: Sequence[str]) -> str:
    return "\n".join(f"{i + 1}. {item}" for i, item in enumerate(items))


def _tools_json(tools: Sequence[ToolSpec] | None) -> str | None:
    return _dump([t.to_prompt() for t in tools]) if tools else None


# ------------------------------------------------------------------------- 1. taxonomy


def build_default_taxonomy(
    tools: Sequence[ToolSpec] | None, focus_areas: Sequence[str] | None, axes: TaxonomyConfig
) -> Taxonomy:
    """Every tool alone and every pair; no gaps (ETK ``build_default_taxonomy``)."""
    names = [t.name for t in tools or ()]
    combos: list[tuple[str, ...]] = [(n,) for n in names]
    combos += [(a, b) for i, a in enumerate(names) for b in names[i + 1 :]]
    return Taxonomy(
        tools=tuple(names),
        tool_combinations=tuple(combos) or ((),),
        turn_counts=axes.turn_counts,
        difficulty=axes.difficulty,
        topics=tuple(focus_areas[:5]) if focus_areas else ("general",),
        outcomes=axes.outcomes,
    )


async def build_taxonomy(
    judge: JudgeModel,
    *,
    system_prompt: str | None,
    tools: Sequence[ToolSpec] | None,
    focus_areas: Sequence[str] | None,
    axes: TaxonomyConfig,
    trace_coverage: TraceCoverage | None = None,
) -> Taxonomy:
    tools_json = _tools_json(tools)
    material = {
        "system_prompt": system_prompt,
        "tools": tools_json,
        "focus": list(focus_areas or ()),
        "axes": [list(axes.turn_counts), list(axes.difficulty), list(axes.outcomes)],
        "coverage": repr(trace_coverage),
    }
    prompt = Prompt(TAXONOMY_V1, "taxonomy", material)
    trace = ""
    if trace_coverage is not None:
        lines = (
            f"- Observed tools: {', '.join(trace_coverage.observed_tools) or 'none'}\n"
            f"- Observed tool combinations: "
            f"{json.dumps([list(c) for c in trace_coverage.observed_combinations])}\n"
            f"- Outcome distribution: success={trace_coverage.success}, "
            f"failure={trace_coverage.failure}, abandonment={trace_coverage.abandonment}"
        )
        trace = prompt.section("trace_section", lines, "user_trace_coverage")
    fields = {
        "system_prompt_section": prompt.section(
            "system_prompt_section", system_prompt, "user_system_prompt"
        ),
        "tools_section": prompt.section("tools_section", tools_json, "user_tools"),
        "focus_section": prompt.section(
            "focus_section", _numbered(focus_areas or ()), "user_focus_areas"
        ),
        "turn_counts": json.dumps(list(axes.turn_counts)),
        "difficulty": json.dumps(list(axes.difficulty)),
        "outcomes": json.dumps(list(axes.outcomes)),
        "trace_section": trace,
    }
    known = {t.name for t in tools or ()}

    def check(obj: dict[str, Any]) -> Taxonomy:
        taxonomy = Taxonomy.from_prompt(obj, axes)
        if known:  # combinations naming unknown tools could never validate in generation
            combos = tuple(c for c in taxonomy.tool_combinations if set(c) <= known)
            taxonomy = replace(
                taxonomy,
                tools=tuple(t for t in taxonomy.tools if t in known) or tuple(known),
                tool_combinations=combos or tuple((n,) for n in sorted(known)),
            )
        return taxonomy

    return await ask_json(judge, prompt, prompt.request(fields), check=check)


# -------------------------------------------------------------------------- 2. planning


def _coverage_counts(scenarios: Sequence[Scenario], taxonomy: Taxonomy) -> dict[str, str]:
    turns = {t: 0 for t in taxonomy.turn_counts}
    difficulty = {d: 0 for d in taxonomy.difficulty}
    outcomes = {o: 0 for o in taxonomy.outcomes}
    combos = {",".join(sorted(c)): 0 for c in taxonomy.tool_combinations}
    for s in scenarios:
        if s.turns in turns:
            turns[s.turns] += 1
        if s.difficulty in difficulty:
            difficulty[s.difficulty] += 1
        if s.outcome in outcomes:
            outcomes[s.outcome] += 1
        key = ",".join(sorted(s.tools_involved))
        if key in combos:
            combos[key] += 1
    return {
        "turn_counts": ", ".join(f"{k}-turn ({v})" for k, v in turns.items()),
        "difficulty": ", ".join(f"{k} ({v})" for k, v in difficulty.items()),
        "outcomes": ", ".join(f"{k} ({v})" for k, v in outcomes.items()),
        "combinations": ", ".join(f"[{k}] ({v})" for k, v in combos.items()),
    }


def _render_failure(c: ExtractedConversation) -> str:
    tools = ", ".join(c.tool_sequence())
    intent = c.turns[0].user_message[:_INTENT_PREVIEW_CHARS] if c.turns else "N/A"
    return f'- [{c.outcome}] tools: [{tools}], turns: {len(c.turns)}, intent: "{intent}..."'


def _trace_section(
    prompt: Prompt,
    coverage: TraceCoverage | None,
    failures: Sequence[ExtractedConversation],
    total_failures: int,
) -> str:
    """ETK ``build_trace_context``: outcome rates and a diverse sample of real failures."""
    if coverage is None and not failures:
        return ""
    extra = prompt.template.extra
    coverage_text = failures_text = ""
    if coverage is not None:
        total = coverage.success + coverage.failure + coverage.abandonment
        coverage_text = str(extra["trace_coverage"]).format(
            failure=coverage.failure,
            abandonment=coverage.abandonment,
            failure_rate=f"{coverage.failure / total * 100:.1f}" if total else "0",
            abandonment_rate=f"{coverage.abandonment / total * 100:.1f}" if total else "0",
            observed_tools=prompt.fence(
                "user_observed_tools", ", ".join(coverage.observed_tools) or "none"
            ),
        )
    if failures:
        failures_text = str(extra["trace_failures"]).format(
            shown=len(failures),
            total=total_failures,
            content=prompt.fence(
                "user_trace_failures", "\n".join(_render_failure(f) for f in failures)
            ),
        )
    return str(extra["trace_section"]).format(coverage=coverage_text, failures=failures_text)


def _duplicates(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    repeated: list[str] = []
    for v in values:
        if v in seen and v not in repeated:
            repeated.append(v)
        seen.add(v)
    return repeated


def _parse_scenarios(
    obj: Mapping[str, Any], taxonomy: Taxonomy, tool_names: Sequence[str], count: int, start: int
) -> list[Scenario]:
    raw = list(obj["scenarios"])[:count]
    if not raw:
        raise JudgeParseError("LLM returned an empty scenarios array")
    errors: list[str] = []
    scenarios: list[Scenario] = []
    known = set(tool_names)
    for i, item in enumerate(raw):
        sid = f"scenario-{start + i:03d}"  # renumbered: ids must be unique across batches
        tools = [str(t) for t in item.get("toolsInvolved") or ()]
        label = str(item.get("id") or sid)
        if _duplicates(tools):
            errors.append(
                f"{label}: duplicate tools in toolsInvolved [{', '.join(_duplicates(tools))}]"
            )
        if known and set(tools) - known:
            errors.append(f"{label}: unknown tools [{', '.join(sorted(set(tools) - known))}]")
        if item["turns"] not in taxonomy.turn_counts:
            errors.append(
                f"{label}: turns={item['turns']} not in taxonomy {list(taxonomy.turn_counts)}"
            )
        if item["outcome"] not in taxonomy.outcomes:
            errors.append(
                f'{label}: outcome="{item["outcome"]}" not in taxonomy {list(taxonomy.outcomes)}'
            )
        if item["difficulty"] not in taxonomy.difficulty:
            errors.append(
                f'{label}: difficulty="{item["difficulty"]}" not in taxonomy '
                f"{list(taxonomy.difficulty)}"
            )
        source = item.get("source") or "gap_fill"
        if source not in SCENARIO_SOURCES:
            errors.append(f'{label}: source="{source}" must be one of {list(SCENARIO_SOURCES)}')
        turns = int(item["turns"])
        scenarios.append(
            Scenario(
                id=sid,
                description=str(item["description"]),
                turns=turns,
                difficulty=str(item["difficulty"]),
                outcome=str(item["outcome"]),
                tools_involved=tuple(tools),
                topics=tuple(str(t) for t in item.get("topics") or ()),
                type="single_turn" if turns == 1 else "multi_turn",
                source=str(source),
                priority=int(item.get("priority", 5)),
            )
        )
    if errors:
        raise JudgeParseError(f"Invalid scenarios: {'; '.join(errors)}")
    return scenarios


async def _sample_batch(
    judge: JudgeModel,
    *,
    taxonomy: Taxonomy,
    count: int,
    start: int,
    focus_areas: Sequence[str] | None,
    previous: Sequence[Scenario],
    tool_names: Sequence[str],
    trace_coverage: TraceCoverage | None,
    failures: Sequence[ExtractedConversation],
    total_failures: int,
) -> list[Scenario]:
    taxonomy_json = _dump(taxonomy.to_prompt())
    material = {"taxonomy": taxonomy_json, "count": count, "start": start, "prev": len(previous)}
    prompt = Prompt(SCENARIOS_V1, f"scenarios-{start:03d}", material)
    coverage = ""
    if previous:
        lines = "\n".join(
            f'- {s.id}: "{s.description}" ({s.turns}-turn, {s.difficulty}, {s.outcome}, '
            f"tools: [{', '.join(s.tools_involved)}])"
            for s in previous
        )
        coverage = str(SCENARIOS_V1.extra["coverage_section"]).format(
            scenarios=prompt.fence("previous_scenarios", lines),
            **_coverage_counts(previous, taxonomy),
        )
    turns = taxonomy.turn_counts
    fields = {
        "taxonomy": prompt.fence("user_taxonomy", taxonomy_json),
        "focus_section": prompt.section(
            "focus_section", _numbered(focus_areas or ()), "user_focus_areas"
        ),
        "coverage_section": coverage,
        "count": str(count),
        "start_id": f"{start:03d}",
        "min_turns": str(min(turns)),
        "max_turns": str(max(turns)),
        "difficulty_values": " | ".join(f'"{d}"' for d in taxonomy.difficulty),
        "outcome_values": " | ".join(f'"{o}"' for o in taxonomy.outcomes),
        "turn_counts": json.dumps(list(turns)),
        "difficulty_list": ", ".join(taxonomy.difficulty),
        "outcome_list": ", ".join(taxonomy.outcomes),
        "trace_section": _trace_section(prompt, trace_coverage, failures, total_failures),
    }

    def retry(error: Exception, previous_text: str) -> str:
        return str(SCENARIOS_V1.extra["retry"]).format(
            error=error,
            previous=previous_text,
            allowed_turns=", ".join(str(t) for t in turns),
            allowed_outcomes=", ".join(f'"{o}"' for o in taxonomy.outcomes),
            allowed_difficulty=", ".join(f'"{d}"' for d in taxonomy.difficulty),
            allowed_tools=", ".join(tool_names) or "none",
        )

    return await ask_json(
        judge,
        prompt,
        prompt.request(fields),
        check=lambda obj: _parse_scenarios(obj, taxonomy, tool_names, count, start),
        retry=retry,
        wrap="scenarios",
    )


async def sample_scenarios(
    judge: JudgeModel,
    *,
    taxonomy: Taxonomy,
    total: int,
    focus_areas: Sequence[str] | None,
    tool_names: Sequence[str],
    trace_coverage: TraceCoverage | None = None,
    failures: Sequence[ExtractedConversation] = (),
    total_failures: int = 0,
) -> list[Scenario]:
    """``total`` scenarios in batches of 15, sorted by priority (stable)."""
    out: list[Scenario] = []
    consecutive = 0
    for batch in range(math.ceil(total / SCENARIO_BATCH_SIZE)):
        remaining = total - len(out)
        if remaining <= 0:
            break
        try:
            scenarios = await _sample_batch(
                judge,
                taxonomy=taxonomy,
                count=min(SCENARIO_BATCH_SIZE, remaining),
                start=len(out) + 1,
                focus_areas=focus_areas,
                previous=out if batch > 0 else (),
                tool_names=tool_names,
                trace_coverage=trace_coverage,
                failures=failures,
                total_failures=total_failures,
            )
        except JudgeNonRetryable:
            raise
        except JudgeError as exc:
            consecutive += 1
            logger.warning("scenario batch %d failed: %s", batch + 1, exc)
            if not out:
                raise GenerationError(f"scenario planning failed: {exc}") from exc
            if consecutive >= MAX_CONSECUTIVE_BATCH_FAILURES:
                break
            continue
        consecutive = 0
        out.extend(scenarios)
    if not out:
        raise GenerationError("Failed to sample any scenarios from the taxonomy")
    return sorted(out, key=lambda s: s.priority)


# ------------------------------------------------------------------------ 3. generation


def _enum_hint(tools: Sequence[ToolSpec] | None) -> str:
    entries = []
    for tool in tools or ():
        props = (tool.parameters or {}).get("properties")
        if not isinstance(props, Mapping):
            continue
        for name, schema in props.items():
            if isinstance(schema, Mapping) and isinstance(schema.get("enum"), list):
                values = ", ".join(str(v) for v in schema["enum"])
                entries.append(f"{tool.name}.{name}: [{values}]")
    return str(CASE_V1.extra["enum_hint"]).format(entries="; ".join(entries)) if entries else ""


def _scenario_tool_match(calls: Sequence[ToolCall], scenario: Scenario) -> list[str]:
    errors = []
    generated = {c.name for c in calls}
    planned = ", ".join(scenario.tools_involved)
    missing = [t for t in scenario.tools_involved if t not in generated]
    if missing:
        errors.append(
            f"Missing tools from scenario: [{', '.join(missing)}]. Expected: [{planned}]"
        )
    extra = sorted(generated - set(scenario.tools_involved))
    if extra:
        errors.append(
            f"Extra tools not in scenario: [{', '.join(extra)}]. Expected only: [{planned}]"
        )
    return errors


def _shared_sections(
    prompt: Prompt, system_prompt: str | None, tools: Sequence[ToolSpec] | None
) -> dict[str, str]:
    return {
        "system_prompt_section": prompt.section(
            "system_prompt_section", system_prompt, "user_system_prompt"
        ),
        "tools_section": prompt.section("tools_section", _tools_json(tools), "user_tools"),
    }


async def generate_case(
    judge: JudgeModel,
    scenario: Scenario,
    *,
    system_prompt: str | None,
    tools: list[ToolSpec] | None,
    seed_examples: Sequence[SeedExample] | None,
    validator: ToolValidator,
) -> GeneratedCase:
    scenario_json = _dump(scenario.to_prompt())
    examples = _dump([e.to_prompt() for e in seed_examples]) if seed_examples else None
    material = {
        "scenario": scenario_json,
        "sp": system_prompt,
        "tools": _tools_json(tools),
        "ex": examples,
    }
    prompt = Prompt(CASE_V1, scenario.id, material)
    fields = {
        "scenario": prompt.fence("user_scenario", scenario_json),
        **_shared_sections(prompt, system_prompt, tools),
        "examples_section": prompt.section("examples_section", examples, "user_seed_examples"),
    }

    def check(obj: dict[str, Any]) -> RawCase:
        raw = RawCase.from_prompt(obj)
        calls = check_tool_calls(validator, raw.tool_calls, tools)
        if scenario.tools_involved:
            errors = _scenario_tool_match(calls, scenario)
            if errors:
                raise JudgeParseError(f"Scenario tool mismatch: {'; '.join(errors)}")
        if scenario.turns > 1 and (
            not isinstance(raw.input, tuple) or len(raw.input) != scenario.turns
        ):
            raise JudgeParseError(f"input must be an array of exactly {scenario.turns} turns")
        return raw

    def retry(error: Exception, previous: str) -> str:
        hint = _enum_hint(tools) if tools and _ENUM_ERROR_MARKER in str(error) else ""
        return str(CASE_V1.extra["retry"]).format(
            error=error,
            previous=previous,
            tools_involved=", ".join(scenario.tools_involved),
            enum_hint=hint,
        )

    raw = await ask_json(judge, prompt, prompt.request(fields), check=check, retry=retry)
    provenance = Provenance(
        source=scenario.source,
        scenario_id=scenario.id,
        plan_axis=",".join(scenario.tools_involved),
    )
    return GeneratedCase(
        raw.input, raw.response, raw.topics, raw.tool_calls, provenance, scenario=scenario
    )


async def generate_cases(
    judge: JudgeModel,
    scenarios: Sequence[Scenario],
    *,
    system_prompt: str | None,
    tools: list[ToolSpec] | None,
    seed_examples: Sequence[SeedExample] | None,
    validator: ToolValidator,
    concurrency: int,
    trace_examples: TraceExampleFinder | None = None,
) -> list[GeneratedCase]:
    """One case per scenario (scenario order); raises if every scenario failed (ETK)."""

    async def one(scenario: Scenario) -> GeneratedCase:
        found = trace_examples(scenario.tools_involved, scenario.outcome) if trace_examples else []
        return await generate_case(
            judge,
            scenario,
            system_prompt=system_prompt,
            tools=tools,
            seed_examples=found or seed_examples,
            validator=validator,
        )

    results = await bounded_gather(one, scenarios, concurrency)
    cases = [r for r in results if isinstance(r, GeneratedCase)]
    errors = [r for r in results if not isinstance(r, GeneratedCase)]
    for scenario, r in zip(scenarios, results, strict=True):
        if isinstance(r, Exception):
            logger.warning("generation failed for %s: %s: %s", scenario.id, type(r).__name__, r)
    if errors and not cases:
        raise GenerationError(
            f"Generation failed for all {len(errors)} scenarios; no test cases were produced"
        ) from errors[-1]
    return cases


# ---------------------------------------------------------------------- 4. augmentation


def filter_applicable_strategies(
    requested: Sequence[str], system_prompt: str | None, tools: Sequence[ToolSpec] | None
) -> list[str]:
    """``parameter_variation`` needs tools, ``negative_case`` a system prompt (ETK)."""
    active = []
    for strategy in requested:
        if strategy not in STRATEGIES:
            raise ValueError(f"unknown augmentation strategy {strategy!r}; one of {STRATEGIES}")
        if strategy == "parameter_variation" and not tools:
            logger.warning("skipping parameter_variation (requires tool definitions)")
        elif strategy == "negative_case" and not system_prompt:
            logger.warning("skipping negative_case (requires a system prompt to define scope)")
        elif strategy not in active:
            active.append(strategy)
    return active


async def augment_case(
    judge: JudgeModel,
    base: GeneratedCase,
    strategy: str,
    *,
    system_prompt: str | None,
    tools: list[ToolSpec] | None,
    validator: ToolValidator,
) -> GeneratedCase:
    base_json = _dump(base.to_prompt())
    material = {
        "base": base_json,
        "strategy": strategy,
        "sp": system_prompt,
        "tools": _tools_json(tools),
    }
    prompt = Prompt(AUGMENT_V1, f"{base.provenance.scenario_id}-{strategy}", material)
    fields = {
        "base_case": prompt.fence("user_base_case", base_json),
        **_shared_sections(prompt, system_prompt, tools),
        "strategy": str(AUGMENT_V1.extra["strategies"][strategy]),
    }

    def check(obj: dict[str, Any]) -> RawCase:
        raw = RawCase.from_prompt(obj)
        check_tool_calls(validator, raw.tool_calls, tools)  # no plan match: strategies change it
        return raw

    raw = await ask_json(judge, prompt, prompt.request(fields), check=check)
    parent = base.provenance
    provenance = Provenance(
        source=AUGMENTATION,
        scenario_id=f"{parent.scenario_id}-{strategy}",
        plan_axis=parent.plan_axis,
        augmentation_strategy=strategy,
        parent_scenario_id=parent.scenario_id,
    )
    return GeneratedCase(
        raw.input, raw.response, raw.topics, raw.tool_calls, provenance, scenario=base.scenario
    )


async def augment_cases(
    judge: JudgeModel,
    base_cases: Sequence[GeneratedCase],
    strategies: Sequence[str],
    *,
    system_prompt: str | None,
    tools: list[ToolSpec] | None,
    validator: ToolValidator,
    concurrency: int,
) -> list[GeneratedCase]:
    """One variation per (case, strategy); failures are logged, never fatal (ETK)."""
    jobs = [(case, s) for case in base_cases for s in strategies]

    async def one(job: tuple[GeneratedCase, str]) -> GeneratedCase:
        return await augment_case(
            judge, job[0], job[1], system_prompt=system_prompt, tools=tools, validator=validator
        )

    results = await bounded_gather(one, jobs, concurrency)
    for (case, strategy), r in zip(jobs, results, strict=True):
        if isinstance(r, Exception):
            logger.warning(
                "augmentation %s of %s failed: %s", strategy, case.provenance.scenario_id, r
            )
    return [r for r in results if isinstance(r, GeneratedCase)]


# -------------------------------------------------------------------- 5. review sampling


def apply_review_sampling(
    cases: Sequence[GeneratedCase], rate: float, rng: random.Random
) -> tuple[list[GeneratedCase], dict[str, tuple[int, int]]]:
    """Flag ``ceil(rate x group)`` cases per provenance source (ETK); stats ``{source:
    (flagged, total)}``. Any positive rate flags at least one case of every group."""
    if not 0.0 <= rate <= 1.0:
        raise ValueError("review_sample_rate must be in [0, 1]")
    groups: dict[str, list[int]] = {}
    for i, case in enumerate(cases):
        groups.setdefault(case.provenance.source, []).append(i)
    flagged: set[int] = set()
    stats = {}
    for source, indices in groups.items():
        k = 0 if rate <= 0 else min(len(indices), math.ceil(rate * len(indices)))
        flagged.update(rng.sample(indices, k))
        stats[source] = (k, len(indices))
    return [replace(c, requires_review=i in flagged) for i, c in enumerate(cases)], stats
