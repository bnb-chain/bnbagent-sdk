"""Test-case filters (``generation.filters``).

:class:`LlmAsJudgeFilter` is ETK ``generator_builtin.llm_as_judge.LlmAsJudgeFilter`` (converse
mode): the judge counts how many ``criteria`` a case meets, and the case is kept when
``criteria_met / len(criteria) >= threshold`` (default 0.8). A case the judge cannot validate
is rejected with its error; like ETK, the filter aborts after ``MAX_CONSECUTIVE_ERRORS``
consecutive judge failures, and raises if no case got a verdict.

Deterministic filters: :class:`DedupeFilter`, :class:`LengthFilter` and :class:`LanguageFilter`
(a dependency-free script and stop-word detector). Every filter has
``async filter(cases) -> (kept, rejected)``; :func:`run_filters` chains them.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from ..exceptions import JudgeNonRetryable, JudgeParseError
from ..judges.base import JudgeModel
from ..judges.prompts.generation.filters import DEFAULT_CRITERIA, LLM_AS_JUDGE_V1
from ..types.case import EvalCase, content_text
from ._llm import Prompt, ask_json

__all__ = [
    "DEFAULT_CRITERIA",
    "CaseFilter",
    "DedupeFilter",
    "LanguageFilter",
    "LengthFilter",
    "LlmAsJudgeFilter",
    "Rejection",
    "detect_language",
    "run_filters",
]

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_ERRORS = 10  # ETK common.MAX_CONSECUTIVE_ERRORS


@dataclass(frozen=True)
class Rejection:
    case: EvalCase
    filter: str
    reason: str
    score: float | None = None


FilterResult = tuple[list[EvalCase], list[Rejection]]


class CaseFilter(Protocol):
    name: str

    async def filter(self, cases: Iterable[EvalCase]) -> FilterResult: ...


async def run_filters(cases: Iterable[EvalCase], filters: Sequence[CaseFilter]) -> FilterResult:
    """Apply ``filters`` in order; each sees only the cases its predecessors kept."""
    kept, rejected = list(cases), []
    for f in filters:
        kept, more = await f.filter(kept)
        rejected.extend(more)
    return kept, rejected


def _case_payload(case: EvalCase) -> dict[str, Any]:
    """The case as the judge sees it: non-empty content fields (ETK sends the case verbatim)."""
    data = case.to_dict()
    return {
        k: v for k, v in data.items() if k not in ("id", "metadata") and v not in (None, [], {})
    }


class LlmAsJudgeFilter:
    """Keep cases meeting at least ``threshold`` of ``criteria`` according to ``judge``."""

    name = "llm_as_judge"

    def __init__(
        self,
        judge: JudgeModel,
        criteria: Sequence[str] = DEFAULT_CRITERIA,
        threshold: float = 0.8,
        *,
        prompt: str | None = None,
    ):
        if not criteria:
            raise ValueError("criteria cannot be empty - at least one criterion is required")
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be in [0, 1]")
        self.judge, self.criteria, self.threshold = judge, tuple(criteria), threshold
        self.prompt = prompt or LLM_AS_JUDGE_V1.system

    def _system(self) -> str:
        listed = "\n".join(f"  {i + 1}. {c}" for i, c in enumerate(self.criteria))
        block = str(LLM_AS_JUDGE_V1.extra["criteria_block"]).format(criteria=listed)
        return f"{self.prompt}\n\n{block}"

    async def score(self, case: EvalCase) -> tuple[float, str]:
        """``(criteria_met / len(criteria), reasoning)`` for one case."""
        payload = json.dumps(_case_payload(case), ensure_ascii=False, sort_keys=True)
        prompt = Prompt(LLM_AS_JUDGE_V1, case.id, {"case": payload, "criteria": self.criteria})
        request = prompt.request(
            {"testcase": prompt.fence("testcase", payload)}, system=self._system()
        )

        def check(obj: dict[str, Any]) -> tuple[float, str]:
            met = obj["criteria_met"]
            if isinstance(met, str):
                if not met.strip().isdigit():
                    raise JudgeParseError("criteria_met must be an integer")
                met = int(met.strip())
            if met < 0:
                raise JudgeParseError("criteria_met must be >= 0")
            return min(met, len(self.criteria)) / len(self.criteria), str(obj["reasoning"])

        return await ask_json(self.judge, prompt, request, check=check)

    async def filter(self, cases: Iterable[EvalCase]) -> FilterResult:
        kept: list[EvalCase] = []
        rejected: list[Rejection] = []
        verdicts = errored = consecutive = 0
        last: Exception | None = None
        for case in cases:
            try:
                score, reasoning = await self.score(case)
            except JudgeNonRetryable:
                raise
            except Exception as exc:  # noqa: BLE001 - an unvalidatable case is rejected (ETK)
                errored, consecutive, last = errored + 1, consecutive + 1, exc
                rejected.append(Rejection(case, self.name, f"error: {type(exc).__name__}: {exc}"))
                if consecutive >= MAX_CONSECUTIVE_ERRORS:
                    raise
                continue
            consecutive, verdicts = 0, verdicts + 1
            if score >= self.threshold:
                kept.append(case)
            else:
                reason = f"met {score:.2f} of criteria < threshold {self.threshold}: {reasoning}"
                rejected.append(Rejection(case, self.name, reason, score))
        logger.info(
            "llm_as_judge: %d kept, %d rejected, %d errored", len(kept), len(rejected), errored
        )
        if errored and not verdicts and last is not None:
            raise last
        return kept, rejected


# -------------------------------------------------------------------- deterministic filters


def _text(case: EvalCase, field: str) -> str:
    value = getattr(case, field)
    if field == "contexts":
        return "\n".join(c.text for c in value)
    if isinstance(value, Mapping) and isinstance(value.get("turns"), list):
        return "\n".join(str(t) for t in value["turns"])
    if field == "expected" and not isinstance(value, str) and value is not None:
        if isinstance(value, Mapping) and isinstance(value.get("response"), str):
            return value["response"]
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return content_text(value) or ""


def normalize_text(text: str) -> str:
    """Case-folded, NFKC-normalized text with punctuation removed and whitespace collapsed."""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = "".join(ch if not unicodedata.category(ch).startswith("P") else " " for ch in text)
    return " ".join(text.split())


class DedupeFilter:
    """Drop cases whose key (default: normalized ``input``) repeats an earlier case's key."""

    name = "dedupe"

    def __init__(self, key: Callable[[EvalCase], Any] | None = None, *, field: str = "input"):
        self.key = key or (lambda case: normalize_text(_text(case, field)))

    def apply(self, cases: Iterable[EvalCase]) -> FilterResult:
        seen: dict[Any, str] = {}
        kept, rejected = [], []
        for case in cases:
            k = self.key(case)
            if k in seen:
                rejected.append(Rejection(case, self.name, f"duplicate of {seen[k]}"))
            else:
                seen[k] = case.id
                kept.append(case)
        return kept, rejected

    async def filter(self, cases: Iterable[EvalCase]) -> FilterResult:
        return self.apply(cases)


class LengthFilter:
    """Keep cases whose ``field`` text has ``min_chars <= len <= max_chars`` (stripped)."""

    name = "length"

    def __init__(self, min_chars: int = 1, max_chars: int | None = None, *, field: str = "input"):
        self.min_chars, self.max_chars, self.field = min_chars, max_chars, field

    def apply(self, cases: Iterable[EvalCase]) -> FilterResult:
        kept, rejected = [], []
        for case in cases:
            n = len(_text(case, self.field).strip())
            if n < self.min_chars:
                rejected.append(Rejection(case, self.name, f"{self.field} too short ({n})"))
            elif self.max_chars is not None and n > self.max_chars:
                rejected.append(Rejection(case, self.name, f"{self.field} too long ({n})"))
            else:
                kept.append(case)
        return kept, rejected

    async def filter(self, cases: Iterable[EvalCase]) -> FilterResult:
        return self.apply(cases)


# Scripts that identify a language on their own (first matching Unicode name prefix wins).
_SCRIPTS = (
    ("HIRAGANA", "ja"),
    ("KATAKANA", "ja"),
    ("HANGUL", "ko"),
    ("CJK", "zh"),
    ("CYRILLIC", "ru"),
    ("ARABIC", "ar"),
    ("HEBREW", "he"),
    ("DEVANAGARI", "hi"),
    ("THAI", "th"),
    ("GREEK", "el"),
)
_STOPWORDS = {
    "en": "the and is are of to in that it for with you this what how on be not can do",
    "es": "el la los las de que y en un una es por para con no se su del al como",
    "fr": "le la les des et est un une que qui dans pour pas sur au du avec ce il",
    "de": "der die das und ist nicht ein eine zu den mit von sich auf für dem ich",
    "pt": "o os as de que e em um uma para com não se por do da no na é",
    "it": "il lo gli di che e un una per con non sono della del nel è si al",
    "nl": "de het een en van is dat op te in niet zijn voor met die er",
}
_STOP_SETS = {lang: set(words.split()) for lang, words in _STOPWORDS.items()}
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def detect_language(text: str, *, min_words: int = 3) -> str | None:
    """Primary BCP-47 subtag of ``text`` by script, then Latin stop words; None if unsure.

    Japanese wins over Chinese when kana are present (kana are checked before CJK ideographs).
    """
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return None
    counts: dict[str, int] = {}
    for ch in letters:
        name = unicodedata.name(ch, "")
        for prefix, lang in _SCRIPTS:
            if name.startswith(prefix):
                counts[lang] = counts.get(lang, 0) + 1
                break
    if counts.get("ja"):
        return "ja"
    if counts:
        lang, n = max(counts.items(), key=lambda item: (item[1], item[0]))
        if n * 2 >= len(letters):
            return lang
    words = [w.casefold() for w in _WORD.findall(text)]
    if len(words) < min_words:
        return None
    scores = {lang: sum(w in stops for w in words) for lang, stops in _STOP_SETS.items()}
    best = max(scores.values())
    leaders = [lang for lang, s in scores.items() if s == best]
    return leaders[0] if best > 0 and len(leaders) == 1 else None


class LanguageFilter:
    """Keep cases in ``languages`` (``case.language`` if set, else :func:`detect_language`)."""

    name = "language"

    def __init__(
        self,
        languages: Sequence[str] = ("en",),
        *,
        field: str = "input",
        keep_undetermined: bool = True,
    ):
        self.languages = {lang.split("-")[0].lower() for lang in languages}
        self.field, self.keep_undetermined = field, keep_undetermined

    def apply(self, cases: Iterable[EvalCase]) -> FilterResult:
        kept, rejected = [], []
        for case in cases:
            lang = (case.language or "").split("-")[0].lower() or detect_language(
                _text(case, self.field)
            )
            if lang is None:
                if self.keep_undetermined:
                    kept.append(case)
                else:
                    rejected.append(Rejection(case, self.name, "language undetermined"))
            elif lang in self.languages:
                kept.append(case)
            else:
                rejected.append(Rejection(case, self.name, f"language {lang} not allowed"))
        return kept, rejected

    async def filter(self, cases: Iterable[EvalCase]) -> FilterResult:
        return self.apply(cases)
