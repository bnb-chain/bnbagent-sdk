"""In-sandbox test runner for ``code.tests_pass`` (stdlib only; executed, never imported).

``python -I -B _bnbeval_runner.py <results> <test_module> <names-json>``; the first stdin line is a
nonce copied into every result line. ``names`` come from the parent's AST scan of the trusted test
source: ``test_x``, ``TestCls::test_y``, or ``<snippet>`` (the module's import is the test).
Pytest-style tests are plain functions / classes; the only fixture supported is ``tmp_path``.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

SNIPPET = "<snippet>"
MAX_MESSAGE = 1000


def _message(exc: BaseException) -> str:
    lines = traceback.format_exception_only(type(exc), exc)
    return "".join(lines).strip()[-MAX_MESSAGE:]


def _outcome(exc: BaseException) -> str:
    if type(exc).__name__ == "Skipped":  # pytest.skip
        return "skipped"
    return "failed" if isinstance(exc, AssertionError) else "error"


def _call(fn, name: str) -> None:
    kwargs = {}
    for param in inspect.signature(fn).parameters.values():
        if param.name == "tmp_path":
            kwargs["tmp_path"] = Path(tempfile.mkdtemp(prefix="t-", dir=os.getcwd()))
        elif param.default is inspect.Parameter.empty and param.kind not in (
            param.VAR_POSITIONAL,
            param.VAR_KEYWORD,
        ):
            raise TypeError(f"{name}: fixture {param.name!r} is not supported")
    result = fn(**kwargs)
    if inspect.iscoroutine(result):
        asyncio.run(result)


def _resolve(module, name: str):
    if "::" not in name:
        return getattr(module, name)
    cls_name, method = name.split("::", 1)
    instance = getattr(module, cls_name)()
    setup = getattr(instance, "setup_method", None)
    if callable(setup):
        setup(getattr(instance, method))
    return getattr(instance, method)


def main() -> int:
    nonce = sys.stdin.readline().strip()
    results_path, module_name, names = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
    sys.path.insert(0, os.getcwd())
    out = open(results_path, "a", encoding="utf-8")  # noqa: SIM115

    def emit(name: str, outcome: str, message: str = "") -> None:
        row = {"nonce": nonce, "name": name, "outcome": outcome, "message": message}
        out.write(json.dumps(row) + "\n")
        out.flush()

    try:
        module = importlib.import_module(module_name)
    except BaseException as exc:  # noqa: BLE001 - SystemExit included
        if names == [SNIPPET]:
            emit(SNIPPET, _outcome(exc), _message(exc))
        else:
            for name in names:
                emit(name, "error", "collection failed: " + _message(exc))
        return 0
    if names == [SNIPPET]:
        emit(SNIPPET, "passed")
        return 0
    for name in names:
        try:
            _call(_resolve(module, name), name)
        except BaseException as exc:  # noqa: BLE001 - a test calling sys.exit fails
            emit(name, _outcome(exc), _message(exc))
        else:
            emit(name, "passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
