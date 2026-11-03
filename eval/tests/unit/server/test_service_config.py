"""``ServiceConfig.from_env`` and the ``serve`` options layered on top of it."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")  # server tests run with the [server] extra only

from bnbagent_eval.cli import _serve_config, build_parser  # noqa: E402
from bnbagent_eval.exceptions import EvalError  # noqa: E402
from bnbagent_eval.server.config import ServiceConfig, parse_kinds  # noqa: E402

TTL_VARS = {
    "EVAL_SERVER_JOB_TTL_S": "60",
    "EVAL_SERVER_IDEMPOTENCY_TTL_S": "120",
    "EVAL_SERVER_CREDIT_TTL_S": "3600",
    "EVAL_SERVER_EVAL_TIMEOUT_S": "30.5",
}


def _serve(*extra: str):
    return _serve_config(build_parser().parse_args(["serve", *extra]))


def test_ttls_and_timeout_from_env(monkeypatch) -> None:
    for key in (*TTL_VARS, "EVAL_SERVER_ALLOWED_KINDS"):
        monkeypatch.delenv(key, raising=False)
    d = ServiceConfig.from_env()
    assert (d.job_ttl_s, d.idempotency_ttl_s, d.credit_ttl_s, d.eval_timeout_s) == (
        24 * 3600,
        24 * 3600,
        30 * 86400,
        600.0,
    )
    for key, value in TTL_VARS.items():
        monkeypatch.setenv(key, value)
    c = ServiceConfig.from_env()
    assert (c.job_ttl_s, c.idempotency_ttl_s, c.credit_ttl_s, c.eval_timeout_s) == (
        60,
        120,
        3600,
        30.5,
    )


def test_allowed_kinds_from_env_and_cli(monkeypatch) -> None:
    monkeypatch.delenv("EVAL_SERVER_ALLOWED_KINDS", raising=False)
    assert _serve().allowed_kinds == ("rule", "judge")
    monkeypatch.setenv("EVAL_SERVER_ALLOWED_KINDS", "rule,judge,vendor")
    assert _serve().allowed_kinds == ("rule", "judge", "vendor")
    assert _serve("--allowed-kinds", "rule, model").allowed_kinds == ("rule", "model")
    with pytest.raises(EvalError, match="subset of rule,judge,model,vendor"):
        _serve("--allowed-kinds", "rule,code")
    monkeypatch.setenv("EVAL_SERVER_ALLOWED_KINDS", "everything")
    with pytest.raises(EvalError, match="allowed kinds"):
        _serve()
    assert parse_kinds("judge,judge") == ("judge",)
    with pytest.raises(ValueError):
        parse_kinds(" , ")
