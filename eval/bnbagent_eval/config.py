"""Runtime configuration: an optional YAML file, then ``EVAL_*`` environment variables.

Precedence for the default judge (highest first): an explicit ``EvalClient(judge=...)`` or a
suite's ``judge`` pin, then ``EVAL_JUDGE_*`` variables, then the config file named by
``EVAL_CONFIG`` (or ``bnbagent-eval --config``), then the per-provider defaults below. Choosing a
provider is always explicit (judge calls cost money); the model may be left to the default.

Config file (all keys optional)::

    judge:
      provider: bedrock                         # bedrock | openai | anthropic
      model: global.anthropic.claude-sonnet-5-5 # default per provider (DEFAULT_JUDGE_MODELS)
      embedding_model: amazon.titan-embed-text-v2:0
      region: us-east-1                         # Bedrock; else the AWS credential chain's region
      base_url: http://localhost:8000/v1        # OpenAI-compatible servers
      temperature: 0
      max_tokens: 2048
      cache: sqlite:///.eval-cache/judge.db
    max_concurrency: 8
    timeout_s: 120

API keys never go in the file: they come from the environment (``EVAL_JUDGE_API_KEY``,
``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY``) or the AWS credential chain.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from .exceptions import EvalError
from .types.judge import DEFAULT_JUDGE_MODELS, JudgeConfig

ENV_PREFIX = "EVAL_"
CONFIG_ENV = "EVAL_CONFIG"


def get_env(key: str, default: str | None = None, prefix: str = ENV_PREFIX) -> str | None:
    """Read ``<prefix><key>``; an empty value counts as unset (same semantics as the SDK)."""
    value = os.environ.get(f"{prefix}{key}")
    if value is None or value.strip() == "":
        return default
    return value


def get_env_number(key: str, default: Any, kind: type = float) -> Any:
    """``<EVAL_><key>`` parsed as ``kind`` (``int`` / ``float``); invalid values raise."""
    raw = get_env(key)
    if raw is None:
        return default
    try:
        return kind(raw)
    except ValueError:
        raise EvalError(f"{ENV_PREFIX}{key}={raw!r} is not a valid {kind.__name__}") from None


@dataclass(frozen=True)
class EvalConfig:
    judge_provider: str | None = None
    judge_model: str | None = None
    judge_base_url: str | None = None
    judge_region: str | None = None
    judge_embedding_model: str | None = None
    judge_temperature: float | None = 0.0
    judge_max_tokens: int = 2048
    judge_cache: str | None = None  # e.g. sqlite:///.eval-cache/judge.db
    max_concurrency: int = 8
    timeout_s: float = 120.0
    source: str | None = field(default=None, compare=False)  # the config file, if any

    @classmethod
    def from_file(cls, path: str | os.PathLike[str]) -> EvalConfig:
        """Read a YAML config file (see the module docstring)."""
        import yaml

        try:
            data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise EvalError(f"cannot read config file {path}: {exc}") from exc
        if not isinstance(data, Mapping):
            raise EvalError(f"{path}: expected a mapping")
        judge = data.get("judge") or {}
        if not isinstance(judge, Mapping):
            raise EvalError(f"{path}: 'judge' must be a mapping")
        unknown = (set(data) - {"judge", "max_concurrency", "timeout_s"}) | (
            set(judge) - {f.name[6:] for f in fields(cls) if f.name.startswith("judge_")}
        )
        if unknown:
            raise EvalError(f"{path}: unknown keys {sorted(unknown)}")
        values: dict[str, Any] = {f"judge_{k}": v for k, v in judge.items()}
        for key in ("max_concurrency", "timeout_s"):
            if key in data:
                values[key] = data[key]
        return cls(**values, source=str(path))

    @classmethod
    def from_env(cls) -> EvalConfig:
        """The config file named by ``EVAL_CONFIG`` (if any), overridden by ``EVAL_*``."""
        path = os.environ.get(CONFIG_ENV)
        base = cls.from_file(path) if path and path.strip() else cls()
        return cls(
            judge_provider=get_env("JUDGE_PROVIDER", base.judge_provider),
            judge_model=get_env("JUDGE_MODEL", base.judge_model),
            judge_base_url=get_env("JUDGE_BASE_URL", base.judge_base_url),
            judge_region=get_env("JUDGE_REGION", base.judge_region),
            judge_embedding_model=get_env("JUDGE_EMBEDDING_MODEL", base.judge_embedding_model),
            judge_temperature=get_env_number("JUDGE_TEMPERATURE", base.judge_temperature, float),
            judge_max_tokens=get_env_number("JUDGE_MAX_TOKENS", base.judge_max_tokens, int),
            judge_cache=get_env("JUDGE_CACHE", base.judge_cache),
            max_concurrency=get_env_number("MAX_CONCURRENCY", base.max_concurrency, int),
            timeout_s=get_env_number("TIMEOUT_S", base.timeout_s, float),
            source=base.source,
        )

    def judge_config(self) -> JudgeConfig | None:
        """The default judge (``None`` when no provider is configured)."""
        if not self.judge_provider:
            return None
        model = self.judge_model or DEFAULT_JUDGE_MODELS.get(self.judge_provider)
        if not model:
            raise EvalError(
                f"judge provider {self.judge_provider!r} has no default model: "
                "set EVAL_JUDGE_MODEL (or judge.model in the config file)"
            )
        extra = (
            {"embedding_model": self.judge_embedding_model} if self.judge_embedding_model else {}
        )
        return JudgeConfig(
            provider=self.judge_provider,
            model=model,
            temperature=self.judge_temperature,
            max_tokens=self.judge_max_tokens,
            base_url=self.judge_base_url,
            region=self.judge_region,
            extra=extra,
        )
