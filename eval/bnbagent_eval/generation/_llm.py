"""Judge calls shared by the generators: fenced prompt, JSON extraction, validated repair loop.

Port of ETK ``agentic.bedrock.converse_with_retry`` / ``extract_json_from_response`` onto the
``JudgeModel`` protocol: a reply that does not parse or does not validate is sent back with the
error (up to ``MAX_REPAIRS`` times, ETK ``MAX_RETRY_ATTEMPTS``); a transport failure re-sends
the same request; :class:`JudgeNonRetryable` is raised at once. The judge request is single
turn, so the repair is appended to the original user prompt rather than to a conversation.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, TypeVar

from .._jsonschema import validate
from ..exceptions import EvalError, JudgeError, JudgeNonRetryable, JudgeParseError
from ..hashing import hash_obj
from ..judges import structured
from ..judges.base import JudgeModel, JudgeRequest
from ..judges.fencing import fence, fence_clause, make_nonce
from ..judges.prompts import PromptTemplate
from ..judges.prompts.generation.agentic import RETRY_V1

T = TypeVar("T")
R = TypeVar("R")

MAX_REPAIRS = 3  # ETK MAX_RETRY_ATTEMPTS


class GenerationError(EvalError):
    """Test-case generation failed (no usable output, invalid configuration)."""


def extract_json(text: str) -> Any:
    """The first JSON object or array in ``text``: whole text, a fenced block, or embedded."""
    try:
        value = structured.extract_json(text)
    except ValueError:
        raise JudgeParseError("no JSON object in the response") from None
    if not isinstance(value, (dict, list)):
        raise JudgeParseError("the response is not a JSON object or array")
    return value


class Prompt:
    """One rendered request of ``template``; the fence nonce is derived from ``material``."""

    def __init__(self, template: PromptTemplate, key: str, material: Any):
        self.template = template
        self.key = key
        self.nonce = make_nonce(hash_obj(material), f"{template.metric_id}@{template.version}", 0)

    def fence(self, name: str, content: str) -> str:
        return fence(name, content, self.nonce)

    def section(self, name: str, content: str | None, field: str | None = None) -> str:
        """``extra[name]`` around fenced ``content``, or ``""`` when there is no content."""
        if not content:
            return ""
        return str(self.template.extra[name]).format(content=self.fence(field or name, content))

    def request(self, fields: Mapping[str, str], *, system: str | None = None) -> JudgeRequest:
        return JudgeRequest(
            system=(system if system is not None else self.template.system)
            + fence_clause(self.nonce),
            user=self.template.render("simple", fields),
            output_schema=self.template.output_schema,
            metadata={
                "metric_id": self.template.metric_id,
                "prompt_version": self.template.version,
                "tag": self.key,
            },
        )


def default_retry(prompt: Prompt) -> Callable[[Exception, str], str]:
    def retry(error: Exception, previous: str) -> str:
        text = prompt.template.extra.get("retry", RETRY_V1)
        return str(text).format(error=error, previous=previous)

    return retry


async def ask_json(
    judge: JudgeModel,
    prompt: Prompt,
    request: JudgeRequest,
    *,
    check: Callable[[dict[str, Any]], T],
    retry: Callable[[Exception, str], str] | None = None,
    wrap: str | None = None,
    max_repairs: int = MAX_REPAIRS,
) -> T:
    """Call ``judge`` until the reply validates against the schema and ``check`` accepts it.

    ``check`` raises :class:`JudgeParseError` (or ``ValueError``) with an actionable message;
    the message is fed back. ``wrap`` names the key a bare JSON array is wrapped in.
    """
    retry = retry or default_retry(prompt)
    current = request
    last: Exception | None = None
    for attempt in range(max_repairs + 1):
        try:
            response = await judge.complete(current)
        except JudgeNonRetryable:
            raise
        except JudgeParseError as exc:
            last, text = exc, ""
        except JudgeError as exc:  # transport: nothing to correct, re-send unchanged
            last = exc
            continue
        else:
            text = response.text
            try:
                obj = dict(response.parsed) if response.parsed is not None else extract_json(text)
                if isinstance(obj, list):
                    if wrap is None:
                        raise JudgeParseError("expected a JSON object, got an array")
                    obj = {wrap: obj}
                errors = validate(obj, prompt.template.output_schema)
                if errors:
                    raise JudgeParseError("; ".join(errors[:8]))
                return check(obj)
            except (JudgeParseError, ValueError, TypeError) as exc:
                last = exc if isinstance(exc, JudgeParseError) else JudgeParseError(str(exc))
        if attempt < max_repairs and text:
            previous = prompt.fence("previous_response", text)
            current = dataclasses.replace(
                request,
                user=request.user + "\n\n" + retry(last, previous),
                metadata={**request.metadata, "attempt": attempt + 1},
            )
    assert last is not None
    raise last


async def bounded_gather(
    fn: Callable[[R], Awaitable[T]], items: Sequence[R], limit: int
) -> list[T | Exception]:
    """``fn`` over ``items`` with at most ``limit`` in flight; results (or errors) in order."""
    semaphore = asyncio.Semaphore(max(1, limit))

    async def one(item: R) -> T | Exception:
        async with semaphore:
            try:
                return await fn(item)
            except JudgeNonRetryable:
                raise
            except Exception as exc:  # noqa: BLE001 - per-item failures are data here
                return exc

    return list(await asyncio.gather(*(one(item) for item in items)))
