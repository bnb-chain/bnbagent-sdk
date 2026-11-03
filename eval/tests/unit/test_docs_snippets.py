"""Run the Python snippets of the user guides (``docs/*.md``, ``README.md``) offline.

Markers, on the line before a fenced code block:

- ``<!-- doctest -->``: the next ``python`` block is executed (fresh namespace per block);
- ``<!-- doctest: requires=fastapi,eth_account -->``: the same, skipped if a module is missing;
- ``<!-- doctest-file: NAME -->``: the next block is written to ``NAME`` in the working
  directory (suites and cases that later snippets load).

Blocks of one document run in order, in one temporary working directory, with no judge or chain
configured in the environment. Network access is blocked by ``conftest.no_network``.
"""

from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

PACKAGE_DIR = Path(__file__).resolve().parents[2]
DOCS = sorted([*(PACKAGE_DIR / "docs").glob("*.md"), PACKAGE_DIR / "README.md"])

_MARKER = re.compile(r"^<!--\s*doctest(?P<file>-file)?(?::\s*(?P<arg>.*?))?\s*-->\s*$")
_FENCE = re.compile(r"^(?P<fence>```+)\s*(?P<lang>\w*)\s*$")
_ENV = (
    "EVAL_JUDGE_PROVIDER",
    "EVAL_JUDGE_MODEL",
    "EVAL_JUDGE_BASE_URL",
    "EVAL_JUDGE_API_KEY",
    "EVAL_JUDGE_CACHE",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "PRIVATE_KEY",
    "WALLET_KIND",
    "STORAGE_PROVIDER",
)


@dataclass(frozen=True)
class Block:
    doc: str
    line: int
    lang: str
    code: str
    file: str | None = None
    requires: tuple[str, ...] = ()


def extract(path: Path) -> list[Block]:
    lines = path.read_text(encoding="utf-8").splitlines()
    blocks: list[Block] = []
    i = 0
    while i < len(lines):
        marker = _MARKER.match(lines[i].strip())
        if not marker:
            i += 1
            continue
        start = i + 1
        fence = _FENCE.match(lines[start]) if start < len(lines) else None
        if fence is None:
            raise AssertionError(f"{path.name}:{i + 1}: a doctest marker must precede a fence")
        end = start + 1
        while end < len(lines) and lines[end].strip() != fence["fence"]:
            end += 1
        code = "\n".join(lines[start + 1 : end]) + "\n"
        arg = (marker["arg"] or "").strip()
        if marker["file"]:
            blocks.append(Block(path.name, start + 1, fence["lang"], code, file=arg))
        else:
            if fence["lang"] != "python":
                raise AssertionError(f"{path.name}:{start + 1}: doctest blocks must be python")
            requires: tuple[str, ...] = ()
            if arg.startswith("requires="):
                requires = tuple(m.strip() for m in arg[len("requires=") :].split(",") if m)
            blocks.append(Block(path.name, start + 1, "python", code, requires=requires))
        i = end + 1
    return blocks


def _missing(modules: tuple[str, ...]) -> list[str]:
    out = []
    for m in modules:
        try:
            found = importlib.util.find_spec(m) is not None
        except ModuleNotFoundError:
            found = False
        if not found:
            out.append(m)
    return out


# Only docs that have snippets, so a doc without any is not a skip on every run;
# test_guides_have_snippets pins which guides must have them.
@pytest.mark.parametrize("doc", [d for d in DOCS if extract(d)], ids=lambda p: p.name)
def test_doc_snippets(doc: Path, tmp_path, monkeypatch):
    blocks = extract(doc)
    monkeypatch.chdir(tmp_path)
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    ran, not_run = 0, set()
    for block in blocks:
        if block.file is not None:
            target = tmp_path / block.file
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(block.code, encoding="utf-8")
            continue
        missing = _missing(block.requires)
        if missing:
            not_run.update(missing)  # the extra is not installed in this environment
            continue
        ran += 1
        namespace = {"__name__": f"doctest_{doc.stem.lower()}"}
        try:
            exec(compile(block.code, f"{doc.name}:{block.line}", "exec"), namespace)
        except Exception as exc:
            raise AssertionError(
                f"{doc.name}:{block.line}: snippet failed: {type(exc).__name__}: {exc}"
            ) from exc
    if not ran and not_run:
        pytest.skip(f"{doc.name}: every snippet needs a missing extra ({sorted(not_run)})")


def test_guides_have_snippets():
    expected = {
        "SUITES.md",
        "JUDGES.md",
        "ONCHAIN.md",
        "SERVICE.md",
        "GENERATION.md",
        "REPORTING.md",
        "README.md",
        "USER_GUIDE.md",
        "STANDALONE.md",
        "THIRD_PARTY.md",
    }
    with_snippets = {p.name for p in DOCS if any(b.file is None for b in extract(p))}
    assert expected <= with_snippets


def test_relative_links_resolve():
    link = re.compile(r"\]\((?!https?://|#|mailto:)([^)#\s]+)(?:#[^)]*)?\)")
    for doc in DOCS:
        text = doc.read_text(encoding="utf-8")
        for target in link.findall(text):
            path = (doc.parent / target).resolve()
            if path != PACKAGE_DIR and PACKAGE_DIR not in path.parents:
                continue  # outside the package (it may move to its own repository)
            assert path.exists(), f"{doc.name}: broken link {target}"
