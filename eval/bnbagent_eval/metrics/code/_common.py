"""Code sources for ``code.*``: the output (fenced or bare) or fetched artifacts."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ...net import FetchError, fetch_artifact, hash_matches
from ...types.case import Artifact, EvalCase
from ..base import MetricContext, StopScoring
from ..text._common import as_text

MAX_SOURCE_BYTES = 2 * 1024 * 1024
LANGUAGES = ("python", "json", "javascript", "typescript", "solidity")

_FENCE = re.compile(r"```[ \t]*([A-Za-z0-9_+.-]*)[^\n]*\n(.*?)```", re.DOTALL)
_EXTENSIONS = {
    ".py": "python",
    ".json": "json",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".sol": "solidity",
}
_MEDIA = {
    "text/x-python": "python",
    "application/x-python": "python",
    "application/json": "json",
    "text/javascript": "javascript",
    "application/javascript": "javascript",
    "application/typescript": "typescript",
    "text/x-solidity": "solidity",
}
_FENCE_LANG = {"py": "python", "js": "javascript", "ts": "typescript", "sol": "solidity"}


@dataclass(frozen=True)
class Source:
    name: str
    text: str
    language: str | None = None  # inferred (fence tag, extension, media type)


def extract_code(text: str) -> tuple[str, str | None]:
    """The first fenced block (and its language tag), or the whole text."""
    m = _FENCE.search(text)
    if not m:
        return text, None
    tag = m.group(1).lower() or None
    return m.group(2), _FENCE_LANG.get(tag or "", tag)


def artifact_language(art: Artifact) -> str | None:
    """The language of a known extension or media type; an unknown file extension is returned
    as is (e.g. ``"rs"``), so the caller does not mistake the file for Python."""
    for candidate in (art.name or "", art.uri):
        for ext, lang in _EXTENSIONS.items():
            if candidate.lower().split("?")[0].endswith(ext):
                return lang
    media = _MEDIA.get(art.media_type.split(";")[0].strip().lower())
    if media is None and art.name and "." in art.name.rsplit("/", 1)[-1]:
        return art.name.rsplit(".", 1)[-1].lower() or None
    return media


async def sources(case: EvalCase, ctx: MetricContext) -> list[Source]:
    """Code to check: every artifact if the case has any, else the output."""
    if case.artifacts:
        out = []
        for i, art in enumerate(case.artifacts):
            try:
                content, _ = await fetch_artifact(
                    art.uri, cap=MAX_SOURCE_BYTES, storage=getattr(ctx, "storage", None)
                )
            except FetchError as exc:
                raise ValueError(f"artifact {art.name or i}: {exc.code}: {exc}") from exc
            if not hash_matches(art.hash, content):
                raise ValueError(f"artifact {art.name or i}: content does not match its hash")
            name = art.name or f"artifact_{i}"
            out.append(
                Source(name, content.decode("utf-8", errors="replace"), artifact_language(art))
            )
        return out
    if case.has("output"):
        text, lang = extract_code(as_text(case.output))
        if len(text.encode()) > MAX_SOURCE_BYTES:
            raise ValueError(f"source exceeds {MAX_SOURCE_BYTES} bytes")
        return [Source("output", text, lang)]
    raise StopScoring("missing_input", "missing required input(s): output or artifacts")
