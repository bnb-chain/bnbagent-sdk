"""EvalConfig: config file, EVAL_* overrides, per-provider model defaults."""

from __future__ import annotations

import pytest

from bnbagent_eval.config import EvalConfig
from bnbagent_eval.exceptions import EvalError
from bnbagent_eval.types.judge import (
    DEFAULT_EMBEDDING_MODELS,
    DEFAULT_JUDGE_MODELS,
    JudgeConfig,
    embedding_model,
)

ENV = ("EVAL_CONFIG", "EVAL_JUDGE_PROVIDER", "EVAL_JUDGE_MODEL", "EVAL_JUDGE_REGION",
       "EVAL_JUDGE_EMBEDDING_MODEL", "EVAL_JUDGE_TEMPERATURE", "EVAL_MAX_CONCURRENCY")  # fmt: skip


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


def test_provider_defaults(monkeypatch) -> None:
    assert EvalConfig.from_env().judge_config() is None  # no provider: no judge, no calls
    monkeypatch.setenv("EVAL_JUDGE_PROVIDER", "bedrock")
    cfg = EvalConfig.from_env().judge_config()
    assert cfg.model == DEFAULT_JUDGE_MODELS["bedrock"]
    assert embedding_model(cfg) == DEFAULT_EMBEDDING_MODELS["bedrock"]
    monkeypatch.setenv("EVAL_JUDGE_MODEL", "my.custom-model")
    monkeypatch.setenv("EVAL_JUDGE_EMBEDDING_MODEL", "my.embedder")
    monkeypatch.setenv("EVAL_JUDGE_REGION", "eu-west-1")
    cfg = EvalConfig.from_env().judge_config()
    assert (cfg.model, cfg.region, embedding_model(cfg)) == (
        "my.custom-model",
        "eu-west-1",
        "my.embedder",
    )


def test_openai_compatible_needs_a_model(monkeypatch) -> None:
    monkeypatch.setenv("EVAL_JUDGE_PROVIDER", "openai")
    with pytest.raises(EvalError, match="EVAL_JUDGE_MODEL"):
        EvalConfig.from_env().judge_config()
    assert embedding_model(JudgeConfig(provider="openai", model="m")) == "text-embedding-3-small"
    legacy = JudgeConfig(provider="bedrock", model="m", extra={"embeddings_model": "old-key"})
    assert embedding_model(legacy) == "old-key"


def test_config_file_and_env_precedence(tmp_path, monkeypatch) -> None:
    path = tmp_path / "eval.yaml"
    path.write_text(
        "judge:\n  provider: bedrock\n  model: file.model\n  region: us-east-1\n"
        "  embedding_model: file.embedder\n  temperature: 0.2\n  cache: sqlite:///j.db\n"
        "max_concurrency: 3\n"
    )
    monkeypatch.setenv("EVAL_CONFIG", str(path))
    cfg = EvalConfig.from_env()
    assert (cfg.judge_model, cfg.judge_cache, cfg.max_concurrency) == (
        "file.model",
        "sqlite:///j.db",
        3,
    )
    judge = cfg.judge_config()
    assert judge.temperature == 0.2 and embedding_model(judge) == "file.embedder"
    monkeypatch.setenv("EVAL_JUDGE_MODEL", "env.model")  # the environment wins
    assert EvalConfig.from_env().judge_config().model == "env.model"


def test_config_file_errors(tmp_path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("judge:\n  provder: bedrock\n")
    with pytest.raises(EvalError, match="unknown keys"):
        EvalConfig.from_file(bad)
    with pytest.raises(EvalError, match="cannot read"):
        EvalConfig.from_file(tmp_path / "missing.yaml")


def test_cli_config_option(tmp_path, monkeypatch, capsys) -> None:
    import json

    from bnbagent_eval.cli import main

    monkeypatch.setenv("EVAL_CONFIG", "")  # recorded, so the value main() sets is undone
    path = tmp_path / "eval.yaml"
    path.write_text("judge:\n  provider: fake\n  model: scripted\n")
    suite = tmp_path / "s.yaml"
    suite.write_text("name: s\nversion: '1'\nmetrics:\n  - {id: text.exact_match}\n")
    cases = tmp_path / "c.jsonl"
    cases.write_text('{"id": "1", "output": "a", "expected": "a"}\n')
    assert main(["--config", str(path), "run", "--suite", str(suite), "--cases", str(cases)]) == 0
    assert json.loads(capsys.readouterr().out)["verdict"]["status"] == "pass"
    assert EvalConfig.from_env().judge_model == "scripted"


@pytest.mark.parametrize(("key", "value"), [("EVAL_MAX_CONCURRENCY", "eight"),
                                            ("EVAL_JUDGE_TEMPERATURE", "warm")])  # fmt: skip
def test_invalid_numbers_raise_eval_error(monkeypatch, key, value) -> None:
    # an EvalError (CLI exit 64 with a message), not a ValueError traceback
    monkeypatch.setenv(key, value)
    with pytest.raises(EvalError, match=key):
        EvalConfig.from_env()


def test_service_config_reads_numbers_through_eval_config(monkeypatch) -> None:
    from bnbagent_eval.server.config import ServiceConfig

    monkeypatch.setenv("EVAL_SERVER_MAX_CASES", "7")
    monkeypatch.setenv("EVAL_SERVER_RATE_PER_S", "2.5")
    cfg = ServiceConfig.from_env()
    assert cfg.limits.max_cases == 7 and cfg.rate_per_s == 2.5
    monkeypatch.setenv("EVAL_SERVER_RATE_BURST", "lots")
    with pytest.raises(EvalError, match="EVAL_SERVER_RATE_BURST"):
        ServiceConfig.from_env()
