"""Edge cases of text.*, format.* and ops.* rule metrics (coverage gate)."""

from __future__ import annotations

import pytest

from bnbagent_eval.metrics.text._common import as_text
from bnbagent_eval.metrics.text.overlap import _lcs, jaccard, meteor, rouge_prf
from bnbagent_eval.net import FetchError, decode_data_uri
from bnbagent_eval.types import ContentPart

from ._util import case, run_metric


def test_overlap_degenerate_inputs() -> None:
    assert _lcs([], ["a"]) == 0
    assert meteor(["a"], []) == 0.0
    assert jaccard("", "a b") == 0.0 and jaccard("", "") == 1.0


def test_rouge_tokenizes_cjk_by_character_and_word() -> None:
    *_, f = rouge_prf("东京是首都", "东京是首都", "rougeL", use_stemmer=False)
    assert f == pytest.approx(1.0)


async def test_rouge_cjk_tokenizes_characters() -> None:
    res = await run_metric("text.rouge", case("东京是日本的首都", "东京是首都"))
    assert "backend" not in res.details and 0 < res.score < 1  # one implementation only


def test_as_text_of_content_parts() -> None:
    assert as_text((ContentPart(type="text", text="hi"),)) == "hi"


async def test_similarity_against_expected_without_expected() -> None:
    res = await run_metric("text.embedding_similarity", case("a"), {"against": "expected"})
    assert res.error is not None and "expected" in res.error.message


async def test_consistency_needs_two_runs_and_accepts_parsed_json() -> None:
    one = await run_metric(
        "ops.consistency", case("x"), method="exact", runs=(case("x"), case(None))
    )
    assert one.error is not None and "two runs" in one.error.message
    runs = (case({"a": 1}), case({"a": 1}))
    ted = await run_metric("ops.consistency", runs[0], method="ted", runs=runs)
    assert ted.score == 1.0


async def test_cost_without_token_usage_is_unscored() -> None:
    res = await run_metric("ops.cost", case("x"), {"price_table": {"*": {"input_per_1k": 0.001}}})
    assert res.score is None


def test_malformed_data_uri() -> None:
    with pytest.raises(FetchError):
        decode_data_uri("data:text/plain")


async def test_structure_requires_expected_structure() -> None:
    res = await run_metric("format.structure", case("x"))
    assert res.error is not None and "expected_structure" in res.error.message


async def test_xml_schema_with_malformed_document() -> None:
    pytest.importorskip("lxml")
    xsd = (
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
        '<xs:element name="a" type="xs:string"/></xs:schema>'
    )
    res = await run_metric("format.xml", case("<a>unclosed"), {"xsd": xsd})
    assert res.score == 0.0 and "well-formed" in res.reason
