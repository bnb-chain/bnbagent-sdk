from __future__ import annotations

import logging

import pytest

from bnbagent_eval._jsonschema import validate
from bnbagent_eval._optional import available, require
from bnbagent_eval.config import EvalConfig, get_env
from bnbagent_eval.exceptions import MissingExtraError
from bnbagent_eval.logging_utils import RedactingFilter, redact


def test_get_env_empty_is_unset(monkeypatch) -> None:
    monkeypatch.setenv("EVAL_JUDGE_MODEL", "  ")
    assert get_env("JUDGE_MODEL", "d") == "d"
    monkeypatch.setenv("EVAL_JUDGE_PROVIDER", "openai")
    monkeypatch.setenv("EVAL_JUDGE_MODEL", "m")
    monkeypatch.setenv("EVAL_MAX_CONCURRENCY", "3")
    cfg = EvalConfig.from_env()
    assert cfg.max_concurrency == 3 and cfg.judge_config().model == "m"


def test_optional_require() -> None:
    assert available("json") and not available("definitely_missing_module_xyz")
    with pytest.raises(MissingExtraError, match=r"bnbagent-eval\[ragas\]"):
        require("definitely_missing_module_xyz", "ragas")


def test_redaction() -> None:
    assert "abc123" not in redact("api_key=abc123 rest")
    assert "sk-" not in redact("use sk-ABCDEFGHIJKLMNOPQRSTUV now")  # fake key; secret-scan: allow
    tx = "0x" + "ab" * 32
    assert redact(f"tx {tx}") == f"tx {tx}"  # hashes are not secrets
    record = logging.LogRecord("x", logging.INFO, "f", 1, "password=%s", ("hunter2",), None)
    RedactingFilter().filter(record)
    assert "hunter2" not in record.getMessage()


def test_jsonschema_subset() -> None:
    schema = {
        "type": "object",
        "required": ["a"],
        "additionalProperties": False,
        "properties": {
            "a": {"type": "integer", "minimum": 1},
            "b": {"type": "array", "items": {"enum": ["x", "y"]}},
        },
    }
    assert validate({"a": 2, "b": ["x"]}, schema) == []
    errors = validate({"a": 0, "b": ["z"], "c": 1}, schema)
    assert len(errors) == 3
    assert validate(True, {"type": "integer"})  # bools are not integers
    assert validate(1.0, {"type": "integer"}) == []
    assert validate("x", {"anyOf": [{"type": "integer"}, {"type": "string"}]}) == []


def test_jsonschema_pattern() -> None:
    assert validate("with_tools", {"type": "string", "pattern": "^[a-z_]+$"}) == []
    assert validate("Bad Mode!", {"type": "string", "pattern": "^[a-z_]+$"})
