"""Golden tables for format.*, including ReDoS and XXE safety."""

from __future__ import annotations

import pytest

from bnbagent_eval._optional import available
from bnbagent_eval.metrics.format.structured import extract_json

from ._util import case, run_metric


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ('{"a": 1}', {"a": 1}),
        ('Here you go:\n```json\n{"a": [1, 2]}\n```\nthanks', {"a": [1, 2]}),
        ("result: [1, 2] done", [1, 2]),
    ],
)
def test_extract_json(text, value) -> None:
    assert extract_json(text) == value


async def test_json_valid() -> None:
    assert (await run_metric("format.json_valid", case('{"ok": true}'))).score == 1.0
    assert (await run_metric("format.json_valid", case({"already": "parsed"}))).score == 1.0
    assert (await run_metric("format.json_valid", case("```json\n{}\n```"))).score == 1.0
    strict = await run_metric("format.json_valid", case("x {}"), {"extract_fenced": False})
    assert strict.score == 0.0


async def test_json_schema() -> None:
    schema = {
        "type": "object",
        "required": ["price"],
        "properties": {"price": {"type": "number", "minimum": 0}},
    }
    assert (
        await run_metric("format.json_schema", case('{"price": 3}'), {"schema": schema})
    ).score == 1
    bad = await run_metric("format.json_schema", case('{"price": -1}'), {"schema": schema})
    assert bad.score == 0.0 and bad.details["errors"] == 1
    nested = {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {"type": "integer"}}},
    }
    assert (
        await run_metric("format.json_schema", case('{"items": [1, "x"]}'), {"schema": nested})
    ).score == 0.0


async def test_regex() -> None:
    assert (
        await run_metric("format.regex", case("ABC-123"), {"pattern": r"[A-Z]+-\d+"})
    ).score == 1
    assert (
        await run_metric("format.regex", case("id ABC-123 ok"), {"pattern": r"[A-Z]+-\d+"})
    ).score == 0.0
    assert (
        await run_metric(
            "format.regex", case("id ABC-123 ok"), {"pattern": r"[A-Z]+-\d+", "full_match": False}
        )
    ).score == 1.0
    assert (
        await run_metric("format.regex", case("abc"), {"pattern": "ABC", "flags": ["IGNORECASE"]})
    ).score == 1.0


@pytest.mark.skipif(not available("regex"), reason="needs the regex module ([format]/[server])")
async def test_regex_timeout_is_passed_and_reported(monkeypatch) -> None:
    import regex

    seen = {}

    class Slow:
        def fullmatch(self, text, timeout=None):
            seen["timeout"] = timeout
            raise TimeoutError("regex timed out")

    monkeypatch.setattr(regex, "compile", lambda pattern, flags=0: Slow())
    r = await run_metric(
        "format.regex", case("a" * 40 + "!"), {"pattern": r"(a+)+$", "timeout_ms": 50}
    )
    assert seen["timeout"] == 0.05
    assert r.status == "error" and "timed out" in r.error.message


async def test_xml_well_formed_and_xxe_rejected() -> None:
    assert (await run_metric("format.xml", case("<order><id>1</id></order>"))).score == 1.0
    assert (await run_metric("format.xml", case("<order><id>1</order>"))).score == 0.0
    xxe = '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>'
    r = await run_metric("format.xml", case(xxe))
    assert r.score == 0.0 and "DOCTYPE" in r.reason
    laughs = '<!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><l>&b;</l>'
    assert (await run_metric("format.xml", case(laughs))).score == 0.0


@pytest.mark.skipif(not available("lxml"), reason="needs lxml ([format])")
async def test_xml_xsd_validation() -> None:
    xsd = (
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
        '<xs:element name="order"><xs:complexType><xs:sequence>'
        '<xs:element name="id" type="xs:integer"/></xs:sequence></xs:complexType>'
        "</xs:element></xs:schema>"
    )
    ok = await run_metric("format.xml", case("<order><id>7</id></order>"), {"xsd": xsd})
    bad = await run_metric("format.xml", case("<order><id>x</id></order>"), {"xsd": xsd})
    assert ok.score == 1.0 and bad.score == 0.0 and bad.reason
    evil_xsd = '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>' + xsd
    err = await run_metric("format.xml", case("<order><id>7</id></order>"), {"xsd": evil_xsd})
    assert err.status == "error"


@pytest.mark.skipif(not available("jsonschema"), reason="needs jsonschema ([format])")
async def test_json_schema_uses_library_when_installed() -> None:
    r = await run_metric(
        "format.json_schema",
        case('{"a": "x"}'),
        {"schema": {"properties": {"a": {"type": "integer"}}}},
    )
    assert r.score == 0.0 and r.details["backend"] == "jsonschema"
    remote = {"$ref": "https://example.com/schema.json"}
    res = await run_metric("format.json_schema", case("{}"), {"schema": remote})
    assert res.status == "error"  # remote $ref is never fetched (and network is blocked)


async def test_builtin_backends_when_extras_missing(monkeypatch) -> None:
    import bnbagent_eval.metrics.format.structured as structured

    monkeypatch.setattr(structured, "available", lambda module: False)
    schema = {"type": "object", "required": ["a"]}
    js = await run_metric("format.json_schema", case('{"b": 1}'), {"schema": schema})
    assert js.score == 0.0 and js.details["backend"] == "builtin"
    rx = await run_metric("format.regex", case("abc"), {"pattern": "a.c"})
    assert rx.score == 1.0 and rx.details["backend"] == "re"
    rx_search = await run_metric(
        "format.regex", case("xx abc"), {"pattern": "a.c", "full_match": False}
    )
    assert rx_search.score == 1.0


async def test_invalid_json_paths() -> None:
    bad = await run_metric("format.json_schema", case("not json"), {"schema": {}})
    assert bad.score == 0.0 and "invalid JSON" in bad.reason
    fenced_bad = await run_metric("format.json_valid", case("```json\n{oops}\n```"))
    assert fenced_bad.score == 0.0
    with pytest.raises(ValueError):
        extract_json("{oops", fenced=False)
