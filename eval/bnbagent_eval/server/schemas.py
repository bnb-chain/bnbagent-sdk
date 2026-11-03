"""JSON Schema for the data contracts, generated from the dataclass type hints.

``contract_schemas()`` gives one JSON Schema (2020-12) document per contract; the
dict-shaped chain contracts (``RegistrationMetricsV1``, ``EvalEvidenceV1``) are described by
hand next to the generated dataclass definitions they reference. ``component_schemas()`` is
the same set flattened for OpenAPI ``components.schemas``. ``write_schemas`` writes or checks
``eval/schemas/``. No dependency on FastAPI (the OpenAPI document is built by ``server.app``).
"""

from __future__ import annotations

import dataclasses
import enum
import json
import types
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin

from ..types._serde import EXTENSIONS, _hints, to_dict
from ..types.request import Attestation, EvalRequest, EvalResponse
from ..types.result import Verdict
from ..types.subject import AgentSubject, JobSubject
from ..types.suite import Suite

__all__ = [
    "CONTRACTS",
    "component_schemas",
    "contract_schemas",
    "dataclass_schema",
    "write_schemas",
]

DRAFT = "https://json-schema.org/draft/2020-12/schema"
ID_PREFIX = "urn:bnbagent-eval:schema:v1:"
CONTRACTS = (
    "EvalRequest",
    "EvalResponse",
    "Suite",
    "RegistrationMetricsV1",
    "EvalEvidenceV1",
    "Attestation",
)
_DATACLASS_CONTRACTS = {
    "EvalRequest": EvalRequest,
    "EvalResponse": EvalResponse,
    "Suite": Suite,
    "Attestation": Attestation,
}


class _Gen:
    def __init__(self, prefix: str):
        self.prefix = prefix
        self.defs: dict[str, Any] = {}

    def ref(self, name: str) -> dict[str, Any]:
        return {"$ref": f"{self.prefix}{name}"}

    def dataclass(self, cls: type) -> dict[str, Any]:
        name = cls.__name__
        if name not in self.defs:
            self.defs[name] = {}  # placeholder: recursion guard
            hints = _hints(cls)
            props: dict[str, Any] = {}
            required: list[str] = []
            open_ = False
            for f in dataclasses.fields(cls):
                if not f.init:
                    continue
                if f.name == EXTENSIONS:
                    open_ = True
                    continue
                schema = self.type(hints[f.name])
                default = _default(f)
                if default is not dataclasses.MISSING:
                    schema = {**schema, "default": default}
                else:
                    required.append(f.name)
                props[f.name] = schema
            body: dict[str, Any] = {"type": "object", "title": name, "properties": props}
            if required:
                body["required"] = required
            body["additionalProperties"] = open_
            doc = (cls.__doc__ or "").strip()
            if doc and not doc.startswith(f"{name}("):
                body["description"] = doc.splitlines()[0]
            self.defs[name] = body
        return self.ref(name)

    def type(self, tp: Any) -> dict[str, Any]:
        if tp is Any or tp is object:
            return {}
        if tp is type(None):
            return {"type": "null"}
        if get_origin(tp) is Union or isinstance(tp, types.UnionType):
            args = get_args(tp)
            options = [self.type(a) for a in args if a is not type(None)]
            if type(None) in args:
                options.append({"type": "null"})
            return {"anyOf": options}
        origin = get_origin(tp)
        if origin is Literal:
            values = list(get_args(tp))
            return {"const": values[0]} if len(values) == 1 else {"enum": values}
        if dataclasses.is_dataclass(tp):
            return self.dataclass(tp)
        if isinstance(tp, type) and issubclass(tp, enum.Enum):
            return {"enum": [m.value for m in tp]}
        if origin in (tuple, list, Sequence) or tp in (tuple, list):
            args = get_args(tp)
            if origin is tuple and args and (len(args) != 2 or args[1] is not Ellipsis):
                items = [self.type(a) for a in args]
                return {
                    "type": "array",
                    "prefixItems": items,
                    "minItems": len(items),
                    "maxItems": len(items),
                }
            item = self.type(args[0]) if args else {}
            return {"type": "array", "items": item} if item else {"type": "array"}
        if origin in (dict, Mapping) or tp in (dict, Mapping):
            args = get_args(tp)
            val = self.type(args[1]) if len(args) == 2 else {}
            return {"type": "object", "additionalProperties": val} if val else {"type": "object"}
        simple = {str: "string", int: "integer", float: "number", bool: "boolean"}
        if tp in simple:
            return {"type": simple[tp]}
        raise TypeError(f"no JSON Schema mapping for {tp!r}")


def _default(f: dataclasses.Field) -> Any:
    if f.default is not dataclasses.MISSING:
        value = f.default
    elif f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
        value = f.default_factory()  # type: ignore[misc]
    else:
        return dataclasses.MISSING
    try:
        out = to_dict(value)
        json.dumps(out)
    except (TypeError, ValueError):
        return dataclasses.MISSING
    return out


def dataclass_schema(cls: type, *, prefix: str = "#/$defs/") -> tuple[dict, dict[str, Any]]:
    """``(root schema, definitions)`` for dataclass ``cls``."""
    gen = _Gen(prefix)
    root = gen.dataclass(cls)
    return root, gen.defs


def _hand_written(gen: _Gen) -> dict[str, dict[str, Any]]:
    """Chain contracts that are plain dicts (``chain.erc8004`` / ``chain.evidence``)."""
    att, verdict = gen.dataclass(Attestation), gen.dataclass(Verdict)
    subject = {"anyOf": [gen.dataclass(JobSubject), gen.dataclass(AgentSubject), {"type": "null"}]}
    pointer = {
        "anyOf": [
            {"type": "null"},
            {
                "type": "object",
                "required": ["uri", "hash"],
                "properties": {"uri": {"type": "string"}, "hash": {"type": ["string", "null"]}},
            },
        ]
    }
    num_or_null = {"type": ["number", "null"]}
    registration = {
        "type": "object",
        "title": "RegistrationMetricsV1",
        "description": "One entry of metrics[] in an ERC-8004 registration file.",
        "required": ["schema", "suite_hash", "result_hash", "metrics", "verdict"],
        "properties": {
            "schema": {"const": "bnbagent-eval/registration-metrics/v1"},
            "suite_hash": {"type": "string"},
            "result_hash": {"type": "string"},
            "metrics": {"type": "object", "additionalProperties": {"type": "number"}},
            "verdict": {"enum": ["pass", "fail", "inconclusive", "pending_human"]},
            "cases": {"type": "integer"},
            "agent_version": {"type": ["string", "null"]},
            "benchmark": {"type": ["object", "null"]},
            "report": pointer,
            "evaluated_at": {"type": "integer"},
            "expires_at": {"type": ["integer", "null"]},
            "evaluator": {"type": ["string", "null"]},
            "attestation": {"anyOf": [att, {"type": "null"}]},
        },
        "additionalProperties": True,
    }
    evidence = {
        "type": "object",
        "title": "EvalEvidenceV1",
        "description": "Evaluation evidence in DeliverableManifest.metadata['eval'] (ERC-8183).",
        "required": ["schema", "suite_hash", "result_hash", "verdict"],
        "properties": {
            "schema": {"const": "bnbagent-eval/evidence/v1"},
            "subject": subject,
            "stage": {"enum": ["dev", "registration", "pre_submit", "review", "vote"]},
            "suite_hash": {"type": "string"},
            "result_hash": {"type": "string"},
            "response_hash": {"type": "string"},
            "aggregate": {
                "type": "object",
                "properties": {
                    "score": num_or_null,
                    "pass_rate": num_or_null,
                    "error_rate": {"type": "number"},
                    "cases": {"type": "integer"},
                },
            },
            "verdict": verdict,
            "voter_summary": {"type": "object"},
            "report": pointer,
            "attestation": {"anyOf": [att, {"type": "null"}]},
            "cases": {"type": "array", "items": {"type": "object"}},
        },
        "additionalProperties": True,
    }
    return {"RegistrationMetricsV1": registration, "EvalEvidenceV1": evidence}


def contract_schemas() -> dict[str, dict[str, Any]]:
    """One standalone JSON Schema document per contract (``$defs`` inlined)."""
    out: dict[str, dict[str, Any]] = {}
    for name in CONTRACTS:
        gen = _Gen("#/$defs/")
        if name in _DATACLASS_CONTRACTS:
            gen.dataclass(_DATACLASS_CONTRACTS[name])
            root = dict(gen.defs.pop(name))
        else:
            root = _hand_written(gen)[name]
        out[name] = {
            "$schema": DRAFT,
            "$id": f"{ID_PREFIX}{name}",
            **root,
            "$defs": dict(sorted(gen.defs.items())),
        }
    return out


def component_schemas() -> dict[str, Any]:
    """Every contract and definition, with refs into ``#/components/schemas/`` (OpenAPI)."""
    gen = _Gen("#/components/schemas/")
    for cls in _DATACLASS_CONTRACTS.values():
        gen.dataclass(cls)
    defs = dict(gen.defs)
    defs.update(_hand_written(gen))
    defs.update(gen.defs)
    ref = gen.ref
    defs["ErrorBody"] = {
        "type": "object",
        "required": ["error"],
        "properties": {
            "error": {"type": "string"},
            "message": {"type": "string"},
            "credit": {"type": "object"},
            "accepts": {"type": "array"},
            "quote": {"type": "object"},
        },
        "additionalProperties": True,
    }
    method = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "kind": {"enum": ["rule", "judge", "model", "vendor"]},
            "required_inputs": {"type": "array", "items": {"type": "string"}},
            "optional_inputs": {"type": "array", "items": {"type": "string"}},
            "inverted": {"type": "boolean"},
            "raw_range": {"type": "array", "items": {"type": "number"}},
            "extra": {"type": ["string", "null"]},
            "service_allowed": {"type": "boolean"},
        },
    }
    defs["MetricSpec"] = {
        "type": "object",
        "required": ["id", "category", "methods"],
        "properties": {
            "id": {"type": "string"},
            "category": {"type": "string"},
            "description": {"type": "string"},
            "version": {"type": "string"},
            "default_threshold": {"type": ["number", "null"]},
            "unit": {"type": ["string", "null"]},
            "value_limits": {"type": "boolean"},
            "aliases": {"type": "array", "items": {"type": "string"}},
            "sources": {"type": "array", "items": {"type": "string"}},
            "params_schema": {"type": "object"},
            "methods": {"type": "array", "items": method},
        },
    }
    defs["MetricCatalogue"] = {
        "type": "object",
        "required": ["metrics"],
        "properties": {"metrics": {"type": "array", "items": ref("MetricSpec")}},
    }
    defs["QuoteResponse"] = {
        "type": "object",
        "required": ["request_hash", "judge_calls", "payment_required", "quote"],
        "properties": {
            "request_hash": {"type": "string"},
            "judge_calls": {"type": "integer"},
            "payment_required": {"type": "boolean"},
            "quote": {"type": ["object", "null"]},
        },
    }
    defs["JobCreated"] = {
        "type": "object",
        "required": ["job_id", "access_token", "status"],
        "properties": {
            "job_id": {"type": "string"},
            "access_token": {"type": "string"},
            "status": {"type": "string"},
            "expires_at": {"type": "integer"},
        },
    }
    defs["JobStatus"] = {
        "type": "object",
        "required": ["job_id", "status"],
        "properties": {
            "job_id": {"type": "string"},
            "status": {"enum": ["queued", "running", "done", "failed"]},
            "request_hash": {"type": "string"},
            "result": {"anyOf": [ref("EvalResponse"), {"type": "object"}, {"type": "null"}]},
            "error": {"type": ["string", "null"]},
            "credit": {"type": ["object", "null"]},
            "expires_at": {"type": "integer"},
        },
    }
    defs["CreditRedeem"] = {
        "type": "object",
        "required": ["credit_id", "request"],
        "properties": {"credit_id": {"type": "string"}, "request": ref("EvalRequest")},
    }
    return dict(sorted(defs.items()))


def _dump(doc: Any) -> str:
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def all_documents(openapi: Mapping[str, Any] | None = None) -> dict[str, str]:
    """File name → text for ``eval/schemas/``."""
    files = {f"{name}.schema.json": _dump(doc) for name, doc in contract_schemas().items()}
    if openapi is not None:
        files["openapi.json"] = _dump(openapi)
    return files


def write_schemas(
    directory: str | Path, *, openapi: Mapping[str, Any] | None = None, check: bool = False
) -> list[str]:
    """Write the schema files (or, with ``check``, return the names that are stale)."""
    out = Path(directory)
    stale = []
    for name, text in all_documents(openapi).items():
        path = out / name
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == text:
            continue
        stale.append(name)
        if not check:
            out.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    return stale
