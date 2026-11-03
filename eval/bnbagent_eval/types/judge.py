"""Judge configuration value type (the judge layer itself lives in ``bnbagent_eval.judges``)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ._serde import Serializable

# Defaults when a provider is chosen without a model (override with EVAL_JUDGE_MODEL, the config
# file, a suite's judge pin or JudgeConfig(model=...)). OpenAI-compatible servers vary, so their
# chat model has no default.
DEFAULT_JUDGE_MODELS: dict[str, str] = {
    "bedrock": "global.anthropic.claude-sonnet-5-5",
    "anthropic": "claude-sonnet-5-5",
}
# Embedding models (STED, embedding similarity, RAGAS embeddings); override with
# EVAL_JUDGE_EMBEDDING_MODEL or JudgeConfig(extra={"embedding_model": ...}).
DEFAULT_EMBEDDING_MODELS: dict[str, str] = {
    "bedrock": "amazon.titan-embed-text-v2:0",
    "openai": "text-embedding-3-small",
}


@dataclass(frozen=True)
class JudgeConfig(Serializable):
    """Which judge model to use. API keys are never stored here (they come from the env)."""

    provider: str  # bedrock | openai | anthropic | fake | <registered provider>
    model: str
    temperature: float | None = 0.0  # None: never sent (some models reject the parameter)
    max_tokens: int = 2048
    samples: int = 1
    base_url: str | None = None
    region: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


def embedding_model(config: JudgeConfig) -> str | None:
    """The embedding model of a judge config: ``extra.embedding_model`` (``embeddings_model`` is
    accepted too), else the provider's default."""
    extra = config.extra or {}
    return (
        extra.get("embedding_model")
        or extra.get("embeddings_model")
        or DEFAULT_EMBEDDING_MODELS.get(config.provider)
    )
