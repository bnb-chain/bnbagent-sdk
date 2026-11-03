"""Build prompt ``parts`` for every combination of optional prompt sections.

A section is ``(suffix, text)``: ``suffix`` is ``None`` for an always-present section, else the
name of the optional input it renders. The mode name lists the present optional sections in
order: ``simple`` / ``with_<a>_and_<b>`` (base ``simple``) or ``conversation`` /
``conversation_with_<a>`` (base ``conversation``).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from itertools import combinations

Section = tuple[str | None, str]


def section_mode(base: str, present: Iterable[str]) -> str:
    """Mode name for ``base`` with the optional sections ``present`` (in declaration order)."""
    present = list(present)
    if not present:
        return base
    suffix = "with_" + "_and_".join(present)
    return suffix if base == "simple" else f"{base}_{suffix}"


def section_parts(base: str, sections: Sequence[Section], task: str) -> dict[str, str]:
    """``{mode: body, task_<mode>: task}`` for every subset of the optional sections."""
    optional = [s for s, _ in sections if s is not None]
    parts: dict[str, str] = {}
    for n in range(len(optional) + 1):
        for chosen in combinations(optional, n):
            mode = section_mode(base, chosen)
            parts[mode] = "\n\n".join(t for s, t in sections if s is None or s in chosen)
            parts[f"task_{mode}"] = task
    return parts
