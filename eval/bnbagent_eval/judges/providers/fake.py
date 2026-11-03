"""A scripted judge for tests and offline runs."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ...exceptions import JudgeError
from ...types.judge import JudgeConfig
from ...types.trajectory import Usage
from ..base import JudgeRequest, JudgeResponse

Script = Callable[[JudgeRequest], Any] | Mapping[str, Any] | Sequence[Any] | Any


class FakeJudge:
    """Returns scripted responses and records every request.

    ``script`` may be: a callable ``request -> response``; a mapping ``metric_id -> response``
    (or a list of responses, consumed in order); a list (consumed in order); or a single
    response. A response is a dict (structured output), a string (raw text), or an exception
    instance (raised).
    """

    def __init__(self, script: Script = None, *, model: str = "fake", samples: int = 1):
        self.config = JudgeConfig(provider="fake", model=model, samples=samples)
        self.script = script
        self.requests: list[JudgeRequest] = []
        self._cursor: dict[str, int] = {}

    def _next(self, request: JudgeRequest) -> Any:
        script = self.script
        if callable(script) and not isinstance(script, Mapping):
            return script(request)
        key = str(request.metadata.get("metric_id", ""))
        if isinstance(script, Mapping) and not _looks_like_response(script):
            script = script.get(key, script.get("*"))
        if isinstance(script, (list, tuple)):
            i = self._cursor.get(key, 0)
            self._cursor[key] = i + 1
            return script[min(i, len(script) - 1)]
        return script

    async def complete(self, request: JudgeRequest) -> JudgeResponse:
        self.requests.append(request)
        out = self._next(request)
        if isinstance(out, BaseException):
            raise out
        if out is None:
            raise JudgeError("FakeJudge has no scripted response for this request")
        if isinstance(out, Mapping):
            text = json.dumps(out)
            parsed: Mapping[str, Any] | None = out
        else:
            text, parsed = str(out), None
        usage = Usage(
            input_tokens=len(request.system + request.user) // 4, output_tokens=len(text) // 4
        )
        return JudgeResponse(text=text, parsed=parsed, usage=usage, model=self.config.model)

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[float(len(t))] for t in texts]


def _looks_like_response(mapping: Mapping[str, Any]) -> bool:
    """A script mapping is a per-metric map only if every key is a metric id or ``*``."""
    return not mapping or not all(k == "*" or "." in str(k) for k in mapping)
