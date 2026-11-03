"""``code.syntax_valid``: the code deliverable parses.

Python (``ast``) and JSON are parsed in-process (nothing is executed). JavaScript (``node
--check``), TypeScript (``tsc``, syntactic ``TS1xxx`` diagnostics only) and Solidity (``solc
--stop-after parsing``) need the parser CLI on ``PATH``; without it the result is an error with
code ``missing_extra``. The CLIs parse only; they still run under the sandbox's limits.

The language is the ``language`` param, else the one inferred from the fence tag, extension or
media type, else Python. A source inferred to be in another language (e.g. a ``bash`` fence) is
listed as unsupported and not scored; with no scorable source the result is skipped.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
import shutil
from typing import Any

from ...types.case import EvalCase
from ...types.result import ErrorInfo
from ..base import BaseMetric, Kind, MethodSpec, MetricContext, MetricSpec, Outcome
from ..registry import register
from . import sandbox
from ._common import LANGUAGES, Source, sources

CLI_LIMITS = sandbox.Limits(timeout_s=30.0, memory_mb=None, nproc=256)
_TS_SYNTAX = re.compile(r"error TS1\d{3}:")
_CLIS = {"javascript": "node", "typescript": "tsc", "solidity": "solc"}


class _MissingParser(Exception):
    pass


def check_python(text: str) -> dict[str, Any] | None:
    try:
        ast.parse(text, filename="<code>")
    except SyntaxError as exc:
        return {"error": f"{type(exc).__name__}: {exc.msg}", "line": exc.lineno, "col": exc.offset}
    except (ValueError, RecursionError, MemoryError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    return None


def check_json(text: str) -> dict[str, Any] | None:
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        return {"error": exc.msg, "line": exc.lineno, "col": exc.colno}
    except (ValueError, RecursionError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    return None


def _cli(language: str, text: str) -> dict[str, Any] | None:
    tool = _CLIS[language]
    exe = shutil.which(tool)
    if exe is None:
        raise _MissingParser(f"{language} syntax checks need `{tool}` on PATH")

    def go(argv: list[str], name: str) -> sandbox.SandboxResult:
        return sandbox.run(
            [exe, *argv, name],
            {name: text},
            limits=CLI_LIMITS,
            require_isolation=False,
        )

    if language == "javascript":
        res = go(["--check"], "code.js")
        if res.returncode != 0:  # ES module syntax (import / export) needs .mjs
            module = go(["--check"], "code.mjs")
            if module.returncode == 0:
                return None
        errors = res.stderr
    elif language == "typescript":
        flags = ["--noEmit", "--noResolve", "--noLib", "--skipLibCheck", "--pretty", "false"]
        res = go([*flags, "--target", "esnext", "--module", "esnext"], "code.ts")
        lines = [ln for ln in res.stdout.splitlines() if _TS_SYNTAX.search(ln)]
        if res.timed_out:
            return {"error": "parser timed out"}
        return {"error": "\n".join(lines)[:2000]} if lines else None
    else:
        res = go(["--stop-after", "parsing"], "code.sol")
        errors = res.stderr or res.stdout
    if res.timed_out:
        return {"error": "parser timed out"}
    return None if res.returncode == 0 else {"error": errors.strip()[-2000:]}


async def check(language: str, text: str) -> dict[str, Any] | None:
    """``None`` if ``text`` parses as ``language``, else error details."""
    if language == "python":
        return check_python(text)
    if language == "json":
        return check_json(text)
    return await asyncio.to_thread(_cli, language, text)


@register
class SyntaxValid(BaseMetric):
    spec = MetricSpec(
        id="code.syntax_valid",
        category="code",
        description="The code deliverable (output or artifacts) parses in its language.",
        methods=(
            MethodSpec(
                "rule",
                Kind.RULE,
                optional_inputs=frozenset({"output", "artifacts"}),
                service_allowed=False,
            ),
        ),
        params_schema={
            "type": "object",
            "properties": {"language": {"enum": list(LANGUAGES)}},
            "additionalProperties": False,
        },
        default_threshold=1.0,
        version="3",  # v2: an empty source is invalid; v3: unsupported languages are not scored
        sources=("bnbagent-eval:new",),
    )

    async def _evaluate_rule(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        rows: list[dict[str, Any]] = []
        items: list[Source] = await sources(case, ctx)
        for src in items:
            language = ctx.params.get("language") or src.language or "python"
            if language not in LANGUAGES:
                rows.append({"name": src.name, "language": language, "valid": None})
                rows[-1].update({"error": "unsupported language"})
                continue
            if not src.text.strip():  # nothing to parse is not valid code (ast.parse("") is)
                rows.append({"name": src.name, "language": language, "valid": False})
                rows[-1].update({"error": "empty source"})
                continue
            try:
                problem = await check(language, src.text)
            except _MissingParser as exc:
                return Outcome(None, status="error", error=ErrorInfo("missing_extra", str(exc)))
            except sandbox.SandboxUnavailable as exc:
                return Outcome(
                    None, status="error", error=ErrorInfo("sandbox_unavailable", str(exc))
                )
            rows.append({"name": src.name, "language": language, "valid": problem is None})
            if problem:
                rows[-1].update(problem)
        details: dict[str, Any] = {"sources": rows} if len(rows) > 1 else rows[0]
        scored = [r for r in rows if r["valid"] is not None]
        if not scored:
            langs = sorted({r["language"] for r in rows})
            return Outcome(
                None,
                status="skipped",
                error=ErrorInfo("missing_input", f"no source in a supported language: {langs}"),
                details=details,
            )
        valid = sum(1 for r in scored if r["valid"])
        bad = [r for r in scored if not r["valid"]]
        reason = None
        if bad:
            reason = f"{bad[0]['name']}: {bad[0].get('error', 'invalid')}"[:300]
        return Outcome(score=valid / len(scored), reason=reason, details=details)
