"""Parsing judge output into a JSON object that matches the template's output schema."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Any

from .._jsonschema import validate
from ..exceptions import JudgeParseError

_FENCE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)

REPAIR_INSTRUCTION = (
    "Your previous reply could not be parsed. Reply again with ONLY a JSON object matching the "
    "required schema. Problems: {problems}"
)


def extract_json(
    text: str, fenced: bool = True, scan: bool = True, *, objects_only: bool = False
) -> Any:
    """Parse JSON from text: the whole text, else a fenced block (``fenced``), else (``scan``)
    the first ``{...}`` / ``[...]`` anywhere in the text (trailing text ignored).

    ``objects_only`` accepts JSON objects only. Raises ``ValueError`` if nothing parses. Shared
    by judge replies (``extract_object``), ``format.*`` and ``ops.consistency``.
    """
    text = text.strip()
    candidates = [text] + ([b.strip() for b in _FENCE.findall(text)] if fenced else [])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if not objects_only or isinstance(value, dict):
            return value
    if scan:
        decoder = json.JSONDecoder()
        starts = "{" if objects_only else "{["
        for i, ch in enumerate(text):
            if ch in starts:
                try:
                    value = decoder.raw_decode(text[i:])[0]
                except ValueError:
                    continue
                if not objects_only or isinstance(value, dict):
                    return value
    raise ValueError(
        "no JSON value found" if scan else "no JSON value (whole text or fenced block)"
    )


def extract_object(text: str) -> dict[str, Any]:
    """Find a JSON object in ``text``: whole text, a fenced block, or the first ``{...}``."""
    try:
        return extract_json(text, objects_only=True)
    except ValueError:
        raise JudgeParseError("no JSON object in judge response") from None


def parse(
    text: str, parsed: Mapping[str, Any] | None, schema: Mapping[str, Any]
) -> dict[str, Any]:
    """Return a schema-valid object or raise :class:`JudgeParseError` listing the problems."""
    obj = dict(parsed) if isinstance(parsed, Mapping) else extract_object(text)
    errors = validate(obj, schema)
    if errors:
        raise JudgeParseError("; ".join(errors[:5]))
    if not _finite(obj):  # json accepts NaN / Infinity, and NaN passes minimum / maximum checks
        raise JudgeParseError("reply contains a non-finite number (NaN or Infinity)")
    return obj


def _finite(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, Mapping):
        return all(_finite(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return all(_finite(v) for v in value)
    return True
