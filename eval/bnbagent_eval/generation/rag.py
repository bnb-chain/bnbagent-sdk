"""RAG test generation (``generation.rag``): question/answer cases from documents.

Port of ETK ``generator_builtin.rag.RagGenerator``: documents are split with a recursive
character splitter (``chunk_size`` 1024, ``overlap`` 128, ETK defaults), and the judge writes
``questions_per_chunk`` (3) question/answer pairs per chunk. Each pair becomes an
:class:`EvalCase` with ``input`` = question, ``expected`` = answer and ``contexts`` = the chunk,
whose ``source`` is a provenance id ``<source>#chunk-<n>``.

Sources (instead of ETK's S3 prefix): local text / markdown / JSON / JSONL files or directories,
``http(s)://`` URLs (through an injectable ``httpx.AsyncClient``), ``StoragePort`` URIs (any
other ``scheme://`` or ``data:``), and inline :class:`Document` values. PDFs are read only if
``pypdf`` is installed; otherwise they are skipped with a warning.

Failure handling follows ETK: a chunk whose generation fails is skipped; generation aborts after
``MAX_CONSECUTIVE_ERRORS`` consecutive failures, or if every chunk failed.
"""

from __future__ import annotations

import html
import io
import json
import logging
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .._optional import available
from ..exceptions import JudgeNonRetryable
from ..hashing import keccak_hex
from ..judges.base import JudgeModel
from ..judges.prompts.generation.rag import QA_V1
from ..ports import StoragePort
from ..types.case import Context, EvalCase
from ._llm import GenerationError, Prompt, ask_json

__all__ = [
    "Chunk",
    "Document",
    "GenerationError",
    "RagGenerator",
    "chunk_document",
    "load_documents",
    "split_text",
]

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 1024  # characters (ETK)
DEFAULT_CHUNK_OVERLAP = 128  # characters (ETK)
DEFAULT_QUESTIONS_PER_CHUNK = 3  # ETK
MAX_CONSECUTIVE_ERRORS = 10  # ETK common.MAX_CONSECUTIVE_ERRORS
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
SEPARATORS = ("\n\n", "\n", " ", "")  # langchain RecursiveCharacterTextSplitter defaults
TEXT_SUFFIXES = (".txt", ".md", ".markdown", ".rst", ".json", ".jsonl", ".pdf")
_URI = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_TEXT_KEYS = ("text", "content", "body", "page_content")


@dataclass(frozen=True)
class Document:
    """Inline source text; ``source`` names it in provenance ids."""

    text: str
    source: str


@dataclass(frozen=True)
class Chunk:
    text: str
    source: str
    index: int
    start: int  # character offset in the document (-1 if not locatable)
    end: int

    @property
    def id(self) -> str:
        return f"{self.source}#chunk-{self.index}"

    @property
    def hash(self) -> str:
        return keccak_hex(f"{self.id}\n{self.text}")[2:18]


# ------------------------------------------------------------------------------ chunking


def _split_keep_start(text: str, separator: str) -> list[str]:
    """``re.split`` keeping each separator at the start of the following piece."""
    if not separator:
        return list(text)
    parts = re.split(f"({re.escape(separator)})", text)
    pieces = [parts[i] + parts[i + 1] for i in range(1, len(parts) - 1, 2)]
    if len(parts) % 2 == 0:
        pieces.append(parts[-1])
    return [p for p in [parts[0], *pieces] if p]


def _merge(splits: Sequence[str], chunk_size: int, overlap: int) -> list[str]:
    """Greedy merge of small splits into chunks with up to ``overlap`` trailing characters."""
    docs: list[str] = []
    current: list[str] = []
    total = 0
    for piece in splits:
        size = len(piece)
        if total + size > chunk_size and current:
            doc = "".join(current).strip()
            if doc:
                docs.append(doc)
            while total > overlap or (total + size > chunk_size and total > 0):
                total -= len(current[0])
                current = current[1:]
        current.append(piece)
        total += size
    doc = "".join(current).strip()
    if doc:
        docs.append(doc)
    return docs


def split_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    separators: Sequence[str] = SEPARATORS,
) -> list[str]:
    """Deterministic port of langchain ``RecursiveCharacterTextSplitter.split_text``.

    Splits on the first separator present (paragraph, line, word, character), merges pieces up
    to ``chunk_size`` characters, carries up to ``overlap`` characters into the next chunk,
    and recurses into pieces that are still too long. Chunks are whitespace-stripped.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError(f"overlap ({overlap}) must be >= 0 and less than chunk_size")

    def recurse(text: str, separators: Sequence[str]) -> list[str]:
        separator, rest = separators[-1], ()
        for i, sep in enumerate(separators):
            if sep == "" or sep in text:
                separator, rest = sep, tuple(separators[i + 1 :])
                break
        out: list[str] = []
        good: list[str] = []
        for piece in _split_keep_start(text, separator):
            if len(piece) < chunk_size:
                good.append(piece)
                continue
            if good:
                out.extend(_merge(good, chunk_size, overlap))
                good = []
            out.extend(recurse(piece, rest) if rest else [piece])
        if good:
            out.extend(_merge(good, chunk_size, overlap))
        return out

    return recurse(text, tuple(separators))


def chunk_document(
    document: Document,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[Chunk]:
    """Chunks of one document with their character spans."""
    chunks, cursor = [], 0
    for i, text in enumerate(split_text(document.text, chunk_size, overlap)):
        start = document.text.find(text, cursor)
        if start >= 0:
            cursor = start + 1
        end = start + len(text) if start >= 0 else -1
        chunks.append(Chunk(text=text, source=document.source, index=i, start=start, end=end))
    return chunks


# ------------------------------------------------------------------------------- loading


def _json_texts(data: Any) -> list[str]:
    """Texts in parsed JSON: a string, a record's text field, a list of those, else the dump."""
    if isinstance(data, str):
        return [data]
    if isinstance(data, Mapping):
        for key in _TEXT_KEYS:
            if isinstance(data.get(key), str):
                return [data[key]]
    if isinstance(data, list) and data:
        texts = [_json_texts(item) for item in data if isinstance(item, (str, Mapping))]
        if len(texts) == len(data) and all(
            len(t) == 1
            and (isinstance(d, str) or any(isinstance(d.get(k), str) for k in _TEXT_KEYS))
            for t, d in zip(texts, data, strict=True)
        ):
            return [t[0] for t in texts]
    return [json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)]


def _pdf_texts(data: bytes, source: str) -> list[str]:
    if not available("pypdf"):
        logger.warning("skipping PDF %s: install 'pypdf' to read PDF sources", source)
        return []
    import pypdf

    reader = pypdf.PdfReader(io.BytesIO(data))
    text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
    return [text]


def _decode(data: bytes, source: str, kind: str) -> list[str]:
    if kind == "pdf":
        return _pdf_texts(data, source)
    text = data.decode("utf-8")
    if kind == "json":
        return _json_texts(json.loads(text))
    if kind == "jsonl":
        return [
            t for line in text.splitlines() if line.strip() for t in _json_texts(json.loads(line))
        ]
    if kind == "html":
        text = html.unescape(
            re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ", text))
        )
    return [text]


def _kind(name: str, media_type: str = "") -> str:
    lowered, media_type = name.lower().split("?")[0], media_type.lower()
    if lowered.endswith(".pdf") or "application/pdf" in media_type:
        return "pdf"
    if lowered.endswith(".jsonl") or "jsonl" in media_type or "ndjson" in media_type:
        return "jsonl"
    if lowered.endswith(".json") or "json" in media_type:
        return "json"
    if "html" in media_type or lowered.endswith((".html", ".htm")):
        return "html"
    return "text"


def _named(texts: list[str], source: str) -> list[Document]:
    if len(texts) == 1:
        return [Document(texts[0], source)]
    return [Document(t, f"{source}[{i}]") for i, t in enumerate(texts)]


def _local_files(path: Path) -> list[Path]:
    if path.is_dir():
        return sorted(
            p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES
        )
    return [path]


async def load_documents(
    sources: Iterable[str | os.PathLike[str] | Document],
    *,
    http_client: httpx.AsyncClient | None = None,
    storage: StoragePort | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> list[Document]:
    """Read ``sources`` into documents, in order; unreadable sources are skipped with a warning."""
    docs: list[Document] = []
    own_client: httpx.AsyncClient | None = None
    try:
        for source in sources:
            if isinstance(source, Document):
                docs.append(source)
                continue
            name = os.fspath(source)
            try:
                if name.startswith(("http://", "https://")):
                    if http_client is None:
                        own_client = own_client or httpx.AsyncClient(timeout=30.0)
                    response = await (http_client or own_client).get(name)
                    response.raise_for_status()
                    if len(response.content) > max_bytes:
                        raise GenerationError(f"{name} exceeds {max_bytes} bytes")
                    kind = _kind(name, response.headers.get("content-type", ""))
                    docs.extend(_named(_decode(response.content, name, kind), name))
                elif name.startswith("file://"):
                    docs.extend(await load_documents([name[7:]], max_bytes=max_bytes))
                elif _URI.match(name) or name.startswith("data:"):
                    if storage is None:
                        raise GenerationError(f"{name}: a storage URI needs storage=")
                    docs.extend(_named(_json_texts(await storage.download(name)), name))
                else:
                    for path in _local_files(Path(name)):
                        if path.stat().st_size > max_bytes:
                            raise GenerationError(f"{path} exceeds {max_bytes} bytes")
                        texts = _decode(path.read_bytes(), str(path), _kind(path.name))
                        docs.extend(_named(texts, path.as_posix()))
            except (OSError, ValueError, httpx.HTTPError, GenerationError) as exc:
                logger.warning("skipping source %s: %s: %s", name, type(exc).__name__, exc)
    finally:
        if own_client is not None:
            await own_client.aclose()
    return [d for d in docs if d.text.strip()]


# ---------------------------------------------------------------------------- generation


class RagGenerator:
    """Question/answer cases from document chunks (ETK ``RagGenerator``, converse mode)."""

    def __init__(
        self,
        judge: JudgeModel,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        overlap: int = DEFAULT_CHUNK_OVERLAP,
        questions_per_chunk: int = DEFAULT_QUESTIONS_PER_CHUNK,
        *,
        prompt: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        storage: StoragePort | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        id_prefix: str = "rag",
    ):
        if questions_per_chunk <= 0:
            raise ValueError("questions_per_chunk must be > 0")
        split_text("", chunk_size, overlap)  # validates the sizes
        self.judge = judge
        self.chunk_size, self.overlap = chunk_size, overlap
        self.questions_per_chunk = questions_per_chunk
        self.prompt = prompt or QA_V1.system
        self.http_client, self.storage, self.max_bytes = http_client, storage, max_bytes
        self.id_prefix = id_prefix

    async def chunks(self, sources: Iterable[str | os.PathLike[str] | Document]) -> list[Chunk]:
        docs = await load_documents(
            sources, http_client=self.http_client, storage=self.storage, max_bytes=self.max_bytes
        )
        return [c for d in docs for c in chunk_document(d, self.chunk_size, self.overlap)]

    async def generate(
        self, sources: Iterable[str | os.PathLike[str] | Document] | str | os.PathLike[str]
    ) -> list[EvalCase]:
        if isinstance(sources, (str, os.PathLike, Document)):
            sources = [sources]
        chunks = await self.chunks(sources)
        if not chunks:
            raise GenerationError("no documents could be loaded from the sources")
        cases: list[EvalCase] = []
        errored = consecutive = 0
        last: Exception | None = None
        for chunk in chunks:
            try:
                pairs = await self._qa_pairs(chunk)
            except JudgeNonRetryable:
                raise
            except Exception as exc:  # noqa: BLE001 - one bad chunk is skipped (ETK)
                errored, consecutive, last = errored + 1, consecutive + 1, exc
                logger.warning("QA generation failed for %s: %s", chunk.id, type(exc).__name__)
                if consecutive >= MAX_CONSECUTIVE_ERRORS:
                    raise
                continue
            consecutive = 0
            cases.extend(self._case(chunk, j, q, a) for j, (q, a) in enumerate(pairs))
        logger.info(
            "generated %d QA pairs from %d chunks (%d errored)", len(cases), len(chunks), errored
        )
        if errored and not cases and last is not None:
            raise last
        return cases

    async def _qa_pairs(self, chunk: Chunk) -> list[tuple[str, str]]:
        prompt = Prompt(QA_V1, chunk.id, {"chunk": chunk.text, "n": self.questions_per_chunk})
        rules = str(QA_V1.extra["output_rules"]).format(
            questions_per_chunk=self.questions_per_chunk
        )
        request = prompt.request(
            {"chunk": prompt.fence("chunk", chunk.text)}, system=f"{self.prompt}\n{rules}"
        )

        def check(obj: dict[str, Any]) -> list[tuple[str, str]]:
            pairs = [(p["question"].strip(), p["answer"].strip()) for p in obj["qa_pairs"]]
            return [(q, a) for q, a in pairs if q and a][: self.questions_per_chunk]

        return await ask_json(self.judge, prompt, request, check=check, wrap="qa_pairs")

    def _case(self, chunk: Chunk, j: int, question: str, answer: str) -> EvalCase:
        return EvalCase(
            id=f"{self.id_prefix}-{chunk.hash[:12]}-{j}",
            input=question,
            expected=answer,
            contexts=(Context(text=chunk.text, source=chunk.id),),
            metadata={
                "generator": "generation.rag",
                "prompt": f"{QA_V1.metric_id}@{QA_V1.version}",
                "source": chunk.source,
                "chunk_id": chunk.id,
                "chunk_index": chunk.index,
                "chunk_hash": chunk.hash,
                "char_span": [chunk.start, chunk.end],
            },
        )
