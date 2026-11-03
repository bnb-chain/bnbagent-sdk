"""Contract inputs for code.* metrics (tests_pass runs only where the sandbox is available)."""

from __future__ import annotations

from bnbagent_eval.metrics.code import sandbox

from .catalog import ContractCase, pair

TESTS = "from solution import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
_support = sandbox.isolation_support()

CONTRACT = {
    "code.syntax_valid": ContractCase(**pair("def add(a, b):\n    return a + b\n", "def add(:")),
    "code.tests_pass": ContractCase(
        **pair("def add(a, b):\n    return a + b\n", "def add(a, b):\n    return a - b\n"),
        params={"tests": TESTS, "timeout_s": 20},
        skip=None if _support.full else f"sandbox unavailable: {_support.reason}",
    ),
}
