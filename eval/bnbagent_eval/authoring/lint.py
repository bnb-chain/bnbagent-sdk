"""Suite lint: is this suite fit to be negotiated and arbitrated?

``lint_suite`` never raises for style problems; it returns issues with a level:
- ``error``: the suite cannot be resolved, or is unfit for its intended use;
- ``warning``: the suite works but is weak evidence in a dispute;
- ``info``: suggestions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from ..engine.resolve import ResolvedSuite, resolve_suite
from ..exceptions import EvalError
from ..metrics.base import Kind
from ..types.suite import LockEntry, Suite

Level = Literal["error", "warning", "info"]
Use = Literal["dev", "negotiated", "service"]

_VAGUE = re.compile(r"\b(good|nice|appropriate|reasonable|proper|well|decent|ok|okay)\b", re.I)


@dataclass(frozen=True)
class LintIssue:
    level: Level
    code: str
    message: str
    metric: str | None = None

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "code": self.code,
            "message": self.message,
            "metric": self.metric,
        }


def lint_suite(suite: Suite, *, use: Use = "negotiated") -> list[LintIssue]:
    """Check ``suite`` for its intended ``use`` (dev, negotiated job/registration, or service)."""
    issues: list[LintIssue] = []
    try:
        resolved = resolve_suite(suite)
    except EvalError as exc:
        return [LintIssue("error", "unresolvable", str(exc))]
    strict = use in ("negotiated", "service")
    judge_metrics = [m for m in resolved.metrics if m.method.kind is Kind.JUDGE]
    rule_metrics = [m for m in resolved.metrics if m.method.kind is Kind.RULE]

    for rm in resolved.metrics:
        key, cfg = rm.config.key, rm.config
        threshold = cfg.threshold if cfg.threshold is not None else rm.spec.default_threshold
        value_set = [
            name
            for name in ("value_min", "value_max", "value_target")
            if getattr(cfg, name) is not None
        ]
        if value_set and not rm.spec.value_limits:
            issues.append(
                LintIssue(
                    "warning",
                    "value_limits_ignored",
                    f"{', '.join(value_set)} ignored: {rm.spec.id} does not score a value "
                    "(use threshold)",
                    key,
                )
            )
        value_limited = rm.spec.value_limits and any(
            v is not None for v in (cfg.value_min, cfg.value_max)
        )
        if threshold is None and not value_limited and cfg.required:
            issues.append(
                LintIssue(
                    "error" if strict else "warning",
                    "required_without_threshold",
                    "a required metric needs a threshold or value limits",
                    key,
                )
            )
        if use == "service" and not rm.method.service_allowed:
            issues.append(
                LintIssue(
                    "error",
                    "not_service_allowed",
                    f"method {rm.method.name!r} is not allowed on the eval service",
                    key,
                )
            )
        if rm.method.kind is Kind.JUDGE and strict:
            samples = cfg.params.get("samples", suite.judge.samples if suite.judge else 1)
            if samples < 3:
                issues.append(
                    LintIssue(
                        "warning",
                        "few_samples",
                        "use samples >= 3 for judge metrics in disputes",
                        key,
                    )
                )
        for rubric in cfg.params.get("rubrics", []) or []:
            text = rubric.get("text", "") if isinstance(rubric, dict) else str(rubric)
            if len(text) < 15 or _VAGUE.search(text):
                issues.append(
                    LintIssue(
                        "warning",
                        "ambiguous_criterion",
                        f"rubric item is vague or too short: {text!r}",
                        key,
                    )
                )

    if judge_metrics and suite.judge is None and strict:
        issues.append(
            LintIssue(
                "error",
                "judge_not_pinned",
                "judge metrics in a negotiated suite need a pinned judge "
                "(suite.judge), so every party evaluates with the same model",
            )
        )
    if (
        judge_metrics
        and suite.judge is not None
        and suite.judge.temperature not in (0.0, None)
        and strict
    ):
        issues.append(
            LintIssue(
                "warning", "judge_temperature", "pin temperature 0 for reproducible judge verdicts"
            )
        )
    if strict and not rule_metrics:
        issues.append(
            LintIssue(
                "warning",
                "no_deterministic_metric",
                "add at least one deterministic metric: they replay exactly",
            )
        )
    if strict and not any(m.config.required for m in resolved.metrics):
        issues.append(
            LintIssue(
                "warning", "no_required_metric", "mark the metrics that must pass as required"
            )
        )
    if strict and suite.lock is None:
        issues.append(
            LintIssue(
                "warning",
                "unlocked",
                "pin metric and prompt versions with a lock (bnbagent-eval lint --lock)",
            )
        )
    if strict and judge_metrics and suite.pass_rule.inconclusive_band == 0.0:
        issues.append(
            LintIssue(
                "info",
                "no_inconclusive_band",
                "an inconclusive_band avoids hard verdicts at the threshold",
            )
        )
    return issues


def lock_suite(suite: Suite) -> Suite:
    """Return ``suite`` with a lock pinning the installed metric and prompt versions."""
    resolved: ResolvedSuite = resolve_suite(Suite(**{**_fields(suite), "lock": None}))
    lock: dict[str, LockEntry] = dict(resolved.lock)
    return Suite(**{**_fields(suite), "lock": lock})


def _fields(suite: Suite) -> dict:
    return {f: getattr(suite, f) for f in suite.__dataclass_fields__}
