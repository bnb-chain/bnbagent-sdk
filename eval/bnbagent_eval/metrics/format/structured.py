"""JSON validity, JSON Schema, regex and XML checks (rule methods; ``native`` LLM mode).

Security: regexes use the ``regex`` module with a timeout when installed; XML
with a DOCTYPE/ENTITY declaration is rejected before parsing (no XXE / entity expansion), and XSD
validation uses lxml with entity resolution and network access disabled.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..._jsonschema import validate as mini_validate
from ..._optional import available
from ...exceptions import SuiteError
from ...judges.prompts.format.structure import schema_description
from ...judges.structured import extract_json
from ...types.case import EvalCase
from ...types.result import ErrorInfo
from ..base import BaseMetric, MetricContext, MetricSpec, Outcome
from ..judge_base import JUDGE_PARAMS, native_method
from ..registry import register
from ..text._common import output_text, rule_method
from .structure import StructureJudge

# ``native`` = AgentSight OutputStructureCompliance LLM mode for the same check
# (``prioritize_efficiency=False`` with ``json_schema`` / ``regex_pattern`` / ``xml_schema``).
_NATIVE = native_method("output", optional=("conversation",))

__all__ = ["extract_json"]  # re-exported (format.* and ops.consistency parse with it)


def _refuse(uri: str) -> Any:
    raise ValueError(f"remote $ref not allowed: {uri}")


def _local_validator(schema: Any) -> tuple[Any, tuple[type[BaseException], ...]]:
    """A jsonschema validator that resolves only ``$ref``s inside ``schema`` (no file / network
    retrieval: jsonschema's default would read ``file://`` and fetch ``http(s)://`` documents),
    and the exception types an unresolvable ``$ref`` raises."""
    import jsonschema

    cls = jsonschema.validators.validator_for(schema)
    try:
        from referencing import Registry
        from referencing.exceptions import Unresolvable
    except ImportError:  # jsonschema < 4.18: RefResolver with every URL scheme refused
        handlers = {k: _refuse for k in ("", "file", "http", "https", "ftp", "data", "urn")}
        resolver = jsonschema.RefResolver.from_schema(schema, handlers=handlers)
        return cls(schema, resolver=resolver), (jsonschema.RefResolutionError, ValueError)
    return cls(schema, registry=Registry()), (Unresolvable,)


def _output_json(case: EvalCase, fenced: bool) -> Any:
    if isinstance(case.output, (dict, list)) or (
        case.output is not None and not isinstance(case.output, (str, tuple))
    ):
        return case.output
    # No scanning: "See [1] for details" or '{"a": 1} trailing' is not valid JSON output
    # (AgentSight: json.loads of the whole text); a fenced block is our documented extension.
    return extract_json(output_text(case), fenced, scan=False)


@register
class JsonValid(BaseMetric):
    spec = MetricSpec(
        id="format.json_valid",
        category="format",
        description="The output is valid JSON (or a fenced block of it).",
        methods=(rule_method("output"),),
        params_schema={
            "type": "object",
            "properties": {"extract_fenced": {"type": "boolean"}},
            "additionalProperties": False,
        },
        default_threshold=1.0,
        sources=("agentsight:OutputStructureCompliance[json]",),
    )

    def _evaluate_rule(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        try:
            _output_json(case, ctx.params.get("extract_fenced", True))
        except ValueError as exc:
            return Outcome(score=0.0, reason=f"invalid JSON: {exc}")
        return Outcome(score=1.0)


@register
class JsonSchema(StructureJudge):
    validation_type = "json"
    spec = MetricSpec(
        id="format.json_schema",
        category="format",
        description="The output's JSON validates against a JSON Schema (local $ref only).",
        methods=(rule_method("output"), _NATIVE),
        params_schema={
            "type": "object",
            "required": ["schema"],
            "properties": {
                **JUDGE_PARAMS,
                "schema": {"type": "object"},
                "extract_fenced": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        default_threshold=1.0,
        aliases=("deepeval.json_correctness",),
        version="2",  # v2: native threshold 0.5 (AgentSight); rule resolves local $refs only
        sources=("deepeval:json_correctness", "agentsight:OutputStructureCompliance[json_schema]"),
    )

    def structure_spec(self, params: Any) -> str:
        return schema_description("json", json.dumps(params["schema"], indent=2))

    def _evaluate_rule(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        try:
            value = _output_json(case, ctx.params.get("extract_fenced", True))
        except ValueError as exc:
            return Outcome(score=0.0, reason=f"invalid JSON: {exc}")
        schema = ctx.params["schema"]
        if available("jsonschema"):
            validator, unresolvable = _local_validator(schema)
            try:
                errors = [
                    f"{'/'.join(map(str, e.absolute_path)) or '$'}: {e.message}"
                    for e in validator.iter_errors(value)
                ]
            except unresolvable as exc:
                raise SuiteError(f"schema $ref not resolvable (local refs only): {exc}") from exc
            backend = "jsonschema"
        else:
            errors = mini_validate(value, schema)
            backend = "builtin"
        return Outcome(
            score=0.0 if errors else 1.0,
            reason="; ".join(errors[:5]) or None,
            details={"errors": len(errors), "backend": backend},
        )


_FLAGS = {"IGNORECASE": re.IGNORECASE, "MULTILINE": re.MULTILINE, "DOTALL": re.DOTALL}


@register
class Regex(StructureJudge):
    validation_type = "regex"
    spec = MetricSpec(
        id="format.regex",
        category="format",
        description="The output matches a regular expression (full match by default).",
        methods=(rule_method("output"), _NATIVE),
        params_schema={
            "type": "object",
            "required": ["pattern"],
            "properties": {
                **JUDGE_PARAMS,
                "pattern": {"type": "string"},
                "full_match": {"type": "boolean"},
                "flags": {"type": "array", "items": {"enum": sorted(_FLAGS)}},
                "timeout_ms": {"type": "integer", "minimum": 1, "maximum": 10000},
            },
            "additionalProperties": False,
        },
        default_threshold=1.0,
        aliases=("deepeval.pattern_match",),
        version="2",  # v2: native threshold 0.5 (AgentSight)
        sources=(
            "deepeval:pattern_match",
            "agentsight:OutputStructureCompliance[regex]",
            "etk:ExampleTextQualityScorer.prefix",
        ),
    )

    def structure_spec(self, params: Any) -> str:
        return schema_description("regex", params["pattern"])

    def _evaluate_rule(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        p = ctx.params
        flags = 0
        for name in p.get("flags", []):
            flags |= _FLAGS[name]
        text = output_text(case)
        full = p.get("full_match", True)
        if available("regex"):
            import regex

            compiled = regex.compile(p["pattern"], flags)
            fn = compiled.fullmatch if full else compiled.search
            try:
                matched = fn(text, timeout=p.get("timeout_ms", 1000) / 1000) is not None
            except TimeoutError:
                return Outcome(
                    None,
                    status="error",
                    reason="regex timed out",
                    error=ErrorInfo("metric_error", "regex timed out"),
                )
            backend = "regex"
        else:
            compiled = re.compile(p["pattern"], flags)
            matched = (compiled.fullmatch(text) if full else compiled.search(text)) is not None
            backend = "re"
        return Outcome(score=float(matched), details={"backend": backend})


_DTD = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
# xs:include / xs:import / xs:redefine / xs:override load other documents (local files too).
_SCHEMA_LOCATION = re.compile(r"schemaLocation", re.IGNORECASE)


@register
class Xml(StructureJudge):
    validation_type = "xml"
    spec = MetricSpec(
        id="format.xml",
        category="format",
        description="Output is well-formed XML; validated against an XSD when given ([format]).",
        methods=(rule_method("output"), _NATIVE),
        params_schema={
            "type": "object",
            "properties": {**JUDGE_PARAMS, "xsd": {"type": "string"}},
            "additionalProperties": False,
        },
        default_threshold=1.0,
        version="2",  # v2: native threshold 0.5 (AgentSight)
        sources=(
            "agentsight:OutputStructureCompliance[xml]",
            "agentsight:OutputStructureCompliance[xml_schema]",
        ),
    )

    def structure_spec(self, params: Any) -> str:
        if not params.get("xsd"):  # AgentSight's XML LLM mode needs ``xml_schema``
            raise SuiteError("format.xml method native requires the xsd param")
        return schema_description("xml", params["xsd"])

    def _evaluate_rule(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        text = output_text(case).strip()
        if _DTD.search(text):
            return Outcome(score=0.0, reason="DOCTYPE/ENTITY declarations are not allowed")
        xsd = ctx.params.get("xsd")
        if xsd is None:
            import xml.etree.ElementTree as ET

            try:
                ET.fromstring(text)
            except ET.ParseError as exc:
                return Outcome(score=0.0, reason=f"not well-formed: {exc}")
            return Outcome(score=1.0)
        if _DTD.search(xsd):
            raise ValueError("DOCTYPE/ENTITY declarations are not allowed in the XSD")
        if _SCHEMA_LOCATION.search(xsd):
            raise ValueError("schemaLocation (include / import of other documents) is not allowed")
        from ..._optional import require

        etree = require("lxml.etree", "format")
        parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
        schema = etree.XMLSchema(etree.fromstring(xsd.encode(), parser))
        try:
            doc = etree.fromstring(text.encode(), parser)
        except etree.XMLSyntaxError as exc:
            return Outcome(score=0.0, reason=f"not well-formed: {exc}")
        ok = schema.validate(doc)
        reason = None if ok else "; ".join(str(e) for e in list(schema.error_log)[:5])
        return Outcome(score=float(ok), reason=reason)
