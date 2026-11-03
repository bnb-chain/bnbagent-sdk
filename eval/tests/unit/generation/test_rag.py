"""RagGenerator: deterministic chunking, sources (files, URLs, storage), case shape, repair."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest

from bnbagent_eval._optional import available
from bnbagent_eval.exceptions import JudgeParseError
from bnbagent_eval.generation.rag import (
    Document,
    GenerationError,
    RagGenerator,
    chunk_document,
    load_documents,
    split_text,
)
from bnbagent_eval.judges.providers.fake import FakeJudge

QA = "generation.rag.qa"


def pairs(n: int, tag: str = "q") -> dict[str, Any]:
    return {"qa_pairs": [{"question": f"{tag}{i}?", "answer": f"a{i}"} for i in range(n)]}


# ------------------------------------------------------------------------------ chunking


def test_defaults_are_etk():
    gen = RagGenerator(FakeJudge())
    assert (gen.chunk_size, gen.overlap, gen.questions_per_chunk) == (1024, 128, 3)


def test_split_words_with_overlap_exact():
    assert split_text("aaaa bbbb cccc dddd", 10, 5) == ["aaaa bbbb", "bbbb cccc", "cccc dddd"]


def test_split_short_text_is_one_stripped_chunk():
    assert split_text("  hello world \n", 1024, 128) == ["hello world"]
    assert split_text("", 10, 2) == []


def test_split_prefers_paragraph_boundaries():
    text = "First paragraph.\n\nSecond paragraph here.\n\nThird."
    assert split_text(text, 30, 0) == ["First paragraph.", "Second paragraph here.", "Third."]


def test_split_bounds_overlap_and_coverage():
    words = [f"w{i:03d}" for i in range(200)]
    text = " ".join(words)
    chunks = split_text(text, 60, 15)
    assert all(len(c) <= 60 for c in chunks)
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        assert prev.split()[-1] in nxt.split()  # trailing words carried over
        assert len(set(prev.split()) & set(nxt.split())) * 5 <= 15 + 5
    seen = [w for c in chunks for w in c.split()]
    assert set(seen) == set(words)
    assert split_text(text, 60, 15) == chunks  # deterministic


def test_split_long_token_falls_back_to_characters():
    chunks = split_text("x" * 25, 10, 2)
    assert all(len(c) <= 10 for c in chunks)
    assert chunks[0] == "x" * 10


def test_split_rejects_bad_sizes():
    with pytest.raises(ValueError):
        split_text("a", 10, 10)
    with pytest.raises(ValueError):
        split_text("a", 0, 0)
    with pytest.raises(ValueError):
        RagGenerator(FakeJudge(), chunk_size=100, overlap=200)


def test_chunk_spans_locate_text():
    doc = Document("alpha beta gamma delta epsilon zeta eta theta", "mem")
    chunks = chunk_document(doc, 20, 6)
    assert len(chunks) > 1
    for c in chunks:
        assert doc.text[c.start : c.end] == c.text
        assert c.id == f"mem#chunk-{c.index}"
    assert [c.start for c in chunks] == sorted(c.start for c in chunks)


# ------------------------------------------------------------------------------- sources


async def test_local_files_and_directory(tmp_path):
    (tmp_path / "a.md").write_text("# Title\n\nSome markdown.", encoding="utf-8")
    (tmp_path / "b.json").write_text(
        json.dumps([{"text": "record one"}, {"text": "record two"}]), encoding="utf-8"
    )
    (tmp_path / "c.json").write_text(json.dumps({"k": 1}), encoding="utf-8")
    (tmp_path / "ignored.bin").write_bytes(b"\x00\x01")
    docs = await load_documents([tmp_path])
    by_source = {d.source.rsplit("/", 1)[-1]: d.text for d in docs}
    assert by_source["a.md"].startswith("# Title")
    assert by_source["b.json[0]"] == "record one" and by_source["b.json[1]"] == "record two"
    assert json.loads(by_source["c.json"]) == {"k": 1}
    assert not any("ignored" in s for s in by_source)


async def test_missing_file_is_skipped_with_warning(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        docs = await load_documents([tmp_path / "nope.txt", Document("inline", "mem")])
    assert [d.source for d in docs] == ["mem"]
    assert "skipping source" in caplog.text


async def test_pdf_without_reader_is_skipped(tmp_path, caplog):
    if available("pypdf"):
        pytest.skip("pypdf installed")
    (tmp_path / "x.pdf").write_bytes(b"%PDF-1.4 synthetic")
    with caplog.at_level(logging.WARNING):
        assert await load_documents([tmp_path / "x.pdf"]) == []
    assert "pypdf" in caplog.text


async def test_urls_via_mock_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/doc.txt":
            return httpx.Response(200, text="Remote text body.")
        if request.url.path == "/data":
            return httpx.Response(200, json={"content": "json content"})
        if request.url.path == "/page":
            return httpx.Response(
                200,
                text="<html><script>x()</script><p>Hello &amp; bye</p></html>",
                headers={"content-type": "text/html"},
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        docs = await load_documents(
            [
                "https://docs.example/doc.txt",
                "https://docs.example/data",
                "https://docs.example/page",
                "https://docs.example/missing",
            ],
            http_client=client,
        )
    texts = {d.source: d.text for d in docs}
    assert texts["https://docs.example/doc.txt"] == "Remote text body."
    assert texts["https://docs.example/data"] == "json content"
    assert "Hello & bye" in texts["https://docs.example/page"]
    assert "x()" not in texts["https://docs.example/page"]
    assert "https://docs.example/missing" not in texts


class FakeStorage:
    def __init__(self, data: dict[str, dict[str, Any]]):
        self.data = data

    async def upload(self, data, name):  # pragma: no cover - unused
        raise NotImplementedError

    async def download(self, uri: str) -> dict[str, Any]:
        return self.data[uri]

    async def exists(self, uri: str) -> bool:  # pragma: no cover - unused
        return uri in self.data


async def test_storage_uris():
    storage = FakeStorage({"ipfs://bafyexample": {"content": "stored doc"}})
    docs = await load_documents(["ipfs://bafyexample"], storage=storage)
    assert docs == [Document("stored doc", "ipfs://bafyexample")]
    assert await load_documents(["ipfs://bafyexample"]) == []  # no storage: skipped


# ---------------------------------------------------------------------------- generation


async def test_case_shape_and_truncation(tmp_path):
    path = tmp_path / "doc.txt"
    path.write_text("BNB Chain is an EVM-compatible chain. Blocks are produced quickly.")
    judge = FakeJudge({QA: pairs(5)})
    cases = await RagGenerator(judge).generate([path])
    assert len(cases) == 3  # questions_per_chunk
    case = cases[0]
    assert case.input == "q0?" and case.expected == "a0"
    assert len(case.contexts) == 1
    assert case.contexts[0].text.startswith("BNB Chain")
    assert case.contexts[0].source == f"{path.as_posix()}#chunk-0"
    assert case.metadata["chunk_id"] == case.contexts[0].source
    assert case.metadata["char_span"][0] == 0
    assert len({c.id for c in cases}) == 3
    again = await RagGenerator(FakeJudge({QA: pairs(5)})).generate([path])
    assert [c.id for c in again] == [c.id for c in cases]  # deterministic ids
    request = judge.requests[0]
    assert "Limit the questions to 3 questions" in request.system
    assert "<untrusted-" in request.user and "BNB Chain" in request.user


async def test_one_call_per_chunk():
    text = " ".join(f"word{i}" for i in range(100))
    judge = FakeJudge({QA: pairs(1)})
    cases = await RagGenerator(judge, chunk_size=100, overlap=20).generate([Document(text, "mem")])
    n_chunks = len(split_text(text, 100, 20))
    assert len(judge.requests) == n_chunks == len(cases)
    assert [c.metadata["chunk_index"] for c in cases] == list(range(n_chunks))


async def test_repairs_bad_json_and_accepts_bare_array():
    bare = '```json\n[{"question": "Q?", "answer": "A"}]\n```'
    judge = FakeJudge({QA: ["this is not json", bare]})
    cases = await RagGenerator(judge).generate([Document("Some text.", "mem")])
    assert [(c.input, c.expected) for c in cases] == [("Q?", "A")]
    assert len(judge.requests) == 2
    assert "previous response was invalid" in judge.requests[1].user.lower()


async def test_schema_violation_is_repaired():
    judge = FakeJudge({QA: [{"qa_pairs": [{"question": "Q?"}]}, pairs(1)]})
    cases = await RagGenerator(judge).generate([Document("Some text.", "mem")])
    assert len(cases) == 1
    assert "missing required property 'answer'" in judge.requests[1].user


async def test_failing_chunk_is_skipped_and_all_failed_raises():
    def script(request):
        return "garbage" if "bad chunk" in request.user else pairs(1)

    gen = RagGenerator(FakeJudge(script))
    cases = await gen.generate([Document("bad chunk", "a"), Document("good chunk", "b")])
    assert [c.metadata["source"] for c in cases] == ["b"]
    with pytest.raises(JudgeParseError):
        await RagGenerator(FakeJudge("garbage")).generate([Document("bad chunk", "a")])


async def test_no_documents_raises(tmp_path):
    with pytest.raises(GenerationError):
        await RagGenerator(FakeJudge(pairs(1))).generate([tmp_path / "missing.txt"])
