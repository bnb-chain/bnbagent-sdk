"""Judge providers and the factory that builds one from a :class:`JudgeConfig`."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...exceptions import SuiteError
from ...types.judge import JudgeConfig
from ..base import JudgeModel

_FACTORIES: dict[str, Callable[[JudgeConfig], JudgeModel]] = {}


def register_provider(name: str, factory: Callable[[JudgeConfig], JudgeModel]) -> None:
    _FACTORIES[name] = factory


def create_judge(config: JudgeConfig, **kwargs: Any) -> JudgeModel:
    """Instantiate the judge for ``config.provider`` (imports are lazy per provider)."""
    if config.provider in _FACTORIES:
        return _FACTORIES[config.provider](config)
    if config.provider == "openai":
        from .openai_compat import OpenAICompatJudge

        return OpenAICompatJudge(config, **kwargs)
    if config.provider == "anthropic":
        from .anthropic import AnthropicJudge

        return AnthropicJudge(config, **kwargs)
    if config.provider == "bedrock":
        from .bedrock import BedrockJudge

        return BedrockJudge(config, **kwargs)
    if config.provider == "fake":
        raise SuiteError("provider 'fake' must be passed as a FakeJudge instance")
    raise SuiteError(f"unknown judge provider {config.provider!r}")


__all__ = ["create_judge", "register_provider"]
