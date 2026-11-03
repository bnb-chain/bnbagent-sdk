"""``code.tests_pass``: share of the provided tests the code passes, run in the sandbox.

The code (output or a single artifact) is written as ``<module>.py`` and the ``tests`` param as
``test_<module>.py`` in a fresh work dir; :mod:`.sandbox` runs ``_python_runner.py`` there (no
network, CPU / wall / memory limits, no file access outside the work dir: see ``sandbox``). The
test list comes from the parent's AST scan of the (trusted) test source: top-level ``test*``
functions and ``Test*`` classes' ``test*`` methods; a test source without any is an assert-based
snippet counted as one test. Tests without a result (crash, timeout, ``os._exit``) fail.

Scoring is not tamper-proof against code written to subvert the in-process runner (it shares the
interpreter with the tests); the sandbox limits what such code can reach, not what it reports.
"""

from __future__ import annotations

import ast
import asyncio
import json
import secrets
from pathlib import Path
from typing import Any

from ...types.case import EvalCase
from ...types.result import ErrorInfo
from ..base import BaseMetric, Kind, MethodSpec, MetricContext, MetricSpec, Outcome
from ..registry import register
from . import sandbox
from ._common import sources

RUNNER = Path(__file__).with_name("_python_runner.py")
RUNNER_NAME = "_bnbeval_runner.py"
SNIPPET = "<snippet>"
MAX_TESTS = 500
MAX_REPORTED = 50
TAIL = 2000


def collect_tests(source: str) -> list[str]:
    """Test names in a pytest-style source; ``[SNIPPET]`` for an assert-based snippet."""
    tree = ast.parse(source)
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test"
        ):
            names.append(node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            methods = [
                n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            if any(m.name == "__init__" for m in methods):
                continue  # pytest does not collect classes with __init__
            names += [f"{node.name}::{m.name}" for m in methods if m.name.startswith("test")]
    if len(names) > MAX_TESTS:
        raise ValueError(f"more than {MAX_TESTS} tests")
    return names or [SNIPPET]


def _parse_results(raw: bytes, nonce: str, names: list[str]) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    wanted = set(names)
    for line in raw.decode("utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("nonce") != nonce:
            continue
        name = row.get("name")
        if (
            name in wanted
            and name not in results
            and row.get("outcome")
            in (
                "passed",
                "failed",
                "error",
                "skipped",
            )
        ):
            results[name] = {"outcome": row["outcome"], "message": str(row.get("message", ""))}
    return results


@register
class TestsPass(BaseMetric):
    __test__ = False  # not a pytest class

    spec = MetricSpec(
        id="code.tests_pass",
        category="code",
        description="Share of the provided tests the code passes (sandboxed run).",
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
            "properties": {
                "tests": {"type": "string", "minLength": 1, "maxLength": 1_000_000},
                "language": {"enum": ["python"]},
                "module": {"type": "string", "pattern": "^[A-Za-z_][A-Za-z0-9_]{0,63}$"},
                "timeout_s": {"type": "number", "exclusiveMinimum": 0, "maximum": 600},
                "memory_mb": {"type": "integer", "minimum": 64, "maximum": 16384},
                "runtime": {"enum": list(sandbox.RUNTIMES)},
                "image": {"type": "string", "minLength": 1},
            },
            "required": ["tests"],
            "additionalProperties": False,
        },
        default_threshold=1.0,
        sources=("bnbagent-eval:new",),
    )

    async def _evaluate_rule(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        p = ctx.params
        tests = p.get("tests")
        if not isinstance(tests, str) or not tests.strip():
            raise ValueError("params.tests (test source) is required")
        language = p.get("language", "python")
        if language != "python":
            raise ValueError(f"language {language!r} is not supported (python only)")
        items = await sources(case, ctx)
        if len(items) != 1:
            raise ValueError("code.tests_pass needs exactly one code source (output or artifact)")
        try:
            names = collect_tests(tests)
        except SyntaxError as exc:
            raise ValueError(f"the tests do not parse: {exc.msg} (line {exc.lineno})") from exc
        module = p.get("module", "solution")
        runtime = p.get("runtime", "subprocess")
        limits = sandbox.Limits(
            timeout_s=float(p.get("timeout_s", 10)), memory_mb=p.get("memory_mb", 512)
        )
        nonce = secrets.token_hex(16)
        results_file = f".bnbeval-results-{secrets.token_hex(4)}.jsonl"
        files = {
            f"{module}.py": items[0].text,
            f"test_{module}.py": tests,
            RUNNER_NAME: RUNNER.read_text(encoding="utf-8"),
        }
        command = [
            sandbox.PYTHON,
            "-I",
            "-B",
            RUNNER_NAME,
            results_file,
            f"test_{module}",
            json.dumps(names),
        ]
        try:
            res = await asyncio.to_thread(
                sandbox.run,
                command,
                files,
                limits=limits,
                network="deny",
                runtime=runtime,
                image=p.get("image"),
                stdin=(nonce + "\n").encode(),
                collect=[results_file],
            )
        except sandbox.SandboxUnavailable as exc:
            return Outcome(None, status="error", error=ErrorInfo("sandbox_unavailable", str(exc)))
        results = _parse_results(res.files.get(results_file, b""), nonce, names)
        rows = []
        for name in names:
            r = results.get(name) or {
                "outcome": "failed",
                "message": "timed out" if res.timed_out else "no result (crashed or exited)",
            }
            rows.append({"name": name, **r})
        counted = [r for r in rows if r["outcome"] != "skipped"]
        passed = sum(1 for r in counted if r["outcome"] == "passed")
        score = passed / len(counted) if counted else 0.0
        failed = [r for r in counted if r["outcome"] != "passed"]
        details: dict[str, Any] = {
            "passed": passed,
            "total": len(counted),
            "skipped": len(rows) - len(counted),
            "tests": rows[:MAX_REPORTED],
            "timed_out": res.timed_out,
            "returncode": res.returncode,
            "isolation": dict(res.isolation),
        }
        if res.stdout:
            details["stdout"] = res.stdout[-TAIL:]
        if res.stderr:
            details["stderr"] = res.stderr[-TAIL:]
        if res.stdout_truncated or res.stderr_truncated:
            details["output_truncated"] = True
        reason = None
        if failed:
            reason = f"{len(failed)} of {len(counted)} test(s) failed; first: {failed[0]['name']}"
            if failed[0]["message"]:
                reason += f": {failed[0]['message'].splitlines()[-1][:200]}"
        elif not counted:
            reason = "no tests ran (all skipped)"
        return Outcome(score=score, reason=reason, details=details)
